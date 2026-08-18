"""Affinity 读取链路重构的 red-first 测试。

1. 会话有 affinity_state 且不在内存 sessions 中 → GET 返回持久化真实值，而非 50/30/70/陌生。
2. 会话确无 affinity 数据 → GET 返回 204（无数据语义），而非假默认值 dict。
3. 仅有扁平列的老会话（已评估数值）→ 仍能正确读出（向后兼容）。

改前跑：1、2 必须红；3 是兼容回归（改前已绿，改后保持绿）。
"""

import json
import sqlite3
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deps import get_sessions, get_storage
from routers.auth import get_current_user
from routers.chat import router as chat_router
from storage.sqlite_store import SQLiteStore


def _run_async(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / f"test_{uuid.uuid4().hex}.db")
    return SQLiteStore(db_path)


@pytest.fixture
def user_id():
    return f"user_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def client(store, user_id):
    """独立 app：override storage / sessions（空内存）/ current_user。"""
    app = FastAPI()
    app.include_router(chat_router)
    fresh_sessions = {}
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_sessions] = lambda: fresh_sessions
    app.dependency_overrides[get_current_user] = lambda: {
        "id": user_id,
        "username": "testuser",
        "is_admin": False,
    }
    return TestClient(app)


def _create_session(store, user_id):
    text_id = f"txt_{uuid.uuid4().hex}"
    _run_async(store.save_text(text_id, "src.txt", "content", user_id=user_id))
    card_id = f"card_{uuid.uuid4().hex}"
    _run_async(store.save_card(card_id, text_id, "张三", '{"name": "张三"}', user_id=user_id))
    sid = f"ses_{uuid.uuid4().hex}"
    _run_async(store.save_session(sid, card_id, "user", "", user_id=user_id))
    return sid


def _set_flat_columns(db_path, session_id, *, affinity, trust, mood, guard, reason):
    """直接写 sessions 扁平列（模拟老会话），不依赖将被删除的 update_session_affinity。"""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE sessions SET affinity=?, trust=?, mood=?, guard=?, affinity_reason=? WHERE id=?",
            (affinity, trust, mood, guard, reason, session_id),
        )
        conn.commit()
    finally:
        conn.close()


class TestGetAffinityReadPath:
    def test_persisted_state_returned_when_not_in_memory(self, store, user_id, client):
        """affinity_state 非空 + 不在内存 → 返回真实持久化值，而非假默认值。"""
        sid = _create_session(store, user_id)
        real = {
            "affinity": 88,
            "trust": 44,
            "mood": "开心",
            "guard": 55,
            "reason": "越来越信任",
            "inner_voice": "今天聊得很开心",
            "mood_emoji": "😄",
            "stage": "熟络",
            "stage_emoji": "🤝",
            "user_catchwords": [],
        }
        _run_async(store.save_affinity_state(sid, json.dumps(real, ensure_ascii=False)))

        r = client.get(f"/api/chat/affinity/{sid}")
        assert r.status_code == 200
        data = r.json()
        assert data["affinity"] == 88
        assert data["mood"] == "开心"
        assert data["inner_voice"] == "今天聊得很开心"
        assert data["stage"] == "熟络"

    def test_no_data_returns_204(self, store, user_id, client):
        """确无 affinity 数据 → 204（无数据），而非假默认值。"""
        sid = _create_session(store, user_id)

        r = client.get(f"/api/chat/affinity/{sid}")
        assert r.status_code == 204

    def test_legacy_flat_columns_still_read(self, store, user_id, client):
        """仅扁平列的老会话（已评估数值）→ 仍正确读出（向后兼容）。"""
        sid = _create_session(store, user_id)
        _set_flat_columns(
            store.db_path,
            sid,
            affinity=75,
            trust=40,
            mood="期待",
            guard=60,
            reason=json.dumps({"inner_voice": "我有点期待下次见面", "mood_emoji": "✨"}, ensure_ascii=False),
        )

        r = client.get(f"/api/chat/affinity/{sid}")
        assert r.status_code == 200
        data = r.json()
        assert data["affinity"] == 75
        assert data["mood"] == "期待"
        assert data["inner_voice"] == "我有点期待下次见面"
