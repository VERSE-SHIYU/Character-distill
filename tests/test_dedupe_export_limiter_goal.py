"""导出接口合并 / 测试限流隔离 / 变异产物行尾的目标检查（spec §一）。

G1 后端只剩一个导出实现。
G2 raw 导出在 card_json 非法时照样 200 原样返回。
G3 distill 导出保留 60/分钟限流。
G4 测试限流隔离只写在 conftest，且没有文件在导入期改 limiter 装饰器。
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from deps import get_storage
from limiter import limiter
from routers import distill as D
from routers.auth import get_current_user
from routers.card import router as card_router
from storage.sqlite_store import SQLiteStore

_TESTS_DIR = Path(__file__).resolve().parent
_EXPORT_ROUTE = "/api/distill/cards/{card_id}/export"


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / f"test_{uuid.uuid4().hex}.db"))


@pytest.fixture
def user_id():
    return f"usr_{uuid.uuid4().hex[:8]}"


def _seed_card(store, uid, card_json, name="张三"):
    text_id = f"txt_{uuid.uuid4().hex}"
    card_id = f"card_{uuid.uuid4().hex}"
    _run_async(store.save_text(text_id, "src.txt", "content", user_id=uid))
    _run_async(store.save_card(card_id, text_id, name, card_json, user_id=uid))
    return card_id


def _build_app(store, uid):
    app = FastAPI()
    app.include_router(card_router)
    app.include_router(D.router)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {
        "id": uid, "username": "testuser", "role": "user",
    }
    return app


def _client(store, uid):
    return TestClient(_build_app(store, uid), raise_server_exceptions=False)


def _route_paths(routes):
    """展开 app.routes：FastAPI 会把 include_router 收成 _IncludedRouter。"""
    for route in routes:
        path = getattr(route, "path", None)
        if path is not None:
            yield path
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _route_paths(included.routes)


def test_g1_single_export_backend(store, user_id):
    card_id = _seed_card(store, user_id, '{"name": "张三"}')
    app = _build_app(store, user_id)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get(f"/api/cards/{card_id}/export")
    assert resp.status_code == 404

    export_routes = [
        path for path in _route_paths(app.routes)
        if path.endswith("/export") and "card_id" in path
    ]
    assert export_routes == [_EXPORT_ROUTE]


def test_g2_raw_export_survives_bad_json(store, user_id):
    bad_json = '{"name": "张三",'
    card_id = _seed_card(store, user_id, bad_json)

    resp = _client(store, user_id).get(
        f"/api/distill/cards/{card_id}/export?format=raw")

    assert resp.status_code == 200, resp.text
    assert "attachment" in resp.headers.get("content-disposition", "")
    assert resp.content == bad_json.encode("utf-8")


def test_g3_distill_export_rate_limited(store, user_id):
    card_id = _seed_card(store, user_id, '{"name": "张三"}')
    client = _client(store, user_id)

    statuses = [
        client.get(f"/api/distill/cards/{card_id}/export").status_code
        for _ in range(61)
    ]

    assert statuses[-1] == 429, statuses[-5:]


def test_g4_rate_limiter_isolation_single_source():
    reset_needle = "limiter." + "reset("
    patch_needle = "limiter." + "limit ="
    reset_files = set()
    patched_files = []

    for path in _TESTS_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(_TESTS_DIR).as_posix()
        if reset_needle in text:
            reset_files.add(rel)
        if patch_needle in text:
            patched_files.append(rel)

    assert reset_files == {"conftest.py"}, reset_files
    assert patched_files == [], patched_files
