#!/bin/bash
set -euo pipefail
LOG_DIR=/Users/Admin/github-ai-tools/stock-intelligence/data/logs
mkdir -p "$LOG_DIR"
for file in "$LOG_DIR"/*.log; do
    [ -f "$file" ] || continue
    size=$(stat -f%z "$file")
    if [ "$size" -gt 10485760 ]; then
        cp "$file" "$file.1"
        : > "$file"
    fi
done
