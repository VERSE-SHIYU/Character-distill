"""`scripts/demo_seed.py` 的端到端用例：真 PG、假数据、零模型调用。

跑法 —— 用**一次性容器**，别用共享的 `charsim_test`（别的窗口的迁移会把它改脏）：

  docker run -d --rm --name demo-seed-test-pg -p 55433:5432 \\
    -e POSTGRES_USER=charsim -e POSTGRES_PASSWORD=<同 docker-compose.test.yml 的一次性值> \\
    -e POSTGRES_DB=demo_seed_test postgres:16-alpine

  TEST_DATABASE_URL=postgresql://charsim:<同一个值>@localhost:55433/demo_seed_test \\
    .venv/Scripts/python.exe -m pytest tests/test_demo_seed.py -q

库名必须以 `_test` 结尾 —— `tests/conftest.py::pytest_sessionstart` 据此判定「你连的是
测试库」，不满足就整场中止。目标账号一律是本用例现建的唯一用户名，故不用清表、互不干扰。
"""

from __future__ import annotations

import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from conftest import PG_ENV  # noqa: E402
from core.distiller import Distiller  # noqa: E402
from scripts.demo_seed import (  # noqa: E402
    DemoSeedError,
    _write_roster,
    build_bundle,
    bundle_sha256,
    run_import,
)
from storage.postgres_store import PostgresStore  # noqa: E402

_pg = PG_ENV.skipif("demo_seed 用例")

CARD_NAMES = ("宝玉", "刘姥姥")


@pytest.fixture
async def store():
    st = PostgresStore(os.environ["DATABASE_URL"])
    await st._ensure_initialized()
    yield st
    await st.close()


