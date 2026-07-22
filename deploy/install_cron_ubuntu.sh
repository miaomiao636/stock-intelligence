#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/stock-intelligence}"
SERVICE_NAME="${SERVICE_NAME:-stock-intelligence}"
CRON_FILE="/etc/cron.d/${SERVICE_NAME}"

if [[ "$(id -u)" != "0" ]]; then
  echo "Please run as root."
  exit 1
fi

cat >"$CRON_FILE" <<EOF
SHELL=/bin/bash
PATH=${PROJECT_DIR}/.venv/bin:/usr/local/bin:/usr/bin:/bin
PY=${PROJECT_DIR}/.venv/bin/python
DIR=${PROJECT_DIR}

5 0 * * * root /bin/bash \$DIR/deploy/rotate_logs.sh

# 主任务与漏推检查共用同一把锁。补推最多等待30分钟，避免在报告仍生成时误报警。
45 8 * * 1-5 root /usr/bin/flock -n /var/lock/stock-intelligence-morning.lock /bin/bash -c 'cd "\$DIR" && "\$PY" cli.py daily --mode morning' >> /var/log/stock-intelligence-morning.log 2>&1
55 8 * * 1-5 root /usr/bin/flock -w 1800 /var/lock/stock-intelligence-morning.lock /bin/bash -c 'cd "\$DIR" && "\$PY" cli.py notify --mode morning --if-missing' >> /var/log/stock-intelligence-watchdog.log 2>&1

15 13 * * 1-5 root /usr/bin/flock -n /var/lock/stock-intelligence-afternoon.lock /bin/bash -c 'cd "\$DIR" && "\$PY" cli.py daily --mode afternoon' >> /var/log/stock-intelligence-afternoon.log 2>&1
25 13 * * 1-5 root /usr/bin/flock -w 1800 /var/lock/stock-intelligence-afternoon.lock /bin/bash -c 'cd "\$DIR" && "\$PY" cli.py notify --mode afternoon --if-missing' >> /var/log/stock-intelligence-watchdog.log 2>&1

35 9 * * 1-5 root cd \$DIR && \$PY cli.py paper open >> /var/log/stock-intelligence-open.log 2>&1
45 9 * * 1-5 root cd \$DIR && \$PY cli.py paper open --if-missing >> /var/log/stock-intelligence-open-watchdog.log 2>&1
* 9-14 * * 1-5 root cd \$DIR && \$PY cli.py paper execute-due >> /var/log/stock-intelligence-execute.log 2>&1
0 15 * * 1-5 root cd \$DIR && \$PY cli.py paper execute-due >> /var/log/stock-intelligence-execute.log 2>&1
5,35 10 * * 1-5 root cd \$DIR && \$PY cli.py paper intraday >> /var/log/stock-intelligence-intraday.log 2>&1
5,25 11 * * 1-5 root cd \$DIR && \$PY cli.py paper intraday >> /var/log/stock-intelligence-intraday.log 2>&1
5,35 13-14 * * 1-5 root cd \$DIR && \$PY cli.py paper intraday >> /var/log/stock-intelligence-intraday.log 2>&1
55 14 * * 1-5 root cd \$DIR && \$PY cli.py paper intraday >> /var/log/stock-intelligence-intraday.log 2>&1

45 16 * * 1-5 root /usr/bin/flock -n /var/lock/stock-intelligence-closing.lock /bin/bash -c 'cd "\$DIR" && "\$PY" cli.py daily --mode closing' >> /var/log/stock-intelligence-closing.log 2>&1
55 16 * * 1-5 root /usr/bin/flock -w 1800 /var/lock/stock-intelligence-closing.lock /bin/bash -c 'cd "\$DIR" && "\$PY" cli.py notify --mode closing --if-missing' >> /var/log/stock-intelligence-watchdog.log 2>&1

10 0 * * * root mkdir -p /opt/stock-intelligence-backups && tar -czf /opt/stock-intelligence-backups/data-\$(date +\\%F).tar.gz -C ${PROJECT_DIR} data
EOF

chmod 644 "$CRON_FILE"
echo "Installed synchronized cron schedule: $CRON_FILE"
