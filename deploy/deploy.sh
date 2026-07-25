#!/usr/bin/env bash
# Pull latest main from GitHub and restart the web service.
# Usage (on server):
#   bash /opt/octo-google-site-automation/deploy/deploy.sh
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
BRANCH="${DEPLOY_BRANCH:-main}"
REMOTE="${DEPLOY_REMOTE:-origin}"
SERVICE_NAME="${SERVICE_NAME:-octo-web}"
APP_USER="${APP_USER:-octo}"

cd "$APP_DIR"
echo "[deploy] dir=$APP_DIR branch=$BRANCH"

git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
git fetch "$REMOTE" "$BRANCH"
git reset --hard "$REMOTE/$BRANCH"

if [[ -x "$APP_DIR/.venv/bin/pip" ]]; then
  echo "[deploy] pip (venv)"
  "$APP_DIR/.venv/bin/pip" install -r requirements.txt --quiet
elif command -v python3 >/dev/null 2>&1; then
  echo "[deploy] pip (system)"
  python3 -m pip install -r requirements.txt --quiet || true
fi

# Seed example templates only when runtime files are missing (never overwrite secrets)
for pair in \
  config.example.json:config.json \
  proxies.example.txt:proxies.txt \
  accounts.example.csv:accounts.csv \
  domains.example.txt:domains.txt \
  keywords.example.txt:keywords.txt
do
  src="${pair%%:*}"
  dst="${pair##*:}"
  if [[ -f "$src" && ! -f "$dst" ]]; then
    cp "$src" "$dst"
    echo "[deploy] created $dst"
  fi
done

if id "$APP_USER" >/dev/null 2>&1; then
  chown -R "$APP_USER:$APP_USER" "$APP_DIR" || true
fi

if command -v systemctl >/dev/null 2>&1; then
  if systemctl list-unit-files | grep -q "^${SERVICE_NAME}\\.service"; then
    echo "[deploy] restart ${SERVICE_NAME}"
    systemctl restart "$SERVICE_NAME"
    systemctl --no-pager --full status "$SERVICE_NAME" || true
  else
    echo "[deploy] unit ${SERVICE_NAME}.service not found"
  fi
else
  echo "[deploy] systemctl unavailable — restart the process manually"
fi

echo "[deploy] OK $(git rev-parse --short HEAD)"
