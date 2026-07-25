# Pull latest main from GitHub and restart the Windows web process.
# Usage (on Windows server):
#   cd C:\apps\Octo-Google-Site-Automation
#   powershell -ExecutionPolicy Bypass -File .\deploy\deploy.ps1
$ErrorActionPreference = "Stop"

$AppDir = if ($env:APP_DIR) { $env:APP_DIR } else { Split-Path -Parent $PSScriptRoot }
$Branch = if ($env:DEPLOY_BRANCH) { $env:DEPLOY_BRANCH } else { "main" }
$Remote = if ($env:DEPLOY_REMOTE) { $env:DEPLOY_REMOTE } else { "origin" }
$Python = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { "python" }
$Port = if ($env:OCTO_PORT) { [int]$env:OCTO_PORT } else { 8787 }

Set-Location $AppDir
Write-Host "[deploy] dir=$AppDir branch=$Branch"

git fetch $Remote $Branch
git reset --hard "$Remote/$Branch"

if (Test-Path "requirements.txt") {
  Write-Host "[deploy] pip install"
  & $Python -m pip install -r requirements.txt --quiet
}

# Seed example files only if missing (never overwrite secrets)
& $Python -c @"
from pathlib import Path
import shutil
root = Path('.')
pairs = [
    ('config.example.json', 'config.json'),
    ('proxies.example.txt', 'proxies.txt'),
    ('accounts.example.csv', 'accounts.csv'),
    ('domains.example.txt', 'domains.txt'),
    ('keywords.example.txt', 'keywords.txt'),
]
for src, dst in pairs:
    s, d = root / src, root / dst
    if s.exists() and not d.exists():
        shutil.copy(s, d)
        print(f'[deploy] created {dst}')
"@

# Stop process listening on web port (best-effort)
try {
  $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  foreach ($c in $conns) {
    if ($c.OwningProcess) {
      Write-Host "[deploy] stop PID $($c.OwningProcess) on port $Port"
      Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
    }
  }
} catch {
  Write-Host "[deploy] port stop skipped: $_"
}

Start-Sleep -Seconds 1

$env:OCTO_HOST = if ($env:OCTO_HOST) { $env:OCTO_HOST } else { "0.0.0.0" }
$env:OCTO_PORT = "$Port"
$env:OCTO_NO_BROWSER = "1"

$logDir = Join-Path $AppDir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$outLog = Join-Path $logDir "web.out.log"
$errLog = Join-Path $logDir "web.err.log"

Write-Host "[deploy] start web host=$($env:OCTO_HOST) port=$Port"
Start-Process -FilePath $Python `
  -ArgumentList @("main.py", "--web", "--no-browser", "--host", $env:OCTO_HOST, "--port", "$Port") `
  -WorkingDirectory $AppDir `
  -WindowStyle Hidden `
  -RedirectStandardOutput $outLog `
  -RedirectStandardError $errLog

$sha = (git rev-parse --short HEAD).Trim()
Write-Host "[deploy] OK $sha  -> http://$($env:OCTO_HOST):$Port/"
