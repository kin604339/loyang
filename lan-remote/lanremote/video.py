"""H.264 encoding (PyAV / FFmpeg) with hardware-encoder detection. Optional: falls back to JPEG if unavailable."""
from __future__ import annotations

import logging
import sys
from fractions import Fraction
from typing import Optional

log = logging.getLogger("lanremote.video")

try:
    import av
    import numpy as np

    AVAILABLE = True
except Exception:  # pragma: no cover
    av = None
    np = None
    AVAILABLE = False


def _candidates() -> list[str]:
    if sys.platform == "darwin":
        return ["h264_videotoolbox", "libx264"]
    if sys.platform.startswith("win"):
        return ["h264_nvenc", "h264_qsv", "h264_amf", "libx264"]
    return ["h264_nvenc", "libx264"]


_FORCED: Optional[str] = None
_WORKING: Optional[str] = None


def set_preferred(name: Optional[str]):
    global _FORCED
    _FORCED = name or None


def _crf(quality: int) -> int:
    # quality 10..95 -> crf 34..16
    return int(round(34 - (max(10, min(95, quality)) - 10) * 18 / 85))


def _bitrate(w: int, h: int, fps: int, quality: int) -> int:
    bpp = 0.06 + (max(10, min(95, quality)) - 10) / 85 * 0.34  # bits per pixel per frame
    return int(w * h * fps * bpp)


class H264Encoder:
    """Encodes raw frames (ndarray HxWx4 'bgra' or HxWx3 'rgb24') into Annex-B H.264 packets."""

    def __init__(self, width: int, height: int, fps: int, quality: int, src_format: str = "bgra",
                 out_width: Optional[int] = None, out_height: Optional[int] = None):
        if not AVAILABLE:
            raise RuntimeError("PyAV not available")
        self.src_w, self.src_h, self.src_format = width, height, src_format
        self.width = (out_width or width) // 2 * 2
        self.height = (out_height or height) // 2 * 2
        self.fps = max(1, fps)
        self.quality = quality
        self.pts = 0
        self.name = ""
        self.ctx = None
        self._open()

    def _open(self):
        global _WORKING
        names = [_FORCED] if _FORCED else ([_WORKING] if _WORKING else []) + [c for c in _candidates() if c != _WORKING]
        last_err = None
        for name in names:
            try:
                ctx = av.CodecContext.create(name, "w")
                ctx.width, ctx.height = self.width, self.height
                ctx.pix_fmt = "yuv420p"
                ctx.framerate = Fraction(self.fps, 1)
                ctx.time_base = Fraction(1, self.fps)
                ctx.gop_size = self.fps * 10
                ctx.max_b_frames = 0
                br = _bitrate(self.width, self.height, self.fps, self.quality)
                if name == "libx264":
                    ctx.options = {"preset": "ultrafast", "tune": "zerolatency", "crf": str(_crf(self.quality)),
                                   "x264-params": "keyint=300:min-keyint=30:scenecut=0:bframes=0:repeat-headers=1:"
                                                  "sliced-threads=1:rc-lookahead=0:sync-lookahead=0:forced-idr=1"}
                    ctx.thread_type = "SLICE"
                elif name == "h264_videotoolbox":
                    ctx.bit_rate = br
                    ctx.options = {"realtime": "1", "allow_sw": "1", "prio_speed": "1"}
                elif name == "h264_nvenc":
                    ctx.bit_rate = br
                    ctx.options = {"preset": "p1", "tune": "ull", "zerolatency": "1", "rc": "vbr", "delay": "0",
                                   "forced-idr": "1"}
                elif name == "h264_qsv":
                    ctx.bit_rate = br
                    ctx.options = {"preset": "veryfast", "look_ahead": "0", "async_depth": "1"}
                elif name == "h264_amf":
                    ctx.bit_rate = br
                    ctx.options = {"usage": "ultralowlatency", "quality": "speed", "rc": "vbr_latency"}
                ctx.open()
                # smoke test: hardware encoders often "exist" but fail at the first frame
                test = av.VideoFrame(self.width, self.height, "yuv420p")
                test.pts = 0
                ctx.encode(test)
                self.ctx = ctx
                self.name = name
                self.pts = 1
                if _WORKING != name:
                    log.info("H.264 encoder: %s (%dx%d @ %d fps)", name, self.width, self.height, self.fps)
                _WORKING = name
                return
            except Exception as e:  # noqa: BLE001
                last_err = e
                log.debug("encoder %s unavailable: %s", name, e)
        raise RuntimeError(f"no H.264 encoder available: {last_err}")

    def encode(self, arr, force_key: bool = False) -> list[bytes]:
        frame = av.VideoFrame.from_ndarray(arr, format=self.src_format)
        if frame.width != self.width or frame.height != self.height or frame.format.name != "yuv420p":
            frame = frame.reformat(width=self.width, height=self.height, format="yuv420p",
                                   interpolation="FAST_BILINEAR" if (frame.width != self.width) else None)
        frame.pts = self.pts
        self.pts += 1
        if force_key:
            frame.pict_type = av.video.frame.PictureType.I
        out = []
        for p in self.ctx.encode(frame):
            out.append((bytes(p), bool(p.is_keyframe)))
        return out

    def close(self):
        try:
            if self.ctx:
                self.ctx.close()
        except Exception:
            pass
        self.ctx = None


class H264Decoder:
    def __init__(self):
        if not AVAILABLE:
            raise RuntimeError("PyAV not available")
        self.ctx = av.CodecContext.create("h264", "r")
        self.ctx.thread_type = "AUTO"
        self.ready = False

    def decode(self, data: bytes, is_key: bool = False):
        """Returns the last decoded frame as an RGB ndarray (HxWx3) or None."""
        if not self.ready and not is_key:
            return None  # wait for the first keyframe
        self.ready = True
        try:
            frames = self.ctx.decode(av.Packet(data))
        except Exception as e:  # corrupted / out of order
            log.debug("decode error: %s", e)
            self.ready = False
            return None
        last = None
        for f in frames:
            last = f
        if last is None:
            return None
        return last.to_ndarray(format="rgb24")
