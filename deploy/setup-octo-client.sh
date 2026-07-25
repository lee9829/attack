#!/usr/bin/env bash
# Install Octo Browser Linux client (headless + Xvfb) for Local API on :58888
# Run as root on Ubuntu:  sudo bash deploy/setup-octo-client.sh
set -euo pipefail

OCTO_HOME="${OCTO_HOME:-/opt/octobrowser}"
OCTO_USER="${OCTO_USER:-octo}"
DISPLAY_NUM="${DISPLAY_NUM:-99}"
TAR_URL="${OCTO_TAR_URL:-https://binaries.octobrowser.net/releases/installer/OctoBrowser.linux.tar.gz}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/setup-octo-client.sh"
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y \
  curl ca-certificates xvfb libgl1 libglib2.0-0 libgles2 libegl1 \
  libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
  libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
  libgbm1 libasound2t64 libasound2 libpango-1.0-0 libcairo2 \
  fonts-liberation fonts-noto-cjk unzip fuse libfuse2t64 libfuse2 \
  2>/dev/null || apt-get install -y \
  curl ca-certificates xvfb libgl1 libglib2.0-0 libgles2 libegl1 \
  libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
  libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
  libgbm1 libasound2 libpango-1.0-0 libcairo2 \
  fonts-liberation fonts-noto-cjk unzip

id -u "$OCTO_USER" >/dev/null 2>&1 || useradd --system --create-home --home-dir "/home/$OCTO_USER" --shell /bin/bash "$OCTO_USER"

mkdir -p "$OCTO_HOME"
chown -R "$OCTO_USER:$OCTO_USER" "$OCTO_HOME"

if [[ ! -x "$OCTO_HOME/OctoBrowser.AppImage" && ! -x "$OCTO_HOME/squashfs-root/AppRun" ]]; then
  echo "[octo] downloading client..."
  curl -fsSL "$TAR_URL" -o /tmp/octo-browser.tar.gz
  sudo -u "$OCTO_USER" tar -xzf /tmp/octo-browser.tar.gz -C "$OCTO_HOME"
  # find appimage
  APPIMG=$(find "$OCTO_HOME" -maxdepth 3 -type f -name 'OctoBrowser*.AppImage' | head -n1 || true)
  if [[ -z "$APPIMG" ]]; then
    APPIMG=$(find "$OCTO_HOME" -maxdepth 3 -type f -name '*.AppImage' | head -n1 || true)
  fi
  if [[ -n "$APPIMG" ]]; then
    chmod +x "$APPIMG"
    # extract if fuse unavailable
    if ! "$APPIMG" --appimage-help >/dev/null 2>&1; then
      cd "$(dirname "$APPIMG")"
      sudo -u "$OCTO_USER" "$APPIMG" --appimage-extract || true
    fi
    ln -sfn "$APPIMG" "$OCTO_HOME/OctoBrowser.AppImage"
  fi
  rm -f /tmp/octo-browser.tar.gz
fi

# Prefer extracted AppRun if present
if [[ -x "$OCTO_HOME/squashfs-root/AppRun" ]]; then
  RUN_CMD="$OCTO_HOME/squashfs-root/AppRun"
elif [[ -x "$OCTO_HOME/OctoBrowser.AppImage" ]]; then
  RUN_CMD="$OCTO_HOME/OctoBrowser.AppImage"
else
  # last resort: any binary named Octo*
  RUN_CMD=$(find "$OCTO_HOME" -type f -perm -111 -name 'Octo*' | head -n1 || true)
fi

if [[ -z "${RUN_CMD:-}" || ! -e "$RUN_CMD" ]]; then
  echo "[octo] ERROR: client binary not found under $OCTO_HOME"
  ls -laR "$OCTO_HOME" | head -n 80
  exit 1
fi

echo "[octo] binary: $RUN_CMD"

cat > /usr/local/bin/octo-client-start.sh <<EOF
#!/usr/bin/env bash
set -euo pipefail
export DISPLAY=:${DISPLAY_NUM}
export OCTO_HEADLESS=1
export HOME=/home/${OCTO_USER}
# virtual framebuffer
if ! pgrep -f "Xvfb :${DISPLAY_NUM}" >/dev/null 2>&1; then
  Xvfb :${DISPLAY_NUM} -ac -screen 0 1920x1080x24 -nolisten tcp +extension GLX +render -noreset &
  sleep 2
fi
cd ${OCTO_HOME}
exec ${RUN_CMD}
EOF
chmod +x /usr/local/bin/octo-client-start.sh

cat > /etc/systemd/system/octo-client.service <<EOF
[Unit]
Description=Octo Browser Local Client (headless Local API :58888)
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=${OCTO_USER}
Group=${OCTO_USER}
Environment=HOME=/home/${OCTO_USER}
Environment=DISPLAY=:${DISPLAY_NUM}
Environment=OCTO_HEADLESS=1
ExecStartPre=/bin/bash -c 'pgrep -f "Xvfb :${DISPLAY_NUM}" >/dev/null || (Xvfb :${DISPLAY_NUM} -ac -screen 0 1920x1080x24 -nolisten tcp +extension GLX +render -noreset & sleep 2)'
ExecStart=${RUN_CMD}
Restart=always
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

# Xvfb must run as same user or root — run Xvfb as root helper service
cat > /etc/systemd/system/xvfb-octo.service <<EOF
[Unit]
Description=Xvfb for Octo Browser
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/Xvfb :${DISPLAY_NUM} -ac -screen 0 1920x1080x24 -nolisten tcp +extension GLX +render -noreset
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now xvfb-octo.service
sleep 1
systemctl enable --now octo-client.service
sleep 5
systemctl --no-pager --full status xvfb-octo.service || true
systemctl --no-pager --full status octo-client.service || true

echo "[octo] probing Local API..."
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -sf --max-time 3 http://127.0.0.1:58888/api/username >/dev/null 2>&1 \
     || curl -sf --max-time 3 http://127.0.0.1:58888/api/update >/dev/null 2>&1; then
    echo "[octo] Local API is responding on :58888"
    curl -s http://127.0.0.1:58888/api/username || true
    echo
    exit 0
  fi
  echo "  wait $i..."
  sleep 3
done

echo "[octo] WARNING: Local API not responding yet. Check: journalctl -u octo-client -n 80"
exit 0
