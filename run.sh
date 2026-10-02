#!/usr/bin/env bash
# 一键启动：创建虚拟环境（首次）→ 装依赖 → 起服务
# 用法：./run.sh            前台运行 http://127.0.0.1:8787
#       ./run.sh --reload   开发模式热重载
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate
pip install -q -r requirements.txt
exec uvicorn server.main:app --host 127.0.0.1 --port 8787 "$@"
