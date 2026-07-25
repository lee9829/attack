@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "%~dp0start.vbs" (
  start "" wscript.exe //nologo "%~dp0start.vbs"
  exit /b 0
)
if exist "%~dp0시작.vbs" (
  start "" wscript.exe //nologo "%~dp0시작.vbs"
  exit /b 0
)
where pythonw >nul 2>nul
if not errorlevel 1 (
  start "" pythonw main.py
  exit /b 0
)
python main.py
if errorlevel 1 pause
