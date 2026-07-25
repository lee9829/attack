@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [Octo] Web UI starting...
python main.py --web
if errorlevel 1 (
  echo.
  echo Failed. Install Python 3.10+ and run: python -m pip install -r requirements.txt
  pause
)
