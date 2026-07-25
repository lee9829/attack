#!/usr/bin/env bash
# First-time Linux server bootstrap (run once as root or with sudo).
# Example:
#   curl -sL ... | bash
#   OR: bash deploy/setup-server.sh
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/lee9829/attack.git}"
APP_DIR="${APP_DIR:-/opt/octo-google-site-automation}"
APP_USER="${APP_USER:-www-data}"
BRANCH="${DEPLOY_BRANCH:-main}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/setup-server.sh"
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y git python3 python3-pip python3-venv

mkdir -p "$(dirname "$APP_DIR")"
if [[ ! -d "$APP_DIR/.git" ]]; then
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
else
  git -C "$APP_DIR" fetch origin "$BRANCH"
  git -C "$APP_DIR" reset --hard "origin/$BRANCH"
fi

id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

sudo -u "$APP_USER" python3 -m pip install --user -r "$APP_DIR/requirements.txt"

# Seed runtime files from examples if missing
sudo -u "$APP_USER" python3 - <<PY
from pathlib import Path
import shutil
root = Path("$APP_DIR")
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
        print("created", dst)
PY

UNIT_SRC="$APP_DIR/deploy/octo-web.service"
UNIT_DST="/etc/systemd/system/octo-web.service"
sed "s|/opt/octo-google-site-automation|$APP_DIR|g; s|User=www-data|User=$APP_USER|g; s|Group=www-data|Group=$APP_USER|g" \
  "$UNIT_SRC" > "$UNIT_DST"

# Prefer user-local python bin if pip --user was used
USER_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
if [[ -x "$USER_HOME/.local/bin/python3" ]]; then
  :
fi

systemctl daemon-reload
systemctl enable --now octo-web
systemctl --no-pager --full status octo-web || true

echo
echo "[setup] OK"
echo "  App:     $APP_DIR"
echo "  Service: octo-web"
echo "  URL:     http://SERVER_IP:8787/"
echo "  Deploy:  cd $APP_DIR && bash deploy/deploy.sh"
echo
echo "GitHub Actions 자동 배포를 쓰려면 리포 Secrets에 추가:"
echo "  DEPLOY_HOST, DEPLOY_USER, DEPLOY_SSH_KEY, DEPLOY_PATH=$APP_DIR"
