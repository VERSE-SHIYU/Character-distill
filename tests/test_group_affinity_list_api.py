"""群聊 affinity 列表端点 red-first 测试。

1. 成员确无 affinity 记录 → 端点结果不含该 card_id（而非伪造 50/陌生/🫥）。
2a. 成员有记录 → 返回真实 affinity + 正确 stage（回归保护）。
2b. 成员 affinity=0（真实可达状态，关系彻底破裂）→ 正常出现在结果中，
    不被省略、不被隐藏（保护后端 `if not row: continue` 不误伤 dict）。
3. 混合：同群部分有部分无 → 有者真实值、无者缺条目。

改前跑：1、3 必须红；2a、2b 改前已绿，作为回归。
"""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deps import get_storage
from routers.auth import get_current_user
from routers.group import router as group_router
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
    """独立 app：override storage / current_user，仅挂群聊路由。"""
    app = FastAPI()
    app.include_router(group_router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {
        "id": user_id,
        "username": "testuser",
        "is_admin": False,
    }
    return TestClient(app)


def _create_group(store, user_id, card_ids):
    group_id = f"grp_{uuid.uuid4().hex}"
    _run_async(store.create_group_session(group_id, "群聊A", card_ids, user_id=user_id))
    return group_id


class TestGroupAffinityList:
    def test_no_data_member_omitted(self, store, user_id, client):
        """成员确无 affinity 记录 → 结果不含该 card_id，而非伪造 50/陌生/🫥。"""
        cid = f"card_{uuid.uuid4().hex}"
        gid = _create_group(store, user_id, [cid])

        r = client.get(f"/api/group/{gid}/affinities")
        assert r.status_code == 200
        ids = [item["card_id"] for item in r.json()]
        assert cid not in ids  # 改前红：当前返回 {card_id, 50, 陌生, 🫥}

    def test_has_data_member_returns_real_value(self, store, user_id, client):
        """成员有记录 → 返回真实 affinity + 正确 stage。"""
        cid = f"card_{uuid.uuid4().hex}"
        gid = _create_group(store, user_id, [cid])
        _run_async(store.update_group_affinity(gid, cid, 62, 45, "心软", 38))

        r = client.get(f"/api/group/{gid}/affinities")
        assert r.status_code == 200
        items = r.json()
        assert len(items) == 1
        assert items[0]["card_id"] == cid
        assert items[0]["affinity"] == 62
        assert items[0]["stage_name"] == "朋友"
        assert items[0]["stage_emoji"] == "😄"

    def test_zero_affinity_member_not_omitted(self, store, user_id, client):
        """affinity=0（真实可达，关系破裂）→ 正常出现在结果，不省略不隐藏。"""
        cid = f"card_{uuid.uuid4().hex}"
        gid = _create_group(store, user_id, [cid])
        _run_async(store.update_group_affinity(gid, cid, 0, 0, "陌生", 100))

        r = client.get(f"/api/group/{gid}/affinities")
        assert r.status_code == 200
        items = r.json()
        assert len(items) == 1
        assert items[0]["card_id"] == cid
        assert items[0]["affinity"] == 0
        assert items[0]["stage_name"] == "陌生"
        assert items[0]["stage_emoji"] == "🫥"

    def test_mixed_members(self, store, user_id, client):
        """混合：有记录返回真实值，无记录缺条目。"""
        cid_has = f"card_{uuid.uuid4().hex}"
        cid_none = f"card_{uuid.uuid4().hex}"
        gid = _create_group(store, user_id, [cid_has, cid_none])
        _run_async(store.update_group_affinity(gid, cid_has, 80, 50, "亲近", 20))

        r = client.get(f"/api/group/{gid}/affinities")
        assert r.status_code == 200
        items = r.json()
        ids = [item["card_id"] for item in items]
        assert cid_has in ids
        assert cid_none not in ids  # 改前红：当前会返回 50/陌生/🫥
        item = next(i for i in items if i["card_id"] == cid_has)
        assert item["affinity"] == 80
        assert item["stage_name"] == "亲近"
