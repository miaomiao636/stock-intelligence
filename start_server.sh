#!/bin/bash
set -euo pipefail
umask 077
cd /Users/Admin/github-ai-tools/stock-intelligence
exec .venv/bin/python server.py
