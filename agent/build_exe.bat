@echo off
chcp 65001 >nul
cd /d "%~dp0\.."

echo [build] install pyinstaller...
python -m pip install -q pyinstaller requests playwright pywin32

echo [build] write embedded_config from server settings...
python agent\write_embedded_config.py
if errorlevel 1 (
  echo failed to write embedded config
  pause
  exit /b 1
)

echo [build] PyInstaller one-file EXE (version info for less SmartScreen noise)...
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name OctoAgent ^
  --paths . ^
  --version-file agent\version_info.txt ^
  --hidden-import src ^
  --hidden-import src.runner ^
  --hidden-import src.automation ^
  --hidden-import src.octo_client ^
  --hidden-import src.proxy_manager ^
  --hidden-import src.logutil ^
  --hidden-import src.bulk_targets ^
  --hidden-import src.traffic_metrics ^
  --hidden-import src.own_site_ops ^
  --hidden-import agent.embedded_config ^
  --collect-submodules src ^
  agent\windows_agent.py

if errorlevel 1 (
  echo build failed
  pause
  exit /b 1
)

if not exist "dist\OctoAgent.exe" (
  echo exe not found
  pause
  exit /b 1
)

mkdir downloads 2>nul
copy /Y "dist\OctoAgent.exe" "agent\OctoAgent.exe" >nul
copy /Y "dist\OctoAgent.exe" "downloads\OctoAgent.exe" >nul
copy /Y "dist\OctoAgent.exe" "%USERPROFILE%\Desktop\OctoAgent.exe" >nul
copy /Y "agent\실행허용-의심파일해결.bat" "%USERPROFILE%\Desktop\실행허용-의심파일해결.bat" >nul 2>nul

powershell -NoProfile -Command "Unblock-File -Path '%USERPROFILE%\Desktop\OctoAgent.exe' -ErrorAction SilentlyContinue; Unblock-File -Path 'dist\OctoAgent.exe' -ErrorAction SilentlyContinue"

echo.
echo ========================================
echo  DONE
echo  - downloads\OctoAgent.exe  (웹 업로드용)
echo  - Desktop\OctoAgent.exe
echo  SmartScreen: 추가 정보 → 그래도 실행
echo  또는 Desktop\실행허용-의심파일해결.bat
echo ========================================
pause
