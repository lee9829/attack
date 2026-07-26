@echo off
chcp 65001 >nul
echo ============================================
echo  OctoAgent 의심 파일 경고 해제 (Windows)
echo ============================================
echo.
echo 1) 바탕화면 OctoAgent.exe 차단 해제 중...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Unblock-File -LiteralPath \"$env:USERPROFILE\Desktop\OctoAgent.exe\" -ErrorAction SilentlyContinue; Get-Item \"$env:USERPROFILE\Desktop\OctoAgent.exe\" -ErrorAction SilentlyContinue | Select-Object FullName,Length"
echo.
echo 2) 다운로드 폴더 복사본도 해제...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path $env:USERPROFILE\Downloads -Filter OctoAgent*.exe -ErrorAction SilentlyContinue | ForEach-Object { Unblock-File -LiteralPath $_.FullName; Write-Host Unblocked $_.FullName }"
echo.
echo 3) 실행 방법
echo    - 여전히 경고 시: 추가 정보 → 그래도 실행
echo    - Octo Browser 켠 뒤 OctoAgent.exe 실행
echo.
pause
start "" "%USERPROFILE%\Desktop\OctoAgent.exe"
