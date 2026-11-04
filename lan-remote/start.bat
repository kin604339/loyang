@echo off
chcp 65001 >nul
title LAN Remote
cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
  echo 尚未安裝，請先執行 install.bat
  pause
  exit /b 1
)
venv\Scripts\python.exe -m lanremote %*
if errorlevel 1 pause
