"""Pytest configuration: add project root + web to sys.path, set default storage backend."""

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "web"))   # web 模块（deps/routers/...）

# 测试默认用 SQLite；若外部已显式设 postgres 则尊重（避免覆盖 PG 测试）
os.environ.setdefault("STORAGE_BACKEND", "sqlite")
