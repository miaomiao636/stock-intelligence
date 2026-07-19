#!/bin/bash
set -euo pipefail
umask 077
cd /Users/Admin/github-ai-tools/stock-intelligence
# 飞书长连接必须独立于本机桌面代理存活；代理重启不应中断交易回调。
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export NO_PROXY="${NO_PROXY:+${NO_PROXY},}localhost,127.0.0.1,open.feishu.cn,.feishu.cn"
export no_proxy="$NO_PROXY"
exec .venv/bin/python -m src.notifier.feishu_ws
