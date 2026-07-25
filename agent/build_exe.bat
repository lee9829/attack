@echo off
chcp 65001 >nul
cd /d "%~dp0\.."

echo [build] install pyinstaller...
python -m pip install -q pyinstaller requests playwright

echo [build] write embedded_config from server settings...
python agent\write_embedded_config.py
if errorlevel 1 (
  echo failed to write embedded config
  pause
  exit /b 1
)

echo [build] PyInstaller one-file EXE...
python -m PyInstaller --noconfirm --clean --onefile --console ^
  --name OctoAgent ^
  --paths . ^
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

copy /Y "dist\OctoAgent.exe" "agent\OctoAgent.exe" >nul
copy /Y "dist\OctoAgent.exe" "%USERPROFILE%\Desktop\OctoAgent.exe" >nul

echo.
echo ========================================
echo  DONE
echo  - agent\OctoAgent.exe
echo  - Desktop\OctoAgent.exe
echo  상대방: Octo 켠 뒤 exe 더블클릭만
echo ========================================
pause
