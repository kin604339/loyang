#!/bin/bash
# LAN Remote 安裝 (macOS / Linux) - 在 Mac 上可直接雙擊執行
cd "$(dirname "$0")"
echo "==== LAN Remote 安裝 ===="
if ! command -v python3 >/dev/null 2>&1; then
  echo "找不到 python3。Mac 請先執行:  xcode-select --install"
  echo "或到 https://www.python.org/downloads/macos/ 安裝 Python 3"
  read -r -p "按 Enter 結束"; exit 1
fi
python3 -m venv venv || { echo "建立虛擬環境失敗"; read -r -p "按 Enter 結束"; exit 1; }
./venv/bin/python -m pip install --upgrade pip >/dev/null
./venv/bin/python -m pip install -r requirements.txt || { echo "安裝套件失敗，請確認網路"; read -r -p "按 Enter 結束"; exit 1; }
chmod +x start.command
echo
echo "安裝完成！之後雙擊 start.command 即可啟動。"
if [ "$(uname)" = "Darwin" ]; then
  echo
  echo "⚠ macOS 需要授權（只需做一次）："
  echo "  系統設定 → 隱私權與安全性 → 「螢幕錄製」與「輔助使用」"
  echo "  把「終端機」(Terminal) 加進去並打勾，否則對方只會看到黑畫面 / 無法控制。"
fi
read -r -p "按 Enter 結束"
