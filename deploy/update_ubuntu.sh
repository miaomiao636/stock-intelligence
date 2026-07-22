#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/stock-intelligence}"
SERVICE_NAME="${SERVICE_NAME:-stock-intelligence}"
WS_SERVICE_NAME="${WS_SERVICE_NAME:-stock-intelligence-feishu-ws}"
CRON_FILE="/etc/cron.d/${SERVICE_NAME}"

if [[ "$(id -u)" != "0" ]]; then
  echo "Please run as root."
  exit 1
fi

cd "$PROJECT_DIR"
test -f .env
test -x .venv/bin/python

echo "[1/4] Installing persistent Feishu long connection..."
cat >"/etc/systemd/system/${WS_SERVICE_NAME}.service" <<EOF
[Unit]
Description=Stock Intelligence Feishu WebSocket Client
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${PROJECT_DIR}
EnvironmentFile=${PROJECT_DIR}/.env
ExecStart=${PROJECT_DIR}/.venv/bin/python -m src.notifier.feishu_ws
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

echo "[2/4] Installing synchronized report schedule..."
if [[ ! -f "$CRON_FILE" ]]; then
  echo "Missing $CRON_FILE; run deploy/install_ubuntu.sh first."
  exit 1
fi
PROJECT_DIR="${PROJECT_DIR}" SERVICE_NAME="${SERVICE_NAME}" /bin/bash deploy/install_cron_ubuntu.sh

echo "[3/4] Restarting application services..."
systemctl daemon-reload
systemctl enable "${WS_SERVICE_NAME}.service"
# Stop the one-off nohup client used during initial setup, if it still exists.
# The anchored command line keeps this scoped to this project's legacy process.
pkill -f "^${PROJECT_DIR}/.venv/bin/python -m src.notifier.feishu_ws$" 2>/dev/null || true
systemctl restart "${SERVICE_NAME}.service"
systemctl restart "${WS_SERVICE_NAME}.service"

echo "[4/4] Health checks..."
sleep 2
curl --fail --silent --show-error http://127.0.0.1:8080/api/health
echo
systemctl is-active --quiet "${SERVICE_NAME}.service"
systemctl is-active --quiet "${WS_SERVICE_NAME}.service"
echo "Update complete. Existing .env and data were preserved."
