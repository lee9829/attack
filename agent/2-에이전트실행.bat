@echo off
chcp 65001 >nul
cd /d "%~dp0\.."

REM load agent\config.env if present
if exist "%~dp0config.env" (
  for /f "usebackq tokens=1,* delims==" %%A in ("%~dp0config.env") do (
    if not "%%A"=="" if not "%%A:~0,1%"=="#" set "%%A=%%B"
  )
)

if "%AGENT_SERVER%"=="" set AGENT_SERVER=http://66.29.149.197:8787
if "%AGENT_NAME%"=="" set AGENT_NAME=%COMPUTERNAME%
if "%AGENT_USER%"=="" set AGENT_USER=admin
if "%AGENT_PASS%"=="" set AGENT_PASS=

echo.
echo ========================================
echo   Octo 연동 에이전트 (상대방 PC용)
echo ========================================
echo   사이트: %AGENT_SERVER%
echo   이름:   %AGENT_NAME%
echo.
echo   1) Octo Browser 를 실행하고 로그인 하세요
echo   2) 이 창은 닫지 마세요 (연결 유지)
echo   3) 사이트에서 엔진=agent 로 [시작]
echo ========================================
echo.

python agent\windows_agent.py
if errorlevel 1 (
  echo.
  echo 오류. 1-의존성설치.bat 을 먼저 실행했는지, config.env 비밀번호를 확인하세요.
  pause
)
