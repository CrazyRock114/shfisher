"""Vercel serverless 入口：把 ASGI 应用暴露给 Vercel Python runtime。

本地开发不走此文件，直接 `uvicorn server.main:app`。
"""

import sys
from pathlib import Path

# serverless 中 api/ 位于 /var/task/api，项目根在 /var/task
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.main import app  # noqa: E402  (必须在 sys.path 注入后导入)
