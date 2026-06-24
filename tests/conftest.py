"""Pytest configuration: add project root to sys.path, set default storage backend."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 测试默认用 SQLite；若外部已显式设 postgres 则尊重（避免覆盖 PG 测试）
os.environ.setdefault("STORAGE_BACKEND", "sqlite")
