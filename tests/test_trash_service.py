# -*- coding: utf-8 -*-
"""回收站服务（core/trash_service.py）四类实体 × soft/restore/hard 的行为规格。

测的是**服务层的公开口**（soft_delete / restore / hard_delete），走真实 SQLiteStore ——
派发层此前零覆盖，`ENTITY_MAP` 里四个取数原语名悬空 9 天无人察觉（GET 取不到 → 500），
正是「测试直接调 storage、跳过服务层」留下的盲区。

三条口径（与 AGENTS.md §四 全仓约定一致）：
  - 权限：soft / hard 允许属主**或** admin；restore **仅属主**。
  - 非属主与不存在同判 **404**（防 ID 枚举），文案也须一致 —— 状态码与 detail 都是枚举信道。
  - hard 前必须先进回收站，否则 400。**对称地，soft 前必须没进回收站，否则 404**（缺陷 77）。
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
    return {"id": f"owner_{uuid.uuid4().hex[:8]}", "role": "user"}


@pytest.fixture
def intruder():
    """非属主、非 admin。"""
    return {"id": f"intruder_{uuid.uuid4().hex[:8]}", "role": "user"}


@pytest.fixture
def admin():
    return {"id": f"admin_{uuid.uuid4().hex[:8]}", "role": "admin"}


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
# 缺陷 77：已在回收站的记录再软删 → 拒绝，且**副作用零发生**
#
# soft_delete 的前置条件是「尚未删除」。缺了它，重复软删会穿过 before_mutation ——
# text 的 keep_cards 钩子照跑，把「本来还挂在文本上的卡」摘掉；而文本已是软删态，
# 这步摘卡**永久不可逆**（restore 不恢复卡片挂接）→ 恢复后卡片不回来。
# 状态码沿用现状：text / session 今天就已经是 404（`delete_text` / `delete_session`
# 的 rowcount 为 0 → 路由判 404），card / group 今天是**空转 200**
# （`delete_card` 无条件 return True、`delete_group_session` 返回 None）→ 统一到 404。
# ═══════════════════════════════════════════════════════════════════════════════

_READ = {
    "text": lambda s, i: s.get_text_unscoped(i),
    "card": lambda s, i: s.get_card_unscoped(i),
    "session": lambda s, i: s.get_session_unscoped(i),
    "group": lambda s, i: s.get_group_session_unscoped(i),
}


def _hook_recording(sink: list):
    async def hook(record: dict) -> None:
        sink.append(record["id"])
    return hook


@pytest.mark.parametrize("entity", ENTITIES)
class TestAlreadyInTrash:
    def test_first_soft_delete_reaches_hook_and_writes(self, store, owner, seed, entity):
        """正控 —— 没有它，一个「永远拒绝」的实现也能让下面三条全绿。"""
        eid = seed(entity, owner["id"])
        assert not _run(_READ[entity](store, eid)).get("deleted_at"), "新记录不该带 deleted_at"

        calls: list[str] = []
        code = _status(soft_delete(entity, eid, owner, store,
                                   before_mutation=_hook_recording(calls)))
        assert code is None, f"首次软删该成功，实得 {code}"
        assert calls == [eid], "首次软删没调用 before_mutation"
        assert _run(_READ[entity](store, eid))["deleted_at"], "首次软删没写 deleted_at"

    def test_repeat_soft_delete_is_rejected(self, store, owner, seed, entity):
        eid = seed(entity, owner["id"])
        _run(soft_delete(entity, eid, owner, store))
        before = _run(_READ[entity](store, eid))["deleted_at"]

        calls: list[str] = []
        code = _status(soft_delete(entity, eid, owner, store,
                                   before_mutation=_hook_recording(calls)))
        assert code == 404, f"期望 404，实得 {code}"
        assert calls == [], "被拒的软删仍跑了 before_mutation（副作用早于前置条件）"
        assert _run(_READ[entity](store, eid))["deleted_at"] == before, "被拒的软删仍写了库"

    def test_repeat_soft_delete_after_restore_is_allowed(self, store, owner, seed, entity):
        """前置条件是「在不在回收站」，不是「删过几次」—— 恢复后必须重新可删。"""
        eid = seed(entity, owner["id"])
        _run(soft_delete(entity, eid, owner, store))
        _run(restore(entity, eid, owner, store))
        assert _status(soft_delete(entity, eid, owner, store)) is None


def test_repeat_soft_delete_with_keep_cards_leaves_card_attached(store, owner, seed):
    """77 的病灶本体 —— 只对 text 有可观测的持久副作用，故不参数化。

    第一次**不带** keep_cards，故卡仍挂在文本上（非空读数）；第二次带 keep_cards
    若被拒，读数必须原封不动。用「本来就已断开」的卡做对象没有分辨力。
    """
    tid = seed("text", owner["id"])
    cid = f"card_{uuid.uuid4().hex}"
    _run(store.save_card(cid, tid, "张三", '{"name": "张三"}', user_id=owner["id"]))
    _run(soft_delete("text", tid, owner, store))
    assert _run(store.get_card_unscoped(cid))["text_id"] == tid, "首次软删不该断开卡片"

    code = _status(soft_delete("text", tid, owner, store, keep_cards=True))
    assert code == 404, f"期望 404，实得 {code}"
    assert _run(store.get_card_unscoped(cid))["text_id"] == tid, \
        "已删文本的第二次软删把卡片摘了（恢复后卡片回不来）"


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
