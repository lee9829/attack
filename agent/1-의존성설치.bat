@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
echo [1/2] Python 패키지 설치 중...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo 실패: Python 3.10+ 가 설치되어 있는지 확인하세요.
  echo https://www.python.org/downloads/
  pause
  exit /b 1
)
echo [2/2] Playwright 브라우저...
python -m playwright install chromium
echo.
echo 설치 완료. 이제 Octo Browser 실행 후 2-에이전트실행.bat 을 실행하세요.
pause
