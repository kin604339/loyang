@echo off
chcp 65001 >nul
title LAN Remote 安裝
cd /d "%~dp0"
echo ==== LAN Remote 安裝 (Windows) ====
where py >nul 2>nul && set PY=py -3
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY (
  echo 找不到 Python。請先到 https://www.python.org/downloads/ 安裝 Python 3.9 以上，
  echo 安裝時記得勾選 "Add python.exe to PATH"，然後再執行一次 install.bat
  pause
  exit /b 1
)
%PY% -m venv venv || (echo 建立虛擬環境失敗 & pause & exit /b 1)
call venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt || (echo 安裝套件失敗，請確認網路連線 & pause & exit /b 1)
echo.
echo 正在新增 Windows 防火牆規則（允許區網連入 7788/7789）...
netsh advfirewall firewall delete rule name="LAN Remote" >nul 2>nul
netsh advfirewall firewall add rule name="LAN Remote" dir=in action=allow protocol=TCP localport=7788 profile=private,domain >nul 2>nul
netsh advfirewall firewall add rule name="LAN Remote" dir=in action=allow protocol=UDP localport=7789 profile=private,domain >nul 2>nul
if errorlevel 1 echo （防火牆規則需要系統管理員權限，若略過，第一次啟動時 Windows 會跳出詢問，請按「允許存取」）
echo.
echo 安裝完成！之後雙擊 start.bat 即可啟動。
echo 若要開機自動啟動：把 start.bat 的捷徑放進「shell:startup」資料夾。
pause
