# -*- coding: utf-8 -*-
"""回收站服务（core/trash_service.py）四类实体 × soft/restore/hard 的行为规格。

测的是**服务层的公开口**（soft_delete / restore / hard_delete），走真实 SQLiteStore ——
派发层此前零覆盖，`ENTITY_MAP` 里四个取数原语名悬空 9 天无人察觉（GET 取不到 → 500），
正是「测试直接调 storage、跳过服务层」留下的盲区。

三条口径（与 AGENTS.md §四 全仓约定一致）：
  - 权限：soft / hard 允许属主**或** admin；restore **仅属主**。
  - 非属主与不存在同判 **404**（防 ID 枚举），文案也须一致 —— 状态码与 detail 都是枚举信道。
  - hard 前必须先进回收站，否则 400。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import HTTPException

from core.trash_service import hard_delete, restore, soft_delete
from storage.sqlite_store import SQLiteStore

ENTITIES = ("text", "card", "session", "group")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / f"trash_{uuid.uuid4().hex}.db"))


@pytest.fixture
def owner():
    return {"id": f"owner_{uuid.uuid4().hex[:8]}", "is_admin": False}


@pytest.fixture
def intruder():
    """非属主、非 admin。"""
    return {"id": f"intruder_{uuid.uuid4().hex[:8]}", "is_admin": False}


@pytest.fixture
def admin():
    return {"id": f"admin_{uuid.uuid4().hex[:8]}", "is_admin": True}


def _seed(store, entity: str, uid: str) -> str:
    """造一条属于 uid 的记录，返回其 id。card/session 依赖 text/card，按链补齐。"""
    if entity == "text":
        tid = f"txt_{uuid.uuid4().hex}"
        _run(store.save_text(tid, "src.txt", "内容", user_id=uid))
        return tid
    if entity == "card":
        text_id = _seed(store, "text", uid)
        cid = f"card_{uuid.uuid4().hex}"
        _run(store.save_card(cid, text_id, "张三", '{"name": "张三"}', user_id=uid))
        return cid
    if entity == "session":
        card_id = _seed(store, "card", uid)
        sid = f"ses_{uuid.uuid4().hex}"
        _run(store.save_session(sid, card_id, "user", "", user_id=uid))
        return sid
    if entity == "group":
        gid = f"grp_{uuid.uuid4().hex}"
        _run(store.create_group_session(gid, "群聊A", [], user_id=uid))
        return gid
    raise AssertionError(f"未知实体类型：{entity}")


@pytest.fixture
def seed(store):
    return lambda entity, uid: _seed(store, entity, uid)


def _status(coro) -> int | None:
    """跑 coro；返回 HTTPException.status_code，成功则 None。"""
    try:
        _run(coro)
    except HTTPException as exc:
        return exc.status_code
    return None


def _outcome(coro) -> tuple[int | None, str | None]:
    """跑 coro；返回 (HTTPException.status_code, detail)，成功则 (None, None)。"""
    try:
        _run(coro)
    except HTTPException as exc:
        return exc.status_code, exc.detail
    return None, None


# ═══════════════════════════════════════════════════════════════════════════════
# 属主：soft → restore → soft → hard 全程可用
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("entity", ENTITIES)
class TestOwner:
    def test_soft_then_restore_then_hard(self, store, owner, seed, entity):
        eid = seed(entity, owner["id"])

        assert _status(soft_delete(entity, eid, owner, store)) is None
        assert _status(restore(entity, eid, owner, store)) is None
        assert _status(soft_delete(entity, eid, owner, store)) is None
        assert _status(hard_delete(entity, eid, owner, store)) is None

    def test_hard_before_trash_is_400(self, store, owner, seed, entity):
        """没进过回收站就永久删除 → 400（不是 404、不是 500）。"""
        eid = seed(entity, owner["id"])
        code = _status(hard_delete(entity, eid, owner, store))
        assert code == 400, f"期望 400，实得 {code}"

    def test_unknown_id_is_404(self, store, owner, entity):
        code = _status(soft_delete(entity, f"nope_{uuid.uuid4().hex}", owner, store))
        assert code == 404, f"期望 404，实得 {code}"


# ═══════════════════════════════════════════════════════════════════════════════
# 非属主：soft / restore / hard 一律 404，且与「不存在」同码同文案
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("entity", ENTITIES)
class TestNonOwner:
    def test_soft_delete_404(self, store, owner, intruder, seed, entity):
        eid = seed(entity, owner["id"])
        code = _status(soft_delete(entity, eid, intruder, store))
        assert code == 404, f"期望 404，实得 {code}"

    def test_restore_404(self, store, owner, intruder, seed, entity):
        eid = seed(entity, owner["id"])
        _run(soft_delete(entity, eid, owner, store))
        code = _status(restore(entity, eid, intruder, store))
        assert code == 404, f"期望 404，实得 {code}"

    def test_hard_delete_404(self, store, owner, intruder, seed, entity):
        eid = seed(entity, owner["id"])
        _run(soft_delete(entity, eid, owner, store))
        code = _status(hard_delete(entity, eid, intruder, store))
        assert code == 404, f"期望 404，实得 {code}"

    def test_404_detail_matches_missing_id(self, store, owner, intruder, seed, entity):
        """非属主与不存在的 detail 必须逐字相同 —— 文案是第二条枚举信道。

        先钉死两边都是 404：只比 detail 的话，两边同抛一条 500（bug 期就是）也算「相同」，
        这条会以恒真的方式变绿（§四「对空集的断言恒真」）。
        """
        eid = seed(entity, owner["id"])
        foreign = _outcome(soft_delete(entity, eid, intruder, store))
        missing = _outcome(soft_delete(entity, f"nope_{uuid.uuid4().hex}", intruder, store))
        assert foreign[0] == 404 and missing[0] == 404, f"两边都须 404，实得 {foreign[0]} / {missing[0]}"
        assert foreign == missing, f"非属主 {foreign!r} != 不存在 {missing!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# admin：可跨属主 soft / hard，但 restore 仍仅属主
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("entity", ENTITIES)
class TestAdmin:
    def test_soft_delete_foreign_ok(self, store, owner, admin, seed, entity):
        eid = seed(entity, owner["id"])
        assert _status(soft_delete(entity, eid, admin, store)) is None

    def test_hard_delete_foreign_ok(self, store, owner, admin, seed, entity):
        eid = seed(entity, owner["id"])
        _run(soft_delete(entity, eid, owner, store))
        assert _status(hard_delete(entity, eid, admin, store)) is None

    def test_restore_foreign_404(self, store, owner, admin, seed, entity):
        eid = seed(entity, owner["id"])
        _run(soft_delete(entity, eid, owner, store))
        code = _status(restore(entity, eid, admin, store))
        assert code == 404, f"admin 恢复他人记录应 404，实得 {code}"

    def test_soft_delete_foreign_unknown_id_404(self, store, admin, entity):
        code = _status(soft_delete(entity, f"nope_{uuid.uuid4().hex}", admin, store))
        assert code == 404, f"期望 404，实得 {code}"


# ═══════════════════════════════════════════════════════════════════════════════
# admin 逃生口的原语本身：group 是四个实体里唯一只有 *_owned 变体的
# ═══════════════════════════════════════════════════════════════════════════════

def test_group_session_unscoped_sees_foreign_row(store, owner, intruder):
    """`get_group_session_unscoped` 必须真的无身份过滤 —— 否则 admin 跨属主删除做不到。

    与 `_owned` 对照：同一个 id，非属主问 `_owned` 得 None，问 `_unscoped` 得行。
    两边都对才算这个原语成立（只有一半时，要么它白加了、要么它其实是 owned 的马甲）。
    """
    gid = _seed(store, "group", owner["id"])
    assert _run(store.get_group_session_owned(gid, intruder["id"])) is None
    assert _run(store.get_group_session_unscoped(gid)) is not None
