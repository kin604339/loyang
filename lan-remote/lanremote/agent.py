"""Entry point: loads config, starts the server (and the optional Tk status window)."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import platform
import secrets
import subprocess
import sys
import threading

from . import __version__
from .server import RemoteServer

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".lanremote")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

DEFAULTS = {
    "name": platform.node() or "my-computer",
    "pin": "",
    "port": 7788,
    "require_accept": False,
    "view_only": False,
    "allow_shell": False,
    "receive_dir": "",
    "fs_root": "",
    "https": False,
    "encoder": "",
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[config] 讀取設定失敗: {e}")
    return cfg


def save_config(cfg: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def new_pin() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(6))


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="lanremote", description="LAN Remote - 區域網路遠端桌面")
    p.add_argument("--port", type=int, help="HTTP 埠號（預設 7788）")
    p.add_argument("--pin", help="連線 PIN 碼（預設自動產生並存檔）")
    p.add_argument("--name", help="這台電腦顯示的名稱")
    p.add_argument("--no-gui", action="store_true", help="不開啟狀態視窗，只在終端機執行")
    p.add_argument("--demo", action="store_true", help="示範模式：用假畫面、不真的控制滑鼠鍵盤")
    p.add_argument("--view-only", action="store_true", help="唯讀模式：只能看畫面不能操作")
    p.add_argument("--require-accept", action="store_true", help="每次連線都要在主機端按「接受」")
    p.add_argument("--allow-shell", action="store_true", help="允許遠端執行指令")
    p.add_argument("--receive-dir", help="接收檔案的資料夾")
    p.add_argument("--fs-root", help="限制檔案管理只能存取此資料夾")
    p.add_argument("--reset-pin", action="store_true", help="重新產生 PIN 碼")
    p.add_argument("--https", action="store_true", help="用 HTTPS（自簽憑證；瀏覽器才能用 H.264 硬體解碼）")
    p.add_argument("--encoder", help="指定 H.264 編碼器，例如 libx264 / h264_videotoolbox / h264_nvenc")
    p.add_argument("--connect", metavar="URL", help="直接開啟原生連線視窗連到對方，例如 --connect 192.168.1.23")
    p.add_argument("--version", action="version", version=f"lanremote {__version__}")
    return p.parse_args(argv)


def build_settings(args) -> dict:
    cfg = load_config()
    if args.port:
        cfg["port"] = args.port
    if args.name:
        cfg["name"] = args.name
    if args.pin:
        cfg["pin"] = args.pin
    if args.reset_pin or not cfg.get("pin"):
        cfg["pin"] = new_pin()
    if args.view_only:
        cfg["view_only"] = True
    if args.require_accept:
        cfg["require_accept"] = True
    if args.allow_shell:
        cfg["allow_shell"] = True
    if args.receive_dir:
        cfg["receive_dir"] = args.receive_dir
    if args.fs_root:
        cfg["fs_root"] = args.fs_root
    if args.https:
        cfg["https"] = True
    if args.encoder:
        cfg["encoder"] = args.encoder
    if not cfg.get("id"):
        cfg["id"] = secrets.token_hex(4)
    save_config(cfg)
    return cfg


def banner(server: RemoteServer):
    from .netutil import local_ips

    ips = local_ips() or ["<這台電腦的 IP>"]
    print("=" * 56)
    print(f"  LAN Remote v{__version__}   名稱: {server.name}")
    print(f"  PIN 碼: {server.settings['pin']}")
    for ip in ips:
        print(f"  在別台電腦開啟:  {server.scheme}://{ip}:{server.port}/")
    if server.demo:
        print("  (示範模式：畫面為模擬，滑鼠鍵盤不會真的被控制)")
    print("  設定檔:", CONFIG_PATH)
    print("=" * 56)


def run_headless(server: RemoteServer):
    async def main():
        await server.start()
        banner(server)
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass
        finally:
            await server.stop()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n已停止")


def run_with_gui(server: RemoteServer):
    try:
        from . import gui
    except Exception as e:
        print(f"[gui] 無法載入視窗 ({e})，改用純終端機模式")
        return run_headless(server)

    loop = asyncio.new_event_loop()
    ready = threading.Event()
    error: list[BaseException] = []

    def worker():
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(server.start())
        except BaseException as e:  # port in use etc.
            error.append(e)
            ready.set()
            return
        ready.set()
        loop.run_forever()
        loop.run_until_complete(server.stop())

    t = threading.Thread(target=worker, name="lr-server", daemon=True)
    t.start()
    ready.wait(10)
    if error:
        print(f"啟動失敗: {error[0]}")
        try:
            gui.show_error(f"啟動失敗：{error[0]}\n\n可能是埠號 {server.port} 已被使用，請改用 --port。")
        except Exception:
            pass
        return
    banner(server)
    try:
        gui.run(server, loop)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(5)


def launch_viewer(url: str, pin: str = "", name: str = "") -> subprocess.Popen:
    """Open the native viewer window in a separate process (works both from source and from the packaged exe)."""
    env = dict(os.environ)
    if pin:
        env["LANREMOTE_PIN"] = pin
    if name:
        env["LANREMOTE_VIEWER_NAME"] = name
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--connect", url]
    else:
        cmd = [sys.executable, "-m", "lanremote", "--connect", url]
    return subprocess.Popen(cmd, env=env)


def main(argv=None):
    args = parse_args(argv)
    if args.connect:
        logging.basicConfig(level=logging.INFO)
        from . import viewer

        sys.exit(viewer.main(args.connect, args.pin or os.environ.get("LANREMOTE_PIN"), args.name))
    if sys.stdout is None or sys.stderr is None:  # pythonw / packaged .exe without console
        os.makedirs(CONFIG_DIR, exist_ok=True)
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                            filename=os.path.join(CONFIG_DIR, "lanremote.log"), encoding="utf-8")
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = build_settings(args)
    if settings.get("encoder"):
        from . import video

        video.set_preferred(settings["encoder"])
    server = RemoteServer(settings, demo=args.demo)
    if args.no_gui or os.environ.get("LANREMOTE_NO_GUI"):
        run_headless(server)
    else:
        run_with_gui(server)


if __name__ == "__main__":
    main()
