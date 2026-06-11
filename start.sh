#!/bin/bash
#
# OpenGNB 管理端启动脚本
# 适用于: Linux
# 用途: 启动 Web 管理端
# 作者: 烽云CDN团队
# 日期: 2026-06-11
#
# 使用方法:
#   chmod +x start.sh
#   ./start.sh
#

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

HOST="${GNB_ADMIN_HOST:-0.0.0.0}"
PORT="${GNB_ADMIN_PORT:-8181}"

echo "启动 OpenGNB 管理端 http://${HOST}:${PORT}/"
exec python3 app.py --host "$HOST" --port "$PORT"
