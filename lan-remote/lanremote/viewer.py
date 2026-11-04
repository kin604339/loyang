"""Native viewer window (no browser): connects to a LAN Remote host, decodes H.264 / JPEG, sends input."""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import struct
import sys
import threading
import time
import webbrowser
from typing import Optional

import aiohttp

from . import video

log = logging.getLogger("lanremote.viewer")



class Net(threading.Thread):
    """Runs the network side (login, websocket, decoding) on its own asyncio loop."""

    def __init__(self, url: str, pin: str, name: str, settings: dict):
        super().__init__(name="lr-viewer-net", daemon=True)
        self.url = url.rstrip("/")
        self.pin = pin
        self.name = name
        self.settings = settings
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.outq: Optional[asyncio.Queue] = None
        self.lock = threading.Lock()
        self.latest = None  # (rgb ndarray, header)
        self.events: list[dict] = []  # json messages for the UI thread
        self.info: dict = {}
        self.token = ""
        self.codec = "?"
        self.bytes = 0
        self.frames = 0
        self.latency = 0
        self.error: Optional[str] = None
        self.connected = False
        self.stop_flag = False
        self._decoder = None
        self._canvas = None  # ndarray for JPEG dirty-rect mode
        self.use_h264 = video.AVAILABLE and settings.get("codec", "h264") == "h264"

    # -- thread API ---------------------------------------------------------
    def send(self, obj: dict):
        if self.loop and self.outq is not None and not self.stop_flag:
            self.loop.call_soon_threadsafe(self.outq.put_nowait, json.dumps(obj))

    def take_frame(self):
        with self.lock:
            f, self.latest = self.latest, None
        return f

    def take_events(self) -> list[dict]:
        with self.lock:
            ev, self.events = self.events, []
        return ev

    def send_settings(self):
        s = self.settings
        self.send({"t": "settings", "codec": "h264" if self.use_h264 else "jpeg", "fps": s.get("fps", 30),
                   "quality": s.get("quality", 60), "scale": s.get("scale", 1.0), "monitor": s.get("monitor", 1)})

    def stop(self):
        self.stop_flag = True
        if self.loop:
            self.loop.call_soon_threadsafe(lambda: None)

    # -- asyncio side ---------------------------------------------------------
    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._main())
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
        finally:
            self.connected = False

    async def _main(self):
        self.outq = asyncio.Queue()
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(self.url + "/api/login", json={"pin": self.pin, "name": self.name}) as r:
                data = await r.json(content_type=None)
                if r.status != 200:
                    raise RuntimeError(data.get("error", f"登入失敗 ({r.status})"))
                self.token = data["token"]
            ws_url = self.url.replace("http", "ws", 1) + "/ws?token=" + self.token
            async with session.ws_connect(ws_url, max_msg_size=16 * 1024 * 1024, heartbeat=20) as ws:
                self.connected = True
                self.send_settings()
                sender = asyncio.create_task(self._sender(ws))
                pinger = asyncio.create_task(self._pinger())
                try:
                    async for msg in ws:
                        if self.stop_flag:
                            break
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            self._on_binary(msg.data)
                        elif msg.type == aiohttp.WSMsgType.TEXT:
                            self._on_text(json.loads(msg.data))
                        else:
                            break
                finally:
                    sender.cancel()
                    pinger.cancel()
                    self.connected = False

    async def _sender(self, ws):
        while True:
            s = await self.outq.get()
            await ws.send_str(s)

    async def _pinger(self):
        while True:
            self.send({"t": "ping", "ts": time.time()})
            await asyncio.sleep(1)

    def _on_text(self, m: dict):
        t = m.get("t")
        if t == "pong":
            self.latency = int((time.time() - float(m.get("ts", 0))) * 1000)
            return
        if t == "info":
            self.info = m
        if t == "codec":
            self.codec = m.get("codec", "?")
        with self.lock:
            self.events.append(m)

    def _on_binary(self, buf: bytes):
        self.bytes += len(buf)
        hl = struct.unpack(">I", buf[:4])[0]
        header = json.loads(buf[4:4 + hl])
        data = buf[4 + hl:]
        arr = None
        if header.get("t") == "h264":
            if self._decoder is None:
                self._decoder = video.H264Decoder()
            arr = self._decoder.decode(data, header.get("key", False))
            self.codec = "h264/" + header.get("enc", "")
        else:
            import numpy as np
            from PIL import Image

            region = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
            fw, fh = header["fw"], header["fh"]
            if self._canvas is None or self._canvas.shape[:2] != (fh, fw):
                self._canvas = np.zeros((fh, fw, 3), dtype=np.uint8)
            x, y = header["x"], header["y"]
            h, w = region.shape[:2]
            self._canvas[y:y + h, x:x + w] = region
            arr = self._canvas.copy()
            self.codec = "jpeg"
        if arr is None:
            with self.lock:
                self.events.append({"t": "cur", "x": header.get("cx"), "y": header.get("cy")})
            return
        self.frames += 1
        with self.lock:
            self.latest = (arr, header)


