"""Storage package exports.

通过环境变量 STORAGE_BACKEND 选择存储后端：
  - STORAGE_BACKEND=postgres  -> 使用 PostgresStore（生产，需 DATABASE_URL）
  - 其它 / 未设置             -> 使用 SQLiteStore（默认，向后兼容、可回退）

get_store() 是工厂函数，返回一个 StorageBase 实例。
业务层只依赖 StorageBase 抽象接口，切换后端无需改动调用方。
"""

from __future__ import annotations

import os

from .base import StorageBase
from .sqlite_store import SQLiteStore

__all__ = ["StorageBase", "SQLiteStore", "get_store"]


def get_store() -> StorageBase:
    """根据环境变量返回对应的存储实现。

    环境变量:
      STORAGE_BACKEND : "postgres" 或 "sqlite"（默认 sqlite）
      DATABASE_URL    : postgres 后端必填，形如
                        postgresql://user:pass@host:5432/dbname
      DB_PATH         : sqlite 后端的数据库文件路径（默认 data/charsim.db）
    """
    backend = os.getenv("STORAGE_BACKEND", "sqlite").strip().lower()

    if backend == "postgres":
        # 延迟导入：未选用 postgres 时不强制安装 asyncpg
        from .postgres_store import PostgresStore

        dsn = os.getenv("DATABASE_URL", "").strip()
        if not dsn:
            raise RuntimeError(
                "STORAGE_BACKEND=postgres 需要设置 DATABASE_URL 环境变量"
            )
        return PostgresStore(dsn)

    db_path = os.getenv("DB_PATH", "data/charsim.db").strip()
    return SQLiteStore(db_path)
