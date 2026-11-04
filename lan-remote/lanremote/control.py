"""Mouse / keyboard injection backends (pynput) with a demo fallback."""
from __future__ import annotations

import re
import time
from typing import Optional


class ControlBackend:
    name = "base"

    def mouse_move(self, x: float, y: float): ...
    def mouse_button(self, button: str, down: bool): ...
    def mouse_scroll(self, dx: int, dy: int): ...
    def key(self, key: str, code: str, down: bool): ...
    def type_text(self, text: str): ...
    def combo(self, keys: list[str]): ...
    def release_all(self): ...
    def cursor_position(self) -> Optional[tuple[float, float]]:
        return None


# Browser KeyboardEvent.key -> pynput Key attribute name
_KEY_NAMES = {
    "Enter": "enter", "Backspace": "backspace", "Tab": "tab", "Escape": "esc", " ": "space",
    "ArrowUp": "up", "ArrowDown": "down", "ArrowLeft": "left", "ArrowRight": "right",
    "Shift": "shift", "Control": "ctrl", "Alt": "alt", "Meta": "cmd", "OS": "cmd",
    "CapsLock": "caps_lock", "Delete": "delete", "Home": "home", "End": "end",
    "PageUp": "page_up", "PageDown": "page_down", "Insert": "insert",
    "PrintScreen": "print_screen", "ScrollLock": "scroll_lock", "Pause": "pause",
    "NumLock": "num_lock", "ContextMenu": "menu",
    "AudioVolumeMute": "media_volume_mute", "AudioVolumeDown": "media_volume_down",
    "AudioVolumeUp": "media_volume_up", "MediaPlayPause": "media_play_pause",
    "MediaTrackNext": "media_next", "MediaTrackPrevious": "media_previous",
}
for _i in range(1, 21):
    _KEY_NAMES[f"F{_i}"] = f"f{_i}"

# names accepted by the "combo" command
_COMBO_ALIASES = {
    "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "option": "alt", "shift": "shift",
    "win": "cmd", "cmd": "cmd", "command": "cmd", "meta": "cmd", "super": "cmd",
    "esc": "esc", "escape": "esc", "tab": "tab", "enter": "enter", "del": "delete", "delete": "delete",
    "backspace": "backspace", "space": "space", "printscreen": "print_screen", "home": "home", "end": "end",
    "pageup": "page_up", "pagedown": "page_down", "up": "up", "down": "down", "left": "left", "right": "right",
}
for _i in range(1, 21):
    _COMBO_ALIASES[f"f{_i}"] = f"f{_i}"

_CODE_CHAR = re.compile(r"^(Key([A-Z])|Digit([0-9]))$")


class PynputBackend(ControlBackend):
    name = "pynput"

    def __init__(self):
        from pynput import keyboard, mouse

        self._kb = keyboard.Controller()
        self._mouse = mouse.Controller()
        self._Key = keyboard.Key
        self._KeyCode = keyboard.KeyCode
        self._Button = mouse.Button
        self._pressed_keys: set = set()
        self._pressed_buttons: set = set()

    # -- mouse ------------------------------------------------------------
    def mouse_move(self, x, y):
        self._mouse.position = (int(x), int(y))

    def _button(self, name: str):
        b = getattr(self._Button, name, None)
        if b is None and name == "back":
            b = getattr(self._Button, "x1", None)
        if b is None and name == "forward":
            b = getattr(self._Button, "x2", None)
        return b

    def mouse_button(self, button, down):
        b = self._button(button)
        if b is None:
            return
        if down:
            self._mouse.press(b)
            self._pressed_buttons.add(b)
        else:
            self._mouse.release(b)
            self._pressed_buttons.discard(b)

    def mouse_scroll(self, dx, dy):
        if dx or dy:
            self._mouse.scroll(int(dx), int(dy))

    def cursor_position(self):
        try:
            return self._mouse.position
        except Exception:
            return None

    # -- keyboard ---------------------------------------------------------
    def _resolve(self, key: str, code: str):
        name = _KEY_NAMES.get(key)
        if name:
            k = getattr(self._Key, name, None)
            if k is not None:
                return k
        modifiers_held = any(getattr(self._Key, n, None) in self._pressed_keys
                             for n in ("ctrl", "ctrl_l", "ctrl_r", "alt", "alt_l", "alt_r", "cmd", "cmd_l", "cmd_r"))
        m = _CODE_CHAR.match(code or "")
        if modifiers_held and m:
            # with Ctrl/Alt/Cmd held the browser's `key` may be garbage (e.g. Alt+a -> å on mac)
            return self._KeyCode.from_char((m.group(2) or m.group(3)).lower())
        if key and len(key) == 1:
            return self._KeyCode.from_char(key)
        if code and code.startswith("Numpad") and len(code) == 7 and code[-1].isdigit():
            return self._KeyCode.from_char(code[-1])
        return None

    def key(self, key, code, down):
        k = self._resolve(key, code)
        if k is None:
            return
        if down:
            self._kb.press(k)
            self._pressed_keys.add(k)
        else:
            self._kb.release(k)
            self._pressed_keys.discard(k)

    def type_text(self, text):
        self._kb.type(text)

    def combo(self, keys):
        resolved = []
        for raw in keys:
            n = _COMBO_ALIASES.get(raw.lower())
            k = getattr(self._Key, n, None) if n else None
            if k is None and len(raw) == 1:
                k = self._KeyCode.from_char(raw.lower())
            if k is not None:
                resolved.append(k)
        for k in resolved:
            self._kb.press(k)
            time.sleep(0.02)
        for k in reversed(resolved):
            self._kb.release(k)
            time.sleep(0.02)

    def release_all(self):
        for k in list(self._pressed_keys):
            try:
                self._kb.release(k)
            except Exception:
                pass
        self._pressed_keys.clear()
        for b in list(self._pressed_buttons):
            try:
                self._mouse.release(b)
            except Exception:
                pass
        self._pressed_buttons.clear()


class DemoBackend(ControlBackend):
    """Records events instead of injecting them (used by --demo and tests)."""

    name = "demo"

    def __init__(self, event_log: Optional[list] = None):
        self.event_log = event_log if event_log is not None else []
        self.pos = (100.0, 100.0)

    def _log(self, s):
        self.event_log.append(time.strftime("%H:%M:%S ") + s)
        del self.event_log[:-200]

    def mouse_move(self, x, y):
        self.pos = (x, y)

    def mouse_button(self, button, down):
        self._log(f"mouse {button} {'down' if down else 'up'} @ {int(self.pos[0])},{int(self.pos[1])}")

    def mouse_scroll(self, dx, dy):
        self._log(f"scroll dx={dx} dy={dy}")

    def key(self, key, code, down):
        self._log(f"key {'down' if down else 'up'}: {key!r} ({code})")

    def type_text(self, text):
        self._log(f"type: {text!r}")

    def combo(self, keys):
        self._log("combo: " + "+".join(keys))

    def release_all(self):
        self._log("release_all")

    def cursor_position(self):
        return self.pos


def make_backend(demo: bool = False, event_log: Optional[list] = None) -> ControlBackend:
    if demo:
        return DemoBackend(event_log)
    try:
        return PynputBackend()
    except Exception as e:  # pragma: no cover
        print(f"[control] 無法初始化滑鼠/鍵盤控制 ({e})，改用 demo 模式（只記錄不執行）")
        return DemoBackend(event_log)
