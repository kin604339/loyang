"""Clipboard, power actions, system info, shell commands - all platform aware."""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import time

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"


# -- clipboard -------------------------------------------------------------
def get_clipboard() -> str:
    try:
        import pyperclip

        return pyperclip.paste() or ""
    except Exception:
        pass
    try:
        if IS_MAC:
            return subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=3).stdout
        if IS_WIN:
            return subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                                  capture_output=True, text=True, timeout=5).stdout.rstrip("\r\n")
        return subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True, text=True,
                              timeout=3).stdout
    except Exception:
        return ""


def set_clipboard(text: str) -> bool:
    try:
        import pyperclip

        pyperclip.copy(text)
        return True
    except Exception:
        pass
    try:
        if IS_MAC:
            subprocess.run(["pbcopy"], input=text, text=True, timeout=3)
            return True
        if IS_WIN:
            subprocess.run(["clip"], input=text, text=True, timeout=3)
            return True
        subprocess.run(["xclip", "-selection", "clipboard"], input=text, text=True, timeout=3)
        return True
    except Exception:
        return False


# -- power -----------------------------------------------------------------
_POWER = {
    "lock": {
        "win": ["rundll32.exe", "user32.dll,LockWorkStation"],
        "mac": ["osascript", "-e", 'tell application "System Events" to keystroke "q" using {control down, command down}'],
        "linux": ["sh", "-c", "loginctl lock-session || xdg-screensaver lock"],
    },
    "sleep": {
        "win": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
        "mac": ["pmset", "sleepnow"],
        "linux": ["systemctl", "suspend"],
    },
    "shutdown": {
        "win": ["shutdown", "/s", "/t", "5"],
        "mac": ["osascript", "-e", 'tell application "System Events" to shut down'],
        "linux": ["systemctl", "poweroff"],
    },
    "restart": {
        "win": ["shutdown", "/r", "/t", "5"],
        "mac": ["osascript", "-e", 'tell application "System Events" to restart'],
        "linux": ["systemctl", "reboot"],
    },
    "logout": {
        "win": ["shutdown", "/l"],
        "mac": ["osascript", "-e", 'tell application "System Events" to log out'],
        "linux": ["sh", "-c", "loginctl terminate-user $USER"],
    },
}


def power_action(action: str, dry_run: bool = False) -> str:
    if action not in _POWER:
        raise ValueError("未知的動作")
    key = "win" if IS_WIN else "mac" if IS_MAC else "linux"
    cmd = _POWER[action][key]
    if dry_run:
        return "demo: " + " ".join(cmd)
    subprocess.Popen(cmd)
    return "ok"


# -- system info -----------------------------------------------------------
def system_info() -> dict:
    info = {
        "hostname": platform.node(),
        "os": f"{platform.system()} {platform.release()}",
        "machine": platform.machine(),
        "python": platform.python_version(),
        "user": os.environ.get("USERNAME") or os.environ.get("USER") or "",
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        import psutil

        info["cpu_percent"] = psutil.cpu_percent(interval=None)
        info["cpu_count"] = psutil.cpu_count()
        vm = psutil.virtual_memory()
        info["mem_total"] = vm.total
        info["mem_used"] = vm.used
        info["mem_percent"] = vm.percent
        info["uptime"] = int(time.time() - psutil.boot_time())
        disks = []
        for p in psutil.disk_partitions(all=False):
            try:
                u = psutil.disk_usage(p.mountpoint)
                disks.append({"mount": p.mountpoint, "total": u.total, "used": u.used, "percent": u.percent})
            except Exception:
                continue
        info["disks"] = disks[:8]
        try:
            b = psutil.sensors_battery()
            if b:
                info["battery"] = {"percent": b.percent, "plugged": b.power_plugged}
        except Exception:
            pass
    except Exception:
        pass
    return info


def run_shell(cmd: str, timeout: int = 30) -> dict:
    try:
        if IS_WIN:
            p = subprocess.run(["cmd", "/c", cmd], capture_output=True, timeout=timeout)
        else:
            p = subprocess.run(cmd, shell=True, capture_output=True, timeout=timeout)
        enc = "utf-8"
        if IS_WIN:
            try:
                import ctypes

                enc = f"cp{ctypes.windll.kernel32.GetConsoleOutputCP()}"
            except Exception:
                enc = "cp950"
        out = p.stdout.decode(enc, "replace") + p.stderr.decode(enc, "replace")
        return {"code": p.returncode, "output": out}
    except subprocess.TimeoutExpired:
        return {"code": -1, "output": f"逾時（{timeout}s）"}
    except Exception as e:
        return {"code": -1, "output": str(e)}


def open_path(path: str) -> None:
    """Open a file/folder with the OS default application."""
    if IS_WIN:
        os.startfile(path)  # type: ignore[attr-defined]
    elif IS_MAC:
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
