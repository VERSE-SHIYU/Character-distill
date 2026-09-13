"""Pytest configuration: add project root + web to sys.path, set default storage backend."""

import functools
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "web"))   # web 模块（deps/routers/...）

# 测试默认用 SQLite；若外部已显式设 postgres 则尊重（避免覆盖 PG 测试）
os.environ.setdefault("STORAGE_BACKEND", "sqlite")


def pg_required() -> bool:
    """环境是否声明「本环境保证有 PG」。CI 用 `REQUIRE_PG_TESTS=1` 声明。

    声明之后，依赖 PG 的用例**拒绝跳过** —— PG 连不上就直接红，而不是默默 skip。
    否则 skip 就成了新的静默通道（缺陷 21「豁免即静默放行」同型）。
    """
    return os.getenv("REQUIRE_PG_TESTS") == "1"


@functools.lru_cache(maxsize=1)
def pg_reachable() -> bool:
    """当前 `DATABASE_URL` 能否真连上 PG（进程内只探一次，不打印凭据）。

    用「真连一次」而不是「读 STORAGE_BACKEND 猜」—— 后者是代理指标：变量写成 postgres
    而库连不上时，用例会以 TypeError/连接错恒红，正是缺陷 25 那类「用代理代替事实」。
    """
    import asyncio

    import asyncpg

    dsn = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/charsim_test")

    async def _probe() -> bool:
        try:
            conn = await asyncpg.connect(dsn, timeout=5)
        except Exception:
            return False
        await conn.close()
        return True

    try:
        return asyncio.run(_probe())
    except Exception:
        return False


def pg_skip_reason(what: str) -> str:
    """PG 不可达时的可见 skip 原因（把「怎么让它真跑」写在原因里）。"""
    return (
        f"{what}需要真 PostgreSQL，但 DATABASE_URL 指向的库连不上。"
        "本地没跑 PG 时这是**显式 skip**（不是失败）；要真跑就起 PG 并设 DATABASE_URL。"
        "CI 用 REQUIRE_PG_TESTS=1 声明有 PG，在那里本用例绝不会被跳过。"
    )

