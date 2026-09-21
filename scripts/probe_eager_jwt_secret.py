# -*- coding: utf-8 -*-
"""缺陷 95 的产数脚本：适配器的 `Depends(get_jwt_secret)` 在无凭据路径也取 secret。

FastAPI 在**进端点函数体之前**解析依赖树，`Depends` 参数的求值与请求带不带凭据无关。
所以只要一条路由的依赖树里有 `get_optional_user` / `get_current_user`（两者都
`secret: str = Depends(get_jwt_secret)`），一次**匿名**请求也会把 secret 读一遍；
`get_jwt_secret()` 未配置时抛 RuntimeError ⇒ 500（而它本该是一次正常的匿名读）。

三条对照：
  - `GET /api/market/card/{id}`  公开 + 依赖树里有适配器  ⇒ 未配置时 500（本该 404）
  - `GET /api/market/tags`       公开 + 无适配器          ⇒ 200（不受影响）
  - `GET /api/history/list`      受保护                   ⇒ 401（中间件在路由前拦下，
                                                             secret 轮不到读）

跑法（在仓根，用仓内 .venv）：
    .venv/Scripts/python.exe scripts/probe_eager_jwt_secret.py
"""
import asyncio
import os
import sys
import tempfile
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "web")]
os.environ["STORAGE_BACKEND"] = "sqlite"

import deps  # noqa: E402
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pwdlib import PasswordHash  # noqa: E402
from storage.sqlite_store import SQLiteStore  # noqa: E402

# 一次性库放临时目录：这脚本入库（scripts/ 是被跟踪的），别在仓里留 .db。
store = SQLiteStore(str(Path(tempfile.mkdtemp(prefix="probe_eager_secret_")) / "probe.db"))


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


_run(store.create_user(
    f"usr_{uuid.uuid4().hex[:16]}", "ProbeX",
    PasswordHash.recommended().hash("Pass1234"),
))
deps._storage = store

URLS = [
    ("GET", "/api/market/card/nope", "公开+适配器(预期: 配置齐全时 404)"),
    ("GET", "/api/market/tags", "公开无适配器"),
    ("GET", "/api/history/list", "受保护"),
]

for secret in ("set", "unset"):
    if secret == "set":
        os.environ["JWT_SECRET"] = "p" * 40
    else:
        os.environ.pop("JWT_SECRET", None)
    client = TestClient(server.app, raise_server_exceptions=False)
    print(f"== JWT_SECRET {secret} ==")
    for method, url, note in URLS:
        r = client.request(method, url)
        print(f"   {method:4} {url:24} -> {r.status_code}  {str(r.text)[:40]}   # {note}")
