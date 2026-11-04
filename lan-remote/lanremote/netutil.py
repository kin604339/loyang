"""Network helpers: local IPs, MAC, UDP discovery of other LAN Remote agents, Wake-on-LAN."""
from __future__ import annotations

import json
import socket
import threading
import time
import uuid
from typing import Callable

DISCOVERY_PORT = 7789
_MAGIC = "LANREMOTE1"


def local_ips() -> list[str]:
    ips: list[str] = []
    try:
        import psutil

        for name, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if a.family == socket.AF_INET and not a.address.startswith("127.") and not a.address.startswith("169.254."):
                    ips.append(a.address)
    except Exception:
        pass
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
            s.close()
        except Exception:
            pass
    if not ips:
        try:
            for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
                if not ip.startswith("127."):
                    ips.append(ip)
        except Exception:
            pass
    # de-dup, keep order
    seen = set()
    return [ip for ip in ips if not (ip in seen or seen.add(ip))]


def broadcast_addresses() -> list[str]:
    out = ["255.255.255.255"]
    try:
        import psutil

        for name, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if a.family == socket.AF_INET and a.broadcast and a.broadcast not in out:
                    out.append(a.broadcast)
    except Exception:
        pass
    return out


def mac_address() -> str:
    n = uuid.getnode()
    return ":".join(f"{(n >> (8 * i)) & 0xFF:02x}" for i in reversed(range(6)))


def send_wol(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> None:
    clean = mac.replace(":", "").replace("-", "").replace(".", "")
    if len(clean) != 12:
        raise ValueError("MAC 格式錯誤")
    packet = b"\xff" * 6 + bytes.fromhex(clean) * 16
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.sendto(packet, (broadcast, port))
    s.close()


class Discovery:
    """Announces this agent over UDP broadcast and keeps a table of peers that do the same."""

    def __init__(self, info_provider: Callable[[], dict], port: int = DISCOVERY_PORT, interval: float = 3.0,
                 ttl: float = 12.0):
        self.info_provider = info_provider
        self.port = port
        self.interval = interval
        self.ttl = ttl
        self.peers: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.my_id = info_provider().get("id")

    def start(self):
        self._threads = [
            threading.Thread(target=self._announce_loop, name="lr-announce", daemon=True),
            threading.Thread(target=self._listen_loop, name="lr-listen", daemon=True),
        ]
        for t in self._threads:
            t.start()

    def stop(self):
        self._stop.set()

    def _announce_loop(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while not self._stop.is_set():
            try:
                info = dict(self.info_provider())
                info["magic"] = _MAGIC
                data = json.dumps(info).encode()
                for b in broadcast_addresses():
                    try:
                        s.sendto(data, (b, self.port))
                    except OSError:
                        pass
            except Exception:
                pass
            self._stop.wait(self.interval)
        s.close()

    def _listen_loop(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        try:
            s.bind(("", self.port))
        except OSError as e:
            print(f"[discovery] 無法監聽 UDP {self.port}: {e}（仍可手動輸入 IP 連線）")
            return
        s.settimeout(1.0)
        while not self._stop.is_set():
            try:
                data, addr = s.recvfrom(4096)
            except socket.timeout:
                self._expire()
                continue
            except OSError:
                break
            try:
                info = json.loads(data.decode())
            except Exception:
                continue
            if info.get("magic") != _MAGIC or info.get("id") == self.my_id:
                continue
            info["ip"] = addr[0]
            info["seen"] = time.time()
            info.pop("magic", None)
            with self._lock:
                self.peers[info["id"]] = info
            self._expire()
        s.close()

    def _expire(self):
        now = time.time()
        with self._lock:
            for k in [k for k, v in self.peers.items() if now - v["seen"] > self.ttl]:
                del self.peers[k]

    def list(self) -> list[dict]:
        with self._lock:
            return sorted(self.peers.values(), key=lambda p: p.get("name", ""))
