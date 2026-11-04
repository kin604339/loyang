"""aiohttp server: web viewer, WebSocket screen stream + input, file transfer, system APIs."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import secrets
import shutil
import ssl
import string
import struct
import sys
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from aiohttp import web

from . import __version__, capture, control, netutil, sysactions, video

log = logging.getLogger("lanremote")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
COOKIE = "lr_token"


@dataclass(eq=False)
class Viewer:
    ws: web.WebSocketResponse
    ip: str
    name: str
    token: str
    connected_at: float = field(default_factory=time.time)
    monitor: int = 1
    quality: int = 60
    fps: int = 15
    scale: float = 1.0
    paused: bool = False
    force_full: bool = True
    codec: str = "jpeg"
    encoder: capture.FrameEncoder = field(default_factory=capture.FrameEncoder)
    h264: Optional["video.H264Encoder"] = None
    prev_raw: Optional[object] = None
    bytes_sent: int = 0
    frames_sent: int = 0
    last_input: float = 0.0


class RemoteServer:
    def __init__(self, settings: dict, demo: bool = False):
        self.settings = settings
        self.demo = demo
        self.event_log: list[str] = []
        self.capture = capture.make_backend(demo, self.event_log)
        self.control = control.make_backend(demo, self.event_log)
        self.demo = self.capture.name == "demo" or self.control.name == "demo"
        self.device_id = settings.setdefault("id", secrets.token_hex(4))
        self.sessions: dict[str, dict] = {}
        self.viewers: set[Viewer] = set()
        self.failed_logins: dict[str, list[float]] = {}
        self.chat_log: list[dict] = []
        self.audit: list[dict] = []
        self.executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="lr-cap")
        self.approval_handler: Optional[Callable[[str, str], Awaitable[bool]]] = None
        self.on_event: Optional[Callable[[str, dict], None]] = None  # GUI hook
        self.discovery = netutil.Discovery(self.announce_info)
        self.app = self._build_app()
        self.runner: Optional[web.AppRunner] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.encoder_name = ""

    @property
    def https(self) -> bool:
        return bool(self.settings.get("https"))

    @property
    def scheme(self) -> str:
        return "https" if self.https else "http"

    # ------------------------------------------------------------------ info
    @property
    def name(self) -> str:
        return self.settings.get("name") or platform.node()

    @property
    def port(self) -> int:
        return int(self.settings.get("port", 7788))

    def announce_info(self) -> dict:
        return {"id": self.device_id, "name": self.name, "port": self.port, "os": platform.system(),
                "mac": netutil.mac_address(), "v": __version__, "https": self.https}

    def _audit(self, kind: str, **data):
        entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, **data}
        self.audit.append(entry)
        del self.audit[:-500]
        log.info("%s %s", kind, data)
        if self.on_event:
            try:
                self.on_event(kind, entry)
            except Exception:
                pass

    # ------------------------------------------------------------------ app
    def _build_app(self) -> web.Application:
        app = web.Application(client_max_size=1024 ** 4, middlewares=[self._auth_middleware])
        r = app.router
        r.add_get("/", self.index)
        r.add_get("/ws", self.ws_handler)
        r.add_post("/api/login", self.api_login)
        r.add_post("/api/logout", self.api_logout)
        r.add_get("/api/info", self.api_info)
        r.add_get("/api/peers", self.api_peers)
        r.add_get("/api/session", self.api_session)
        r.add_get("/api/fs/list", self.api_fs_list)
        r.add_get("/api/fs/download", self.api_fs_download)
        r.add_post("/api/fs/upload", self.api_fs_upload)
        r.add_post("/api/fs/mkdir", self.api_fs_mkdir)
        r.add_post("/api/fs/delete", self.api_fs_delete)
        r.add_post("/api/fs/rename", self.api_fs_rename)
        r.add_post("/api/fs/open", self.api_fs_open)
        r.add_get("/api/sysinfo", self.api_sysinfo)
        r.add_post("/api/power", self.api_power)
        r.add_post("/api/shell", self.api_shell)
        r.add_post("/api/wol", self.api_wol)
        r.add_get("/api/audit", self.api_audit)
        r.add_get("/api/debug/events", self.api_debug_events)
        r.add_static("/static", STATIC_DIR, show_index=False)
        return app

    PUBLIC = {"/", "/api/login", "/api/info", "/api/peers"}

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler):
        path = request.path
        if path in self.PUBLIC or path.startswith("/static/"):
            return await handler(request)
        token = request.cookies.get(COOKIE) or request.query.get("token") or request.headers.get("X-Token")
        sess = self.sessions.get(token or "")
        if not sess:
            raise web.HTTPUnauthorized(text=json.dumps({"error": "unauthorized"}), content_type="application/json")
        sess["last_seen"] = time.time()
        request["session"] = sess
        request["token"] = token
        return await handler(request)

    # ------------------------------------------------------------------ basic
    async def index(self, request):
        return web.FileResponse(os.path.join(STATIC_DIR, "index.html"))

    async def api_info(self, request):
        return web.json_response({
            "name": self.name, "os": platform.system(), "version": __version__, "demo": self.demo,
            "ips": netutil.local_ips(), "port": self.port, "id": self.device_id, "https": self.https,
            "h264": video.AVAILABLE, "view_only": bool(self.settings.get("view_only")),
            "require_accept": bool(self.settings.get("require_accept")),
        })

    async def api_peers(self, request):
        return web.json_response({"peers": self.discovery.list(), "self": self.announce_info()})

    async def api_session(self, request):
        s = request["session"]
        return web.json_response({"name": s["name"], "ip": s["ip"], "allow_shell": bool(self.settings.get("allow_shell")),
                                  "view_only": bool(self.settings.get("view_only"))})

    async def api_login(self, request):
        ip = request.remote or "?"
        now = time.time()
        fails = [t for t in self.failed_logins.get(ip, []) if now - t < 60]
        self.failed_logins[ip] = fails
        if len(fails) >= 5:
            return web.json_response({"error": "嘗試次數過多，請 1 分鐘後再試"}, status=429)
        try:
            body = await request.json()
        except Exception:
            body = {}
        pin = str(body.get("pin", "")).strip()
        name = str(body.get("name", "")).strip()[:40] or ip
        if not secrets.compare_digest(pin, str(self.settings.get("pin", ""))):
            fails.append(now)
            self._audit("login_failed", ip=ip, name=name)
            return web.json_response({"error": "PIN 碼錯誤"}, status=403)
        if self.settings.get("require_accept") and self.approval_handler:
            try:
                ok = await asyncio.wait_for(self.approval_handler(name, ip), timeout=45)
            except asyncio.TimeoutError:
                ok = False
            if not ok:
                self._audit("login_denied", ip=ip, name=name)
                return web.json_response({"error": "對方拒絕了連線（或未在時間內回應）"}, status=403)
        token = secrets.token_urlsafe(24)
        self.sessions[token] = {"name": name, "ip": ip, "created": now, "last_seen": now}
        self._audit("login", ip=ip, name=name)
        resp = web.json_response({"ok": True, "token": token})
        resp.set_cookie(COOKIE, token, httponly=True, samesite="Lax", max_age=7 * 24 * 3600)
        return resp

    async def api_logout(self, request):
        self.sessions.pop(request["token"], None)
        resp = web.json_response({"ok": True})
        resp.del_cookie(COOKIE)
        return resp

    async def api_audit(self, request):
        return web.json_response({"audit": self.audit[-200:]})

    async def api_debug_events(self, request):
        if not self.demo:
            raise web.HTTPNotFound()
        return web.json_response({"events": self.event_log[-50:]})

    # ------------------------------------------------------------------ websocket
    async def ws_handler(self, request):
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=4 * 1024 * 1024)
        await ws.prepare(request)
        sess = request["session"]
        v = Viewer(ws=ws, ip=request.remote or "?", name=sess["name"], token=request["token"])
        self.viewers.add(v)
        self._audit("viewer_connected", ip=v.ip, name=v.name)
        await self._send_info(v)
        for m in self.chat_log[-30:]:
            await ws.send_json(m)
        stream = asyncio.create_task(self._stream_loop(v))
        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        await self._handle_message(v, json.loads(msg.data))
                    except Exception as e:  # keep the connection alive on bad input
                        log.warning("ws message error: %s", e)
                elif msg.type == web.WSMsgType.ERROR:
                    break
        finally:
            stream.cancel()
            self.viewers.discard(v)
            if v.h264:
                v.h264.close()
            self.control.release_all()
            self._audit("viewer_disconnected", ip=v.ip, name=v.name)
        return ws

    async def _send_info(self, v: Viewer):
        await v.ws.send_json({
            "t": "info", "name": self.name, "os": platform.system(), "version": __version__, "demo": self.demo,
            "monitors": self.capture.monitors(), "view_only": bool(self.settings.get("view_only")),
            "allow_shell": bool(self.settings.get("allow_shell")), "capture": self.capture.name,
            "control": self.control.name, "h264": video.AVAILABLE, "encoder": self.encoder_name,
        })

    def _frame_to_screen(self, v: Viewer, nx: float, ny: float) -> tuple[float, float]:
        m = self.capture.monitor_rect(v.monitor)
        return m["left"] + min(max(nx, 0.0), 1.0) * m["width"], m["top"] + min(max(ny, 0.0), 1.0) * m["height"]

    async def _handle_message(self, v: Viewer, m: dict):
        t = m.get("t")
        if t == "ping":
            await v.ws.send_json({"t": "pong", "ts": m.get("ts"), "bytes": v.bytes_sent, "frames": v.frames_sent})
            return
        if t == "settings":
            if "monitor" in m:
                v.monitor = int(m["monitor"])
            if "quality" in m:
                v.quality = max(10, min(95, int(m["quality"])))
            if "fps" in m:
                v.fps = max(1, min(60, int(m["fps"])))
            if "scale" in m:
                v.scale = max(0.25, min(1.0, float(m["scale"])))
            if "paused" in m:
                v.paused = bool(m["paused"])
            if "codec" in m:
                v.codec = "h264" if (m["codec"] == "h264" and video.AVAILABLE) else "jpeg"
            v.force_full = True  # any change -> new keyframe / new encoder
            await v.ws.send_json({"t": "codec", "codec": v.codec})
            return
        if t == "refresh":
            v.force_full = True
            return
        if t == "chat":
            entry = {"t": "chat", "from": v.name, "text": str(m.get("text", ""))[:2000], "time": time.strftime("%H:%M")}
            self.chat_log.append(entry)
            del self.chat_log[:-200]
            await self.broadcast(entry)
            self._audit("chat", **{"from": v.name, "text": entry["text"]})
            return
        if t == "clip_get":
            text = await asyncio.get_running_loop().run_in_executor(None, sysactions.get_clipboard)
            await v.ws.send_json({"t": "clip", "text": text})
            return
        if t == "clip_set":
            ok = await asyncio.get_running_loop().run_in_executor(None, sysactions.set_clipboard, str(m.get("text", "")))
            await v.ws.send_json({"t": "toast", "text": "已寫入對方剪貼簿" if ok else "寫入剪貼簿失敗"})
            return
        # --- everything below injects input ---
        if self.settings.get("view_only"):
            return
        v.last_input = time.time()
        c = self.control
        if t == "mm":
            x, y = self._frame_to_screen(v, float(m["x"]), float(m["y"]))
            c.mouse_move(x, y)
        elif t in ("md", "mu"):
            if "x" in m:
                x, y = self._frame_to_screen(v, float(m["x"]), float(m["y"]))
                c.mouse_move(x, y)
            c.mouse_button(str(m.get("b", "left")), t == "md")
        elif t == "wh":
            c.mouse_scroll(int(m.get("dx", 0)), int(m.get("dy", 0)))
        elif t in ("kd", "ku"):
            c.key(str(m.get("key", "")), str(m.get("code", "")), t == "kd")
        elif t == "type":
            text = str(m.get("text", ""))
            if text:
                await asyncio.get_running_loop().run_in_executor(None, c.type_text, text)
            if m.get("enter"):
                c.combo(["enter"])
        elif t == "combo":
            keys = [str(k) for k in m.get("keys", [])][:6]
            await asyncio.get_running_loop().run_in_executor(None, c.combo, keys)
        elif t == "release_all":
            c.release_all()

    async def broadcast(self, msg: dict):
        for v in list(self.viewers):
            try:
                await v.ws.send_json(msg)
            except Exception:
                pass

    def _produce(self, v: Viewer):
        """Runs in a worker thread: grab the screen and encode one frame. Returns (header, bytes) or None."""
        if v.codec == "h264" and video.AVAILABLE:
            arr, fmt = self.capture.grab_raw(v.monitor)
            h, w = arr.shape[:2]
            scale = _effective_scale(w, v.scale)
            enc = v.h264
            need_new = (enc is None or v.force_full or enc.src_w != w or enc.src_h != h or enc.src_format != fmt
                        or enc.fps != v.fps or enc.quality != v.quality
                        or enc.width != max(2, int(w * scale)) // 2 * 2)
            if not need_new and v.prev_raw is not None and v.prev_raw.shape == arr.shape:
                import numpy as np

                if np.array_equal(v.prev_raw, arr):
                    return None  # nothing changed -> send nothing
            v.prev_raw = arr.copy() if not arr.flags.owndata else arr
            if need_new:
                if enc:
                    enc.close()
                try:
                    enc = video.H264Encoder(w, h, v.fps, v.quality, fmt, int(w * scale), int(h * scale))
                except Exception as e:
                    log.warning("H.264 unavailable (%s), falling back to JPEG", e)
                    v.codec = "jpeg"
                    v.h264 = None
                    v.force_full = True
                    return self._produce(v)
                v.h264 = enc
                self.encoder_name = enc.name
                v.force_full = False
            packets = enc.encode(arr, force_key=need_new)
            if not packets:
                return None
            data = b"".join(p for p, _ in packets)
            key = any(k for _, k in packets)
            return {"t": "h264", "key": key, "fw": enc.width, "fh": enc.height, "enc": enc.name}, data
        img = self.capture.grab(v.monitor)
        force = v.force_full
        v.force_full = False
        return v.encoder.encode(img, v.quality, _effective_scale(img.width, v.scale), force)

    async def _stream_loop(self, v: Viewer):
        loop = asyncio.get_running_loop()
        last_cursor = None
        while not v.ws.closed:
            if v.paused:
                await asyncio.sleep(0.25)
                continue
            t0 = loop.time()
            try:
                res = await loop.run_in_executor(self.executor, self._produce, v)
            except Exception as e:
                log.warning("capture/encode error: %s", e)
                await asyncio.sleep(0.5)
                continue
            cur = self.control.cursor_position()
            cur_norm = None
            if cur is not None:
                mr = self.capture.monitor_rect(v.monitor)
                if mr["width"] and mr["height"]:
                    cur_norm = ((cur[0] - mr["left"]) / mr["width"], (cur[1] - mr["top"]) / mr["height"])
            if res is not None:
                header, data = res
                if cur_norm:
                    header["cx"], header["cy"] = round(cur_norm[0], 4), round(cur_norm[1], 4)
                hb = json.dumps(header).encode()
                packet = struct.pack(">I", len(hb)) + hb + data
                try:
                    await v.ws.send_bytes(packet)
                except Exception:
                    break
                v.bytes_sent += len(packet)
                v.frames_sent += 1
                last_cursor = cur_norm
            elif cur_norm and cur_norm != last_cursor:
                try:
                    await v.ws.send_json({"t": "cur", "x": round(cur_norm[0], 4), "y": round(cur_norm[1], 4)})
                except Exception:
                    break
                last_cursor = cur_norm
            elapsed = loop.time() - t0
            await asyncio.sleep(max(0.0, 1.0 / v.fps - elapsed))

    # ------------------------------------------------------------------ files
    def _receive_dir(self) -> str:
        d = self.settings.get("receive_dir") or os.path.join(os.path.expanduser("~"), "LanRemote Received")
        os.makedirs(d, exist_ok=True)
        return d

    def _check_path(self, p: str) -> str:
        p = os.path.abspath(os.path.expanduser(p))
        root = self.settings.get("fs_root")
        if root:
            root = os.path.abspath(os.path.expanduser(root))
            if os.path.commonpath([root, p]) != root:
                raise web.HTTPForbidden(text="路徑超出允許範圍")
        return p

    async def api_fs_list(self, request):
        path = request.query.get("path", "")
        roots = []
        if sys.platform.startswith("win"):
            roots = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
        else:
            roots = ["/"]
        home = os.path.expanduser("~")
        shortcuts = [{"name": "家目錄", "path": home}, {"name": "接收資料夾", "path": self._receive_dir()}]
        for sub in ("Desktop", "Downloads", "Documents"):
            p = os.path.join(home, sub)
            if os.path.isdir(p):
                shortcuts.append({"name": sub, "path": p})
        if path == "":
            path = home
        path = self._check_path(path)
        if not os.path.isdir(path):
            return web.json_response({"error": "資料夾不存在"}, status=404)

        def scan():
            entries = []
            try:
                with os.scandir(path) as it:
                    for e in it:
                        try:
                            st = e.stat(follow_symlinks=False)
                            entries.append({"name": e.name, "is_dir": e.is_dir(), "size": 0 if e.is_dir() else st.st_size,
                                            "mtime": int(st.st_mtime)})
                        except OSError:
                            continue
            except PermissionError:
                raise web.HTTPForbidden(text="沒有權限")
            entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
            return entries

        entries = await asyncio.get_running_loop().run_in_executor(None, scan)
        parent = os.path.dirname(path.rstrip("\\/")) or path
        if parent == path or (sys.platform.startswith("win") and len(path) <= 3):
            parent = None
        return web.json_response({"path": path, "parent": parent, "entries": entries, "roots": roots,
                                  "shortcuts": shortcuts, "sep": os.sep})

    async def api_fs_download(self, request):
        path = self._check_path(request.query.get("path", ""))
        if not os.path.exists(path):
            raise web.HTTPNotFound(text="檔案不存在")
        name = os.path.basename(path.rstrip("\\/")) or "root"
        self._audit("download", ip=request.remote, path=path, name=request["session"]["name"])
        if os.path.isdir(path):
            tmp = tempfile.NamedTemporaryFile(prefix="lanremote-", suffix=".zip", delete=False)
            tmp.close()

            def zip_it():
                with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as z:
                    for root, dirs, files in os.walk(path):
                        for f in files:
                            fp = os.path.join(root, f)
                            try:
                                z.write(fp, os.path.relpath(fp, path))
                            except OSError:
                                continue

            await asyncio.get_running_loop().run_in_executor(None, zip_it)
            resp = web.FileResponse(tmp.name, headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{_urlquote(name)}.zip"})

            async def cleanup(_):
                try:
                    os.remove(tmp.name)
                except OSError:
                    pass

            request.task.add_done_callback(lambda _: asyncio.ensure_future(cleanup(None)))
            return resp
        return web.FileResponse(path, headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{_urlquote(name)}"})

    async def api_fs_upload(self, request):
        target_dir = request.query.get("dir") or self._receive_dir()
        target_dir = self._check_path(target_dir)
        os.makedirs(target_dir, exist_ok=True)
        reader = await request.multipart()
        saved = []
        while True:
            part = await reader.next()
            if part is None:
                break
            if not part.filename:
                continue
            fname = os.path.basename(part.filename.replace("\\", "/")) or "file"
            dest = _unique_path(os.path.join(target_dir, fname))
            size = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = await part.read_chunk(1024 * 256)
                    if not chunk:
                        break
                    f.write(chunk)
                    size += len(chunk)
            saved.append({"name": os.path.basename(dest), "path": dest, "size": size})
            self._audit("upload", ip=request.remote, path=dest, size=size, name=request["session"]["name"])
        await self.broadcast({"t": "toast", "text": f"收到 {len(saved)} 個檔案 → {target_dir}"})
        return web.json_response({"ok": True, "saved": saved, "dir": target_dir})

    async def api_fs_mkdir(self, request):
        body = await request.json()
        p = self._check_path(os.path.join(body["path"], body["name"]))
        os.makedirs(p, exist_ok=True)
        return web.json_response({"ok": True, "path": p})

    async def api_fs_delete(self, request):
        body = await request.json()
        p = self._check_path(body["path"])
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p)
        else:
            os.remove(p)
        self._audit("delete", ip=request.remote, path=p, name=request["session"]["name"])
        return web.json_response({"ok": True})

    async def api_fs_rename(self, request):
        body = await request.json()
        p = self._check_path(body["path"])
        new = os.path.join(os.path.dirname(p), os.path.basename(body["name"]))
        os.rename(p, new)
        return web.json_response({"ok": True, "path": new})

    async def api_fs_open(self, request):
        body = await request.json()
        p = self._check_path(body["path"])
        if self.demo:
            self.event_log.append(f"open: {p}")
        else:
            sysactions.open_path(p)
        return web.json_response({"ok": True})

    # ------------------------------------------------------------------ system
    async def api_sysinfo(self, request):
        info = await asyncio.get_running_loop().run_in_executor(None, sysactions.system_info)
        info["viewers"] = [{"name": v.name, "ip": v.ip, "since": int(v.connected_at)} for v in self.viewers]
        return web.json_response(info)

    async def api_power(self, request):
        body = await request.json()
        action = str(body.get("action", ""))
        if self.settings.get("view_only"):
            return web.json_response({"error": "目前為唯讀模式"}, status=403)
        try:
            result = sysactions.power_action(action, dry_run=self.demo)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        self._audit("power", ip=request.remote, action=action, name=request["session"]["name"])
        return web.json_response({"ok": True, "result": result})

    async def api_shell(self, request):
        if not self.settings.get("allow_shell") or self.settings.get("view_only"):
            return web.json_response({"error": "遠端指令功能未開啟（在主機端設定 allow_shell）"}, status=403)
        body = await request.json()
        cmd = str(body.get("cmd", ""))[:4000]
        self._audit("shell", ip=request.remote, cmd=cmd, name=request["session"]["name"])
        res = await asyncio.get_running_loop().run_in_executor(None, sysactions.run_shell, cmd)
        return web.json_response(res)

    async def api_wol(self, request):
        body = await request.json()
        try:
            netutil.send_wol(str(body.get("mac", "")))
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"ok": True})

    # ------------------------------------------------------------------ lifecycle
    async def start(self):
        self.loop = asyncio.get_running_loop()
        self.runner = web.AppRunner(self.app, access_log=None)
        await self.runner.setup()
        ssl_ctx = None
        if self.https:
            from .tls import ensure_certificate

            cert, key = ensure_certificate()
            ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_ctx.load_cert_chain(cert, key)
        site = web.TCPSite(self.runner, "0.0.0.0", self.port, reuse_address=True, ssl_context=ssl_ctx)
        await site.start()
        self.discovery.start()
        log.info("listening on port %s", self.port)

    async def stop(self):
        self.discovery.stop()
        for v in list(self.viewers):
            try:
                await v.ws.close()
            except Exception:
                pass
        if self.runner:
            await self.runner.cleanup()

    def viewer_list(self) -> list[dict]:
        return [{"name": v.name, "ip": v.ip, "since": v.connected_at, "fps": v.fps, "bytes": v.bytes_sent}
                for v in self.viewers]

    def kick_all(self):
        """Thread-safe: disconnect every viewer and invalidate sessions."""
        if not self.loop:
            return

        async def _do():
            self.sessions.clear()
            for v in list(self.viewers):
                await v.ws.close()

        asyncio.run_coroutine_threadsafe(_do(), self.loop)

    def host_chat(self, text: str):
        """Thread-safe: send a chat message from the host GUI to all viewers."""
        if not self.loop:
            return
        entry = {"t": "chat", "from": f"{self.name} (主機)", "text": text, "time": time.strftime("%H:%M")}
        self.chat_log.append(entry)
        asyncio.run_coroutine_threadsafe(self.broadcast(entry), self.loop)


MAX_STREAM_WIDTH = 2560


def _effective_scale(width: int, scale: float) -> float:
    """"100%" means "native, but never wider than 2560px" so 4K / Retina screens stay smooth."""
    if width * scale > MAX_STREAM_WIDTH:
        return MAX_STREAM_WIDTH / width
    return scale


def _unique_path(p: str) -> str:
    if not os.path.exists(p):
        return p
    base, ext = os.path.splitext(p)
    i = 1
    while True:
        cand = f"{base} ({i}){ext}"
        if not os.path.exists(cand):
            return cand
        i += 1


def _urlquote(s: str) -> str:
    from urllib.parse import quote

    return quote(s, safe="")