def _uniq(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


async def _make_user(store, role: str = "user") -> dict:
    uid = _uniq("usr")
    username = _uniq("demo")
    await store.create_user(uid, username, "not-a-real-hash")
    if role != "user":
        await store.execute("UPDATE users SET role = $1 WHERE id = $2", (role, uid))
    return {"id": uid, "username": username, "role": role}


async def _seed_source(store, *, text: str) -> dict:
    """本地那侧：一本有名单（当前版本）与两张卡的书。返回可复用的坐标。"""
    owner = await _make_user(store)
    tid = _uniq("txt")
    await store.save_text(tid, "红楼梦.txt", text, title="红楼梦", user_id=owner["id"])
    roster = [{"name": "宝玉", "importance": "主要"}, {"name": "刘姥姥", "importance": "次要"}]
    await store.save_characters(tid, roster, version=Distiller.IDENTIFY_VERSION)
    cards = {}
    for name in CARD_NAMES:
        cid = _uniq("card")
        await store.save_card(
            cid, tid, name,
            json.dumps({"name": name, "identity": f"{name}的设定"}, ensure_ascii=False),
            user_id=owner["id"],
        )
        cards[name] = cid
    return {"owner": owner, "text_id": tid, "roster": roster, "cards": cards, "text": text}


async def _bundle(store, src) -> dict:
    return await build_bundle(store, src["text_id"], list(CARD_NAMES))


@_pg
class TestDemoSeed:

    async def test_dry_run_writes_nothing(self, store):
        """试运行零写入 —— 目标账号名下不该多出任何文本或卡。"""
        src = await _seed_source(store, text="甲在院里站着。" * 20)
        target = await _make_user(store, role="guest")
        bundle = await _bundle(store, src)

        report = await run_import(store, bundle, target_username=target["username"], apply=False)

        assert report["dry_run"] is True
        assert report["text"]["action"] == "create"
        assert report["roster"]["action"] == "write"
        assert sorted(c["action"] for c in report["cards"]) == ["create", "create"]
        assert await store.list_texts(target["id"]) == []
        assert await store.list_cards(report["text"]["id"], target["id"]) == []

    async def test_apply_lands_text_roster_and_cards_on_the_target(self, store):
        src = await _seed_source(store, text="乙在院里站着。" * 20)
        target = await _make_user(store, role="guest")
        bundle = await _bundle(store, src)

        report = await run_import(store, bundle, target_username=target["username"], apply=True)

        assert report["dry_run"] is False
        tid = report["text"]["id"]
        texts = await store.list_texts(target["id"])
        assert [t["id"] for t in texts] == [tid]
        assert (await store.get_text_owned(tid, target["id"]))["content"] == src["text"]
        assert await store.get_characters_owned(
            tid, target["id"], version=Distiller.IDENTIFY_VERSION) == src["roster"]
        cards = await store.list_cards(tid, target["id"])
        assert sorted(c["name"] for c in cards) == sorted(CARD_NAMES)

    async def test_second_apply_does_not_duplicate(self, store):
        """再跑一次：复用同一行、同一批卡，不重复建。"""
        src = await _seed_source(store, text="丙在院里站着。" * 20)
        target = await _make_user(store, role="guest")
        bundle = await _bundle(store, src)

        first = await run_import(store, bundle, target_username=target["username"], apply=True)
        second = await run_import(store, bundle, target_username=target["username"], apply=True)

        assert second["text"]["action"] == "reuse"
        assert second["text"]["id"] == first["text"]["id"]
        assert sorted(c["action"] for c in second["cards"]) == ["skip", "skip"]
        assert len(await store.list_texts(target["id"])) == 1
        assert len(await store.list_cards(first["text"]["id"], target["id"])) == 2

    async def test_version_mismatch_stops_before_writing(self, store):
        src = await _seed_source(store, text="丁在院里站着。" * 20)
        target = await _make_user(store, role="guest")
        bundle = await _bundle(store, src)
        bundle["identify_version"] = Distiller.IDENTIFY_VERSION + 1
        bundle["sha256"] = bundle_sha256(bundle)   # 只让版本这一处不同

        with pytest.raises(DemoSeedError, match="版本不等"):
            await run_import(store, bundle, target_username=target["username"], apply=True)
        assert await store.list_texts(target["id"]) == []

    async def test_missing_account_stops(self, store):
        src = await _seed_source(store, text="戊在院里站着。" * 20)
        bundle = await _bundle(store, src)

        with pytest.raises(DemoSeedError, match="目标账号不存在"):
            await run_import(store, bundle, target_username=_uniq("nobody"), apply=True)

    async def test_soft_delete_only_touches_the_target_account(self, store):
        """软删：目标账号名下的删掉，别人的哪怕 id 传进来也不动。"""
        src = await _seed_source(store, text="己在院里站着。" * 20)      # 别人的书
        other = await _make_user(store)
        other_tid = _uniq("txt")
        await store.save_text(other_tid, "别处的书.txt", "别人的正文", user_id=other["id"])

        target = await _make_user(store, role="guest")
        own_tid = _uniq("txt")
        await store.save_text(own_tid, "旧副本.txt", "目标账号自己的旧副本", user_id=target["id"])
        bundle = await _bundle(store, src)

        report = await run_import(
            store, bundle, target_username=target["username"], apply=True,
            soft_delete_text_ids=[other_tid, own_tid],
        )

        by_id = {e["id"]: e for e in report["soft_delete"]}
        assert by_id[own_tid]["action"] == "soft-delete"
        assert by_id[other_tid]["action"] == "refused"
        assert (await store.get_text_owned(own_tid, target["id"]))["deleted_at"]
        assert not (await store.get_text_owned(other_tid, other["id"]))["deleted_at"]

    async def test_roster_write_refuses_a_text_the_actor_does_not_own(self, store):
        """名单写入口的归属门：别人的 text 行上一个字节都不该被写。"""
        other = await _make_user(store)
        other_tid = _uniq("txt")
        await store.save_text(other_tid, "别人的书.txt", "别人的正文", user_id=other["id"])
        target = await _make_user(store, role="guest")

        with pytest.raises(DemoSeedError, match="归属校验失败"):
            await _write_roster(store, other_tid, target["id"],
                                Distiller.IDENTIFY_VERSION, [{"name": "宝玉"}])
        assert await store.get_characters_owned(
            other_tid, other["id"], version=Distiller.IDENTIFY_VERSION) is None

    async def test_reused_text_never_lands_in_the_soft_delete_list(self, store):
        """包里的原文与要软删的旧副本是同一本 —— 停下，而不是把刚导入的书删掉。"""
        src = await _seed_source(store, text="庚在院里站着。" * 20)
        target = await _make_user(store, role="guest")
        bundle = await _bundle(store, src)

        first = await run_import(store, bundle, target_username=target["username"], apply=True)
        with pytest.raises(DemoSeedError, match="同一条"):
            await run_import(store, bundle, target_username=target["username"], apply=True,
                             soft_delete_text_ids=[first["text"]["id"]])
        live = await store.get_text_owned(first["text"]["id"], target["id"])
        assert live is not None and not live["deleted_at"]
