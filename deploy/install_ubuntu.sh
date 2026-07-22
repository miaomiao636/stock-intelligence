#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/stock-intelligence}"
SERVICE_NAME="${SERVICE_NAME:-stock-intelligence}"
PORT="${PORT:-8080}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ "$(id -u)" != "0" ]]; then
  echo "Please run as root."
  exit 1
fi

cd "$PROJECT_DIR"

echo "[1/9] Installing system packages..."
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3 python3-venv python3-pip git nginx ufw curl ca-certificates

echo "[2/9] Setting timezone..."
timedatectl set-timezone Asia/Shanghai || true

echo "[3/9] Preparing Python environment..."
"$PYTHON_BIN" -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo "[4/9] Preparing .env..."
if [[ ! -f .env ]]; then
  cp .env.example .env
fi

python - <<'PY'
from pathlib import Path

env_path = Path(".env")
lines = env_path.read_text(encoding="utf-8").splitlines()
updates = {
    "PROJECT_ROOT": "/opt/stock-intelligence",
    "TIMEZONE": "Asia/Shanghai",
    "HOST": "127.0.0.1",
    "PORT": "8080",
    "PAPER_TRADING_ENABLED": "false",
}
seen = set()
out = []
for line in lines:
    if line.strip().startswith("#") or "=" not in line:
        out.append(line)
        continue
    key = line.split("=", 1)[0].strip()
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY

echo
echo "Now enter server-only secrets. Input is hidden where appropriate."
echo "Leave optional values empty if you do not use them yet."

set_env_value() {
  local key="$1"
  local value="$2"
  python - "$key" "$value" <<'PY'
from pathlib import Path
import sys

key, value = sys.argv[1], sys.argv[2]
path = Path(".env")
lines = path.read_text(encoding="utf-8").splitlines()
done = False
out = []
for line in lines:
    if line.startswith(f"{key}="):
        out.append(f"{key}={value}")
        done = True
    else:
        out.append(line)
if not done:
    out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
}

read_secret() {
  local label="$1"
  local value
  read -r -s -p "$label: " value
  echo
  printf '%s' "$value"
}

read_plain() {
  local label="$1"
  local value
  read -r -p "$label: " value
  printf '%s' "$value"
}

API_KEY_VALUE="$(read_secret 'API_KEY')"
[[ -n "$API_KEY_VALUE" ]] && set_env_value API_KEY "$API_KEY_VALUE"

LLM_PROVIDER_VALUE="$(read_plain 'LLM_PROVIDER [custom/deepseek]')"
[[ -n "$LLM_PROVIDER_VALUE" ]] && set_env_value LLM_PROVIDER "$LLM_PROVIDER_VALUE"

LLM_BASE_URL_VALUE="$(read_plain 'LLM_BASE_URL')"
[[ -n "$LLM_BASE_URL_VALUE" ]] && set_env_value LLM_BASE_URL "$LLM_BASE_URL_VALUE"

LLM_API_KEY_VALUE="$(read_secret 'LLM_API_KEY')"
[[ -n "$LLM_API_KEY_VALUE" ]] && set_env_value LLM_API_KEY "$LLM_API_KEY_VALUE"

TAVILY_API_KEY_VALUE="$(read_secret 'TAVILY_API_KEY')"
[[ -n "$TAVILY_API_KEY_VALUE" ]] && set_env_value TAVILY_API_KEY "$TAVILY_API_KEY_VALUE"

TUSHARE_TOKEN_VALUE="$(read_secret 'TUSHARE_TOKEN')"
[[ -n "$TUSHARE_TOKEN_VALUE" ]] && set_env_value TUSHARE_TOKEN "$TUSHARE_TOKEN_VALUE"

FEISHU_WEBHOOK_URL_VALUE="$(read_secret 'FEISHU_WEBHOOK_URL')"
[[ -n "$FEISHU_WEBHOOK_URL_VALUE" ]] && set_env_value FEISHU_WEBHOOK_URL "$FEISHU_WEBHOOK_URL_VALUE"

FEISHU_APP_ID_VALUE="$(read_plain 'FEISHU_APP_ID')"
[[ -n "$FEISHU_APP_ID_VALUE" ]] && set_env_value FEISHU_APP_ID "$FEISHU_APP_ID_VALUE"

FEISHU_APP_SECRET_VALUE="$(read_secret 'FEISHU_APP_SECRET')"
[[ -n "$FEISHU_APP_SECRET_VALUE" ]] && set_env_value FEISHU_APP_SECRET "$FEISHU_APP_SECRET_VALUE"

FEISHU_RECEIVE_ID_VALUE="$(read_plain 'FEISHU_RECEIVE_ID')"
[[ -n "$FEISHU_RECEIVE_ID_VALUE" ]] && set_env_value FEISHU_RECEIVE_ID "$FEISHU_RECEIVE_ID_VALUE"
set_env_value FEISHU_RECEIVE_ID_TYPE "chat_id"

FEISHU_VERIFICATION_TOKEN_VALUE="$(read_secret 'FEISHU_VERIFICATION_TOKEN')"
[[ -n "$FEISHU_VERIFICATION_TOKEN_VALUE" ]] && set_env_value FEISHU_VERIFICATION_TOKEN "$FEISHU_VERIFICATION_TOKEN_VALUE"

chmod 600 .env

echo "[5/9] Initializing project data..."
python cli.py init
python cli.py paper init --cash 4000 || true

echo "[6/9] Installing web service..."
cat >/etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=Stock Intelligence Web Server
After=network.target

[Service]
Type=simple
WorkingDirectory=${PROJECT_DIR}
EnvironmentFile=${PROJECT_DIR}/.env
ExecStart=${PROJECT_DIR}/.venv/bin/uvicorn server:app --host 127.0.0.1 --port ${PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"

cat >/etc/systemd/system/${SERVICE_NAME}-feishu-ws.service <<EOF
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

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}-feishu-ws"

echo "[7/9] Installing Nginx site..."
cat >/etc/nginx/sites-available/${SERVICE_NAME} <<EOF
server {
    listen 80;
    server_name _;

    client_max_body_size 10m;

    location / {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/${SERVICE_NAME} /etc/nginx/sites-enabled/${SERVICE_NAME}
nginx -t
systemctl reload nginx

echo "[8/9] Installing cron jobs..."
PROJECT_DIR="${PROJECT_DIR}" SERVICE_NAME="${SERVICE_NAME}" /bin/bash deploy/install_cron_ubuntu.sh

echo "[9/9] Enabling firewall..."
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo
echo "Deployment finished."
echo "Health check:"
curl -fsS http://127.0.0.1:${PORT}/api/health || true
echo
echo "Open: http://$(curl -fsS ifconfig.me || hostname -I | awk '{print $1}')"
echo "Paper trading remains disabled until you explicitly set PAPER_TRADING_ENABLED=true."
