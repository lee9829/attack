@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Octo Automation - optional one-time setup
echo.
echo 보통은 [시작.vbs] 또는 [실행하기.bat] 만 더블클릭하면 됩니다.
echo 이 파일은 패키지를 미리 깔아 두고 싶을 때만 사용하세요.
echo.
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found.
  pause
  exit /b 1
)
python bootstrap.py --force
if errorlevel 1 (
  echo Setup failed.
  pause
  exit /b 1
)
echo.
echo Done. Now double-click 시작.vbs
pause
