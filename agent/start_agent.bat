@echo off
chcp 65001 >nul
cd /d "%~dp0\.."

set AGENT_SERVER=http://66.29.149.197:8787
set AGENT_NAME=%COMPUTERNAME%
set AGENT_USER=admin
REM 웹 로그인 비밀번호 (Octo-Web-Login.txt 참고)
if "%AGENT_PASS%"=="" set AGENT_PASS=EmLnypbe74mwdorZgr

echo.
echo [Agent] Server=%AGENT_SERVER%
echo [Agent] Octo Browser 를 먼저 실행·로그인 하세요.
echo.

python -c "import requests" 2>nul
if errorlevel 1 (
  python -m pip install requests -q
)

python agent\windows_agent.py
if errorlevel 1 pause
