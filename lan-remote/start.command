#!/bin/bash
cd "$(dirname "$0")"
if [ ! -x venv/bin/python ]; then echo "尚未安裝，請先執行 install.command"; read -r -p "按 Enter 結束"; exit 1; fi
exec ./venv/bin/python -m lanremote "$@"
