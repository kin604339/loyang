"""用 PyInstaller 打包成單一執行檔（Windows: LanRemote.exe / macOS: LanRemote.app）。

    python -m pip install pyinstaller
    python build_exe.py

輸出在 dist/ 資料夾。把它複製到別台電腦就能直接執行，不需要安裝 Python。
"""
import os
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))
sep = ";" if sys.platform.startswith("win") else ":"
cmd = [
    sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
    "--name", "LanRemote",
    "--add-data", f"{os.path.join(here, 'lanremote', 'static')}{sep}lanremote/static",
    "--hidden-import", "pynput.keyboard._win32", "--hidden-import", "pynput.mouse._win32",
    "--hidden-import", "pynput.keyboard._darwin", "--hidden-import", "pynput.mouse._darwin",
    "--hidden-import", "pynput.keyboard._xorg", "--hidden-import", "pynput.mouse._xorg",
    "--collect-all", "av", "--collect-submodules", "pygame", "--collect-data", "pygame",
    "--hidden-import", "cryptography", "--hidden-import", "numpy",
    os.path.join(here, "run.py"),
]
print(" ".join(cmd))
sys.exit(subprocess.call(cmd, cwd=here))