def _ask_pin(host: str) -> Optional[str]:
    try:
        import tkinter as tk
        from tkinter import simpledialog

        root = tk.Tk()
        root.withdraw()
        pin = simpledialog.askstring("LAN Remote", f"連線到 {host}\n請輸入對方視窗顯示的 PIN 碼：", parent=root, show="*")
        root.destroy()
        return pin
    except Exception:
        try:
            return input(f"連線到 {host} - PIN 碼: ").strip()
        except EOFError:
            return None


def _message(title: str, text: str):
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, text)
        root.destroy()
    except Exception:
        print(f"{title}: {text}")


QUALITY_LABELS = [(35, "低"), (60, "中"), (80, "高"), (92, "最高")]


def main(url: str, pin: Optional[str] = None, name: Optional[str] = None) -> int:
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    os.environ.setdefault("SDL_HINT_GRAB_KEYBOARD", "1")
    import pygame

    if "://" not in url:
        url = "http://" + url
    if url.count(":") < 2:
        url = url.rstrip("/") + ":7788"
    host = url.split("://", 1)[1]
    if not pin:
        pin = _ask_pin(host)
        if not pin:
            return 1
    name = name or os.environ.get("LANREMOTE_VIEWER_NAME") or (os.environ.get("USERNAME") or os.environ.get("USER") or "viewer")
    settings = {"fps": 30, "quality": 60, "scale": 1.0, "monitor": 1, "codec": "h264" if video.AVAILABLE else "jpeg"}
    test_seconds = float(os.environ.get("LANREMOTE_VIEWER_TEST", "0") or 0)

    net = Net(url, pin, name, settings)
    net.start()

    pygame.init()
    pygame.display.set_caption(f"LAN Remote - {host}")
    screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("Microsoft JhengHei,PingFang TC,Noto Sans CJK TC,Arial", 16)
    surf = scaled = scaled_key = None
    fw = fh = 0
    cursor = None
    fullscreen = False
    smooth = True
    toast = ("滑鼠移到視窗最上方會出現工具列。所有鍵盤操作都會直接送給對方。", time.time() + 6)
    pressed: dict[int, tuple[str, str]] = {}
    pending_move = None
    shown_frames = 0
    last_stat = time.time()
    fps_display = 0
    monitors: list[int] = [1]
    running = True
    t_start = time.time()
    test_posted = False
    kb_grab = True
    bar_visible_until = 0.0
    BAR_H = 38
    def _get_window():
        try:
            import warnings

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                return pygame.Window.from_display_module()
        except Exception:
            return None

    set_kb_grab = hasattr(getattr(pygame, "Window", None), "grab_keyboard")

    def apply_grab():
        """Keyboard grab = system shortcuts (Alt+Tab, Win key) go to the remote side while we have focus."""
        want = bool(kb_grab and pygame.key.get_focused())
        try:
            w = _get_window() if set_kb_grab else None
            if w is not None:
                w.grab_keyboard = want
            elif fullscreen:
                pygame.event.set_grab(want)
        except Exception:
            pass

    def rect_fit():
        sw, sh = screen.get_size()
        if not fw or not fh:
            return pygame.Rect(0, 0, sw, sh)
        k = min(sw / fw, sh / fh)
        w, h = max(1, int(fw * k)), max(1, int(fh * k))
        return pygame.Rect((sw - w) // 2, (sh - h) // 2, w, h)

    def norm(pos):
        r = rect_fit()
        return {"x": max(0.0, min(1.0, (pos[0] - r.x) / r.w)), "y": max(0.0, min(1.0, (pos[1] - r.y) / r.h))}

    def say(text, secs=3):
        nonlocal toast
        toast = (text, time.time() + secs)

    def map_key(ev):
        k = ev.key
        if k in SPECIAL:
            return SPECIAL[k]
        if pygame.K_a <= k <= pygame.K_z:
            ch, code = chr(k), "Key" + chr(k).upper()
        elif pygame.K_0 <= k <= pygame.K_9:
            ch, code = chr(k), "Digit" + chr(k)
        elif k in PUNCT:
            ch, code = PUNCT[k]
        else:
            ch, code = None, pygame.key.name(k)
        uni = getattr(ev, "unicode", "")
        key = uni if (uni and ord(uni[0]) >= 32) else ch
        if key is None:
            return None
        return key, code

    # ---- toolbar actions ------------------------------------------------
    def act_fullscreen():
        nonlocal fullscreen, screen, scaled_key
        fullscreen = not fullscreen
        screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN) if fullscreen else pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
        scaled_key = None
        apply_grab()

    def act_monitor():
        if len(monitors) > 1:
            i = monitors.index(settings["monitor"]) if settings["monitor"] in monitors else 0
            settings["monitor"] = monitors[(i + 1) % len(monitors)]
            net.send_settings()
        else:
            say("對方只有一個螢幕")

    def act_quality():
        qs = [q for q, _ in QUALITY_LABELS]
        i = qs.index(settings["quality"]) if settings["quality"] in qs else 1
        settings["quality"] = qs[(i + 1) % len(qs)]
        net.send_settings()

    def act_clip_pull():
        net.send({"t": "clip_get"})

    def act_clip_push():
        try:
            import pyperclip

            net.send({"t": "clip_set", "text": pyperclip.paste()})
        except Exception as e:
            say(f"讀取剪貼簿失敗: {e}")

    def act_browser():
        webbrowser.open(f"{url}/?token={net.token}")

    def act_refresh():
        net.send({"t": "refresh"})

    def act_grab():
        nonlocal kb_grab
        kb_grab = not kb_grab
        apply_grab()
        say("鍵盤直通：開（Alt+Tab、Win 鍵會送給對方）" if kb_grab else "鍵盤直通：關（系統快捷鍵留在本機）")

    def act_quit():
        nonlocal running
        running = False

    def quality_label():
        return next((lab for q, lab in QUALITY_LABELS if q == settings["quality"]), str(settings["quality"]))

    def toolbar_items():
        return [
            ("離開全螢幕" if fullscreen else "全螢幕", act_fullscreen),
            (f"螢幕 {settings['monitor']}/{len(monitors)}", act_monitor),
            (f"畫質 {quality_label()}", act_quality),
            ("抓對方剪貼簿", act_clip_pull),
            ("送出我的剪貼簿", act_clip_push),
            ("檔案 / 聊天", act_browser),
            ("重新整理", act_refresh),
            ("鍵盤直通 " + ("開" if kb_grab else "關"), act_grab),
            ("離開", act_quit),
        ]

    def toolbar_rects():
        x = 8
        out = []
        for label, fn in toolbar_items():
            w = font.size(label)[0] + 18
            out.append((pygame.Rect(x, 6, w, BAR_H - 12), label, fn))
            x += w + 6
        return out

    def bar_showing():
        return time.time() < bar_visible_until

    BTN = {1: "left", 2: "middle", 3: "right", 6: "back", 7: "forward"}
    apply_grab()
    while running:
        if net.error:
            break
        mx, my = pygame.mouse.get_pos()
        if pygame.mouse.get_focused() and (my <= 2 or (bar_showing() and my < BAR_H)):
            bar_visible_until = time.time() + 1.2
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.MOUSEMOTION:
                if not (bar_showing() and ev.pos[1] < BAR_H):
                    pending_move = norm(ev.pos)
            elif ev.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                if bar_showing() and ev.pos[1] < BAR_H:
                    if ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
                        for r, label, fn in toolbar_rects():
                            if r.collidepoint(ev.pos):
                                fn()
                                break
                    continue
                if ev.button in BTN:
                    if pending_move:
                        net.send({"t": "mm", **pending_move})
                        pending_move = None
                    net.send({"t": "md" if ev.type == pygame.MOUSEBUTTONDOWN else "mu", "b": BTN[ev.button], **norm(ev.pos)})
            elif ev.type == pygame.MOUSEWHEEL:
                dx = int(getattr(ev, "precise_x", ev.x)) or (1 if ev.x > 0 else -1 if ev.x < 0 else 0)
                dy = int(getattr(ev, "precise_y", ev.y)) or (1 if ev.y > 0 else -1 if ev.y < 0 else 0)
                if dx or dy:
                    net.send({"t": "wh", "dx": dx, "dy": dy})
            elif ev.type == pygame.KEYDOWN:
                mk = map_key(ev)
                if mk:
                    pressed[ev.scancode] = mk
                    net.send({"t": "kd", "key": mk[0], "code": mk[1]})
            elif ev.type == pygame.KEYUP:
                mk = pressed.pop(ev.scancode, None) or map_key(ev)
                if mk:
                    net.send({"t": "ku", "key": mk[0], "code": mk[1]})
            elif ev.type == getattr(pygame, "WINDOWFOCUSLOST", -1):
                net.send({"t": "release_all"})
                pressed.clear()
                apply_grab()
            elif ev.type == getattr(pygame, "WINDOWFOCUSGAINED", -1):
                apply_grab()
        if pending_move:
            net.send({"t": "mm", **pending_move})
            pending_move = None

        for m in net.take_events():
            t = m.get("t")
            if t == "info":
                monitors = [mon["index"] for mon in m.get("monitors", [])] or [1]
                pygame.display.set_caption(f"LAN Remote - {m.get('name')} ({host})")
                if m.get("view_only"):
                    say("對方開啟了唯讀模式")
            elif t == "toast":
                say(m.get("text", ""))
            elif t == "chat":
                say(f"{m.get('from')}: {m.get('text')}", 6)
            elif t == "cur" and m.get("x") is not None:
                cursor = (m["x"], m["y"])
            elif t == "clip":
                try:
                    import pyperclip

                    pyperclip.copy(m.get("text", ""))
                    say("已複製對方剪貼簿到本機")
                except Exception as e:
                    say(f"寫入剪貼簿失敗: {e}")

        frame = net.take_frame()
        if frame is not None:
            arr, header = frame
            fh, fw = arr.shape[:2]
            surf = pygame.image.frombuffer(arr.tobytes(), (fw, fh), "RGB")
            scaled_key = None
            shown_frames += 1
            if header.get("cx") is not None:
                cursor = (header["cx"], header["cy"])

        screen.fill((0, 0, 0))
        r = rect_fit()
        if surf is not None:
            key = (r.w, r.h, smooth)
            if scaled_key != key:
                if (r.w, r.h) == surf.get_size():
                    scaled = surf
                else:
                    t0 = time.perf_counter()
                    scaled = pygame.transform.smoothscale(surf, (r.w, r.h)) if smooth else pygame.transform.scale(surf, (r.w, r.h))
                    if smooth and time.perf_counter() - t0 > 0.012:
                        smooth = False  # too slow on this machine -> fast scaling
                        key = (r.w, r.h, smooth)
                scaled_key = key
            screen.blit(scaled, r.topleft)
            if cursor:
                cx, cy = r.x + cursor[0] * r.w, r.y + cursor[1] * r.h
                pts = [(cx, cy), (cx, cy + 16), (cx + 4, cy + 12), (cx + 11, cy + 12)]
                pygame.draw.polygon(screen, (0, 0, 0), pts)
                pygame.draw.polygon(screen, (255, 255, 255), pts, 1)
        else:
            msg = net.error or ("連線中…" if not net.connected else "等待畫面…")
            screen.blit(font.render(msg, True, (220, 220, 220)), (20, 20))
        over_bar = bar_showing() and my < BAR_H
        inside = r.collidepoint((mx, my)) and pygame.mouse.get_focused() and surf is not None and not over_bar
        pygame.mouse.set_visible(not inside)
        if bar_showing():
            bar = pygame.Surface((screen.get_width(), BAR_H), pygame.SRCALPHA)
            bar.fill((20, 22, 30, 235))
            screen.blit(bar, (0, 0))
            for br, label, fn in toolbar_rects():
                hover = br.collidepoint((mx, my))
                pygame.draw.rect(screen, (79, 140, 255) if hover else (45, 50, 64), br, border_radius=6)
                screen.blit(font.render(label, True, (255, 255, 255)), (br.x + 9, br.y + 3))
        if toast[0] and time.time() < toast[1]:
            screen.blit(font.render(toast[0], True, (255, 255, 255), (40, 40, 60)), (20, screen.get_height() - 36))
        pygame.display.flip()

        now = time.time()
        if now - last_stat >= 1.0:
            fps_display = shown_frames
            kb = net.bytes / 1024
            shown_frames = 0
            net.bytes = 0
            last_stat = now
            pygame.display.set_caption(f"LAN Remote - {net.info.get('name', host)}  {fps_display} fps  {kb:.0f} KB/s  "
                                       f"{net.latency} ms  {fw}x{fh}  {net.codec}")
        if test_seconds:
            if not test_posted and surf is not None:
                test_posted = True
                pygame.event.post(pygame.event.Event(pygame.MOUSEMOTION, pos=(r.centerx, r.centery), rel=(1, 1), buttons=(0, 0, 0)))
                pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(r.centerx, r.centery), button=1))
                pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=(r.centerx, r.centery), button=1))
                pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_LALT, mod=0, unicode="", scancode=226))
                pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_TAB, mod=pygame.KMOD_LALT, unicode="\t", scancode=43))
                pygame.event.post(pygame.event.Event(pygame.KEYUP, key=pygame.K_TAB, mod=pygame.KMOD_LALT, unicode="", scancode=43))
                pygame.event.post(pygame.event.Event(pygame.KEYUP, key=pygame.K_LALT, mod=0, unicode="", scancode=226))
                pygame.event.post(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-2, precise_x=0.0, precise_y=-2.0, flipped=False))
                bar_visible_until = now + 5  # exercise the toolbar drawing path too
            if now - t_start > test_seconds:
                print(json.dumps({"frames": net.frames, "codec": net.codec, "latency": net.latency, "error": net.error,
                                  "size": [fw, fh], "fps": fps_display, "kb_grab_api": bool(set_kb_grab)}))
                running = False
        clock.tick(60)

    net.stop()
    pygame.quit()
    if net.error:
        _message("LAN Remote", f"連線失敗：{net.error}")
        return 1
    return 0


