#!/usr/bin/env bash
# Pull latest main from GitHub and restart the web service.
# Usage (on server):
#   cd /opt/octo-google-site-automation && bash deploy/deploy.sh
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
BRANCH="${DEPLOY_BRANCH:-main}"
REMOTE="${DEPLOY_REMOTE:-origin}"
SERVICE_NAME="${SERVICE_NAME:-octo-web}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$APP_DIR"

echo "[deploy] dir=$APP_DIR branch=$BRANCH"

# Keep local secrets (config.json, accounts.csv, ...) — never overwrite from git
git fetch "$REMOTE" "$BRANCH"
git reset --hard "$REMOTE/$BRANCH"

if [[ -f requirements.txt ]]; then
  echo "[deploy] pip install"
  "$PYTHON_BIN" -m pip install -r requirements.txt --quiet
fi

# Ensure example templates exist as runtime files (do not clobber existing secrets)
"$PYTHON_BIN" - <<'PY' || true
from pathlib import Path
import shutil
root = Path(".")
pairs = [
    ("config.example.json", "config.json"),
    ("proxies.example.txt", "proxies.txt"),
    ("accounts.example.csv", "accounts.csv"),
    ("domains.example.txt", "domains.txt"),
    ("keywords.example.txt", "keywords.txt"),
]
for src, dst in pairs:
    s, d = root / src, root / dst
    if s.exists() and not d.exists():
        shutil.copy(s, d)
        print(f"[deploy] created {dst}")
PY

if command -v systemctl >/dev/null 2>&1; then
  if systemctl list-unit-files | grep -q "^${SERVICE_NAME}\\.service"; then
    echo "[deploy] restart ${SERVICE_NAME}"
    sudo systemctl restart "$SERVICE_NAME"
    sudo systemctl --no-pager --full status "$SERVICE_NAME" || true
  else
    echo "[deploy] systemd unit ${SERVICE_NAME}.service not found — start manually:"
    echo "  OCTO_HOST=0.0.0.0 OCTO_NO_BROWSER=1 $PYTHON_BIN main.py --web"
  fi
else
  echo "[deploy] systemctl unavailable — code updated; restart the process yourself"
fi

echo "[deploy] OK $(git rev-parse --short HEAD)"
