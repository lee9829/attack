@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Build OctoAutomation.exe (optional)
echo.
echo [Optional] Creates OctoAutomation.exe so you can double-click without Python.
echo Most users can just use 시작.vbs instead.
echo.
where python >nul 2>nul
if errorlevel 1 (
  echo Python not found.
  pause
  exit /b 1
)

python -m pip install --upgrade pyinstaller playwright requests --quiet
python -m playwright install chromium

if exist dist\OctoAutomation.exe del /f /q dist\OctoAutomation.exe

python -m PyInstaller --noconfirm --clean --windowed --onefile ^
  --name OctoAutomation ^
  --add-data "config.example.json;." ^
  --add-data "proxies.example.txt;." ^
  --add-data "accounts.example.csv;." ^
  --hidden-import playwright ^
  --hidden-import greenlet ^
  --collect-all playwright ^
  main.py

if exist dist\OctoAutomation.exe (
  echo.
  echo OK: dist\OctoAutomation.exe
  echo Copy config.json / proxies next to the exe if needed.
) else (
  echo Build failed.
)
pause