def _build_tables():
    import pygame

    special = {
        pygame.K_RETURN: ("Enter", "Enter"), pygame.K_KP_ENTER: ("Enter", "NumpadEnter"),
        pygame.K_BACKSPACE: ("Backspace", "Backspace"), pygame.K_TAB: ("Tab", "Tab"),
        pygame.K_ESCAPE: ("Escape", "Escape"), pygame.K_SPACE: (" ", "Space"),
        pygame.K_UP: ("ArrowUp", "ArrowUp"), pygame.K_DOWN: ("ArrowDown", "ArrowDown"),
        pygame.K_LEFT: ("ArrowLeft", "ArrowLeft"), pygame.K_RIGHT: ("ArrowRight", "ArrowRight"),
        pygame.K_LSHIFT: ("Shift", "ShiftLeft"), pygame.K_RSHIFT: ("Shift", "ShiftRight"),
        pygame.K_LCTRL: ("Control", "ControlLeft"), pygame.K_RCTRL: ("Control", "ControlRight"),
        pygame.K_LALT: ("Alt", "AltLeft"), pygame.K_RALT: ("Alt", "AltRight"),
        pygame.K_LGUI: ("Meta", "MetaLeft"), pygame.K_RGUI: ("Meta", "MetaRight"),
        pygame.K_CAPSLOCK: ("CapsLock", "CapsLock"), pygame.K_DELETE: ("Delete", "Delete"),
        pygame.K_HOME: ("Home", "Home"), pygame.K_END: ("End", "End"),
        pygame.K_PAGEUP: ("PageUp", "PageUp"), pygame.K_PAGEDOWN: ("PageDown", "PageDown"),
        pygame.K_INSERT: ("Insert", "Insert"), pygame.K_PRINTSCREEN: ("PrintScreen", "PrintScreen"),
        pygame.K_NUMLOCKCLEAR: ("NumLock", "NumLock"), pygame.K_SCROLLLOCK: ("ScrollLock", "ScrollLock"),
        pygame.K_PAUSE: ("Pause", "Pause"), pygame.K_MENU: ("ContextMenu", "ContextMenu"),
    }
    for i in range(1, 13):
        special[getattr(pygame, f"K_F{i}")] = (f"F{i}", f"F{i}")
    punct = {
        pygame.K_MINUS: ("-", "Minus"), pygame.K_EQUALS: ("=", "Equal"), pygame.K_LEFTBRACKET: ("[", "BracketLeft"),
        pygame.K_RIGHTBRACKET: ("]", "BracketRight"), pygame.K_BACKSLASH: ("\\", "Backslash"),
        pygame.K_SEMICOLON: (";", "Semicolon"), pygame.K_QUOTE: ("'", "Quote"), pygame.K_COMMA: (",", "Comma"),
        pygame.K_PERIOD: (".", "Period"), pygame.K_SLASH: ("/", "Slash"), pygame.K_BACKQUOTE: ("`", "Backquote"),
        pygame.K_KP_PLUS: ("+", "NumpadAdd"), pygame.K_KP_MINUS: ("-", "NumpadSubtract"),
        pygame.K_KP_MULTIPLY: ("*", "NumpadMultiply"), pygame.K_KP_DIVIDE: ("/", "NumpadDivide"),
        pygame.K_KP_PERIOD: (".", "NumpadDecimal"),
    }
    for i in range(10):
        punct[getattr(pygame, f"K_KP{i}")] = (str(i), f"Numpad{i}")
    return special, punct


try:
    SPECIAL, PUNCT = _build_tables()
except Exception:  # pygame missing -> viewer unusable, agent still works
    SPECIAL, PUNCT = {}, {}


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1:7788", os.environ.get("LANREMOTE_PIN")))
