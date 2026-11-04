"""Screen capture backends and the frame encoder (dirty-rectangle JPEG)."""
from __future__ import annotations

import io
import math
import threading
import time
from typing import Optional

from PIL import Image, ImageChops, ImageDraw


class CaptureBackend:
    """Abstract screen grabber. Index 0 = all monitors, 1..n = single monitor."""

    name = "base"

    def monitors(self) -> list[dict]:
        raise NotImplementedError

    def grab(self, index: int) -> Image.Image:
        raise NotImplementedError

    def grab_raw(self, index: int):
        """Returns (ndarray, pixel_format) for video encoding. Default: via PIL image."""
        import numpy as np

        img = self.grab(index)
        return np.asarray(img), "rgb24"

    def monitor_rect(self, index: int) -> dict:
        mons = self.monitors()
        for m in mons:
            if m["index"] == index:
                return m
        return mons[0]


class MSSBackend(CaptureBackend):
    """Real screen capture via mss (Windows / macOS / Linux-X11)."""

    name = "mss"

    def __init__(self):
        import mss  # noqa: F401  (import check)

        self._mss = mss
        self._local = threading.local()
        self._sct()  # fail early if capture is impossible

    def _sct(self):
        # mss instances are not thread safe -> one per thread
        sct = getattr(self._local, "sct", None)
        if sct is None:
            sct = self._mss.mss()
            self._local.sct = sct
        return sct

    def monitors(self) -> list[dict]:
        mons = self._sct().monitors
        out = []
        if len(mons) > 2:
            m = mons[0]
            out.append({"index": 0, "name": "全部螢幕", "left": m["left"], "top": m["top"],
                        "width": m["width"], "height": m["height"]})
        for i, m in enumerate(mons[1:], start=1):
            out.append({"index": i, "name": f"螢幕 {i} ({m['width']}x{m['height']})",
                        "left": m["left"], "top": m["top"], "width": m["width"], "height": m["height"]})
        return out

    def grab(self, index: int) -> Image.Image:
        sct = self._sct()
        mons = sct.monitors
        if index < 0 or index >= len(mons):
            index = 1 if len(mons) > 1 else 0
        shot = sct.grab(mons[index])
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    def grab_raw(self, index: int):
        import numpy as np

        sct = self._sct()
        mons = sct.monitors
        if index < 0 or index >= len(mons):
            index = 1 if len(mons) > 1 else 0
        shot = sct.grab(mons[index])
        arr = np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)
        return arr, "bgra"


class DemoBackend(CaptureBackend):
    """Synthetic screen used for --demo mode and automated tests (no display needed)."""

    name = "demo"

    def __init__(self, event_log: Optional[list] = None, size=(1280, 720)):
        self.size = size
        self.event_log = event_log if event_log is not None else []
        self._t0 = time.time()

    def monitors(self) -> list[dict]:
        w, h = self.size
        return [{"index": 1, "name": f"Demo 螢幕 ({w}x{h})", "left": 0, "top": 0, "width": w, "height": h}]

    def grab(self, index: int) -> Image.Image:
        w, h = self.size
        img = Image.new("RGB", (w, h), (24, 28, 38))
        d = ImageDraw.Draw(img)
        t = time.time() - self._t0
        # moving ball so that dirty-rect encoding has something to do
        cx = int(w / 2 + math.cos(t) * w * 0.35)
        cy = int(h / 2 + math.sin(t * 1.3) * h * 0.3)
        d.ellipse((cx - 30, cy - 30, cx + 30, cy + 30), fill=(90, 160, 255))
        d.text((20, 20), "LAN Remote DEMO screen  " + time.strftime("%H:%M:%S"), fill=(230, 230, 230))
        y = 60
        for ev in self.event_log[-12:]:
            d.text((20, y), ev, fill=(180, 220, 180))
            y += 16
        return img


class FrameEncoder:
    """Turns successive screenshots into JPEG frames, sending only changed regions."""

    def __init__(self, keyframe_interval: float = 4.0):
        self.prev: Optional[Image.Image] = None
        self.last_full = 0.0
        self.keyframe_interval = keyframe_interval

    def reset(self):
        self.prev = None

    def encode(self, img: Image.Image, quality: int = 60, scale: float = 1.0, force_full: bool = False):
        if scale < 0.999:
            img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.BILINEAR)
        now = time.time()
        w, h = img.size
        full = (force_full or self.prev is None or self.prev.size != img.size
                or now - self.last_full > self.keyframe_interval)
        if full:
            box = (0, 0, w, h)
        else:
            box = ImageChops.difference(self.prev, img).getbbox()
            if box is None:
                self.prev = img
                return None
            # align to 8px JPEG blocks and pad a little to hide seams
            x0 = max(0, (box[0] - 4) // 8 * 8)
            y0 = max(0, (box[1] - 4) // 8 * 8)
            x1 = min(w, -(-(box[2] + 4) // 8) * 8)
            y1 = min(h, -(-(box[3] + 4) // 8) * 8)
            box = (x0, y0, x1, y1)
            if (x1 - x0) * (y1 - y0) > 0.6 * w * h:
                full = True
                box = (0, 0, w, h)
        if full:
            self.last_full = now
        self.prev = img
        region = img if full else img.crop(box)
        buf = io.BytesIO()
        region.save(buf, "JPEG", quality=int(quality), subsampling=2 if quality < 80 else 0)
        header = {"t": "frame", "x": box[0], "y": box[1], "w": box[2] - box[0], "h": box[3] - box[1],
                  "full": full, "fw": w, "fh": h}
        return header, buf.getvalue()


def make_backend(demo: bool = False, event_log: Optional[list] = None) -> CaptureBackend:
    if demo:
        return DemoBackend(event_log)
    try:
        return MSSBackend()
    except Exception as e:  # pragma: no cover - depends on host
        print(f"[capture] 無法初始化螢幕擷取 ({e})，改用 demo 畫面")
        return DemoBackend(event_log)
