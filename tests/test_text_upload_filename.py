# -*- coding: utf-8 -*-
r"""上传落盘名与用户输入脱钩 —— 超长原文件名不再把整个上传打成 500。

生产复现（SG，2026-10-12）：原文件名 ≈ 400 字节的中文 `.txt`，路由把
`{uuid}_{原文件名}` 拼成落盘名，撞上 ext4 单组件 255 字节上限 → `open()` 抛
OSError(ENAMETOOLONG) → 500。文件名本身**合法**（`.txt` 在白名单内），长度是唯一变量。

三条判据：
  1. 超长中文名 → 200；落盘名 == `^[0-9a-f]{32}\.txt$`；`texts.filename` 仍是原名。
  2. 大写后缀 `.TXT` → 200（后缀按白名单规范化成小写再落盘）。
  3. 非法后缀 → 400，不是 500。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import deps
from core.text_manager import TextManager
from deps import get_storage
from limiter import limiter
from routers.auth import get_current_user
from routers.text import router as text_router


@pytest.fixture(autouse=True)
def _isolate_rate_limiter():
    """`limiter` 是进程级单例、按 client IP 记 5/min；本文件几次上传与别的用例共用
    「testclient」桶会互相顶成 429。前后各清一次，保证本文件零残留。"""
    limiter.reset()
    yield
    limiter.reset()


class _MemStore:
    """最小存储替身：上传路径只用到这两个方法。"""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    async def save_text(self, id, filename, content, **kw):
        row = {"id": id, "filename": filename, "content": content, **kw}
        self.rows[id] = row
        return row

    async def get_text_owned(self, id, user_id):
        return self.rows.get(id)


def _upload(monkeypatch, name: str, content: bytes = b"hello world"):
    """走 `/api/text/upload`；落盘名由 `upload_text_from_file` 的实参捕获（用完即删，只能这样看）。"""
    store = _MemStore()
    seen: dict[str, str] = {}

    app = FastAPI()
    app.state.limiter = limiter          # text 路由带 @limiter.limit
    app.include_router(text_router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "username": "t", "role": "user"}

    async def _fake_user_llm(*a, **kw):
        return object()                  # 非 None，否则路由先 503

    monkeypatch.setattr(deps, "get_user_llm", _fake_user_llm)
    monkeypatch.setattr(
        deps, "get_text_manager",
        lambda *a, **kw: TextManager(lambda: store, None, None, {}, memory_manager=None),
    )

    real = TextManager.upload_text_from_file

    async def _spy(self, file_path, filename, *a, **k):
        seen["file_path"] = file_path
        seen["filename"] = filename
        return await real(self, file_path, filename, *a, **k)

    monkeypatch.setattr(TextManager, "upload_text_from_file", _spy)

    r = TestClient(app, raise_server_exceptions=False).post(
        "/api/text/upload",
        data={"text_type": "other"},     # 非 story/classic，成功后不起后台线程
        files={"file": (name, content, "application/octet-stream")},
    )
    return r, store, seen


def test_long_original_name_uploads_and_disk_name_is_bounded(monkeypatch):
    original = "测" * 300 + ".txt"       # 单组件远超 255 字节；旧代码在此 OSError → 500
    r, store, seen = _upload(monkeypatch, original)

    assert r.status_code == 200, r.text
    disk = Path(seen["file_path"]).name
    assert re.fullmatch(r"[0-9a-f]{32}\.txt", disk), f"落盘名未与用户输入脱钩：{disk!r}"
    assert r.json()["filename"] == original                      # 响应即从库读回的那条记录
    assert next(iter(store.rows.values()))["filename"] == original


def test_uppercase_extension_is_normalized_on_disk(monkeypatch):
    r, _, seen = _upload(monkeypatch, "Novel.TXT")

    assert r.status_code == 200, r.text
    assert Path(seen["file_path"]).name.endswith(".txt"), seen["file_path"]


def test_illegal_extension_is_400_not_500(monkeypatch):
    r, _, seen = _upload(monkeypatch, "evil.exe")

    assert r.status_code == 400, r.text
    assert "file_path" not in seen       # 校验失败的路根本不落盘
