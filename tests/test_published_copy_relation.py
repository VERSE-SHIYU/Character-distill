# -*- coding: utf-8 -*-
"""「草稿 ↔ 作者自己的发布副本」这一关系：他人 fork 不得污染原作者的公开卡。

关系定义（唯一权威表达）：

    「X 是草稿 D 的发布副本」 ⟺ X.forked_from = D.id AND X.visibility = 'public'
                                AND X.deleted_at IS NULL AND X.user_id = D.user_id

`cards.forked_from` 单列承载了两种关系：本文件的这一种，以及「公开卡被任意用户 fork」
（`get_card_forks`）。前者在 pg / sqlite 各有 5 处独立书写，判据不一致 —— 都不比作者
user_id，于是三条后果：

  1. B fork A 的公开卡 P，B 改自己那张 fork 的头像 → 向上同步把 P 的头像也改了
  2. A fork 自己的公开卡 P，A 改那张私有 fork 的头像 → 同样改掉 P，与草稿脱节
  3. B 把自己对 P 的 fork 设为公开 → P 的 `published_id` 指向 B 的卡；
     A 再发布 P 时 `publish_card` 还会**复用 B 的卡**当自己的发布副本

本文件跑在真 SQLiteStore 上（无 mock）：夹具全部走生产路径（`save_card` /
`update_card_visibility` / `fork_card` / `publish_card`），不手插行。
PG 同形用例见 `tests/test_postgres_store.py::TestPublishedCopyRelation`。
"""

from __future__ import annotations

import json
import uuid

import pytest

from storage.sqlite_store import SQLiteStore


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / f"test_{uuid.uuid4().hex}.db"))


@pytest.fixture
def user_a():
    return f"user_a_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def user_b():
    return f"user_b_{uuid.uuid4().hex[:8]}"


async def _text(store, user_id) -> str:
    text_id = f"txt_{uuid.uuid4().hex}"
    await store.save_text(text_id, "src.txt", "content", user_id=user_id)
    return text_id


async def _public_card(store, user_id, text_id, card_id=None) -> str:
    """作者的公开卡 P —— 他人可 fork 的那个对象。"""
    card_id = card_id or f"card_{uuid.uuid4().hex}"
    await store.save_card(card_id, text_id, "张三", json.dumps({"name": "张三"}), user_id=user_id)
    await store.update_card_visibility(card_id, "public")
    return card_id


class TestOtherUserForkDoesNotPolluteOrigin:
    """后果 1：fork 的头像改动不得向上落到原作者的公开卡。"""

    async def test_fork_avatar_save_leaves_origin_avatar_alone(self, store, user_a, user_b):
        tid = await _text(store, user_a)
        p = await _public_card(store, user_a, tid)
        await store.save_card_avatar(p, user_a, "AAAA")

        fork_id = f"card_{uuid.uuid4().hex}"
        fork = await store.fork_card(p, fork_id, user_b, None)
        assert fork is not None and fork["id"] == fork_id, "夹具没建出 fork，本用例会恒绿"
        assert await store.get_card_avatar_owned(fork_id, user_b) == "AAAA", "fork 该继承原卡头像"

        await store.save_card_avatar(fork_id, user_b, "BBBB")

        assert await store.get_card_avatar_owned(fork_id, user_b) == "BBBB", "本卡头像必须写进去"
        assert await store.get_card_avatar_owned(p, user_a) == "AAAA", \
            "他人 fork 改头像，向上同步污染了原作者的公开卡"


class TestOriginAvatarSaveDoesNotLeakIntoOthersForks:
    """后果 4（同根因，方向相反）：改自己公开卡的头像，不得写进他人的公开 fork。

    向下同步修复前写作 `WHERE forked_from = ? AND visibility = 'public'` —— 缺「同一属主」，
    于是原卡改头像会把**任意用户**的公开 fork 一并改掉（跨属主写入）。缺陷 84 的关系定义
    在向下方向上的作用就是这条：`X.user_id = D.user_id` 把他人 fork 排除掉。
    """

    async def test_origin_avatar_save_leaves_others_public_fork_alone(self, store, user_a, user_b):
        tid = await _text(store, user_a)
        p = await _public_card(store, user_a, tid)
        await store.save_card_avatar(p, user_a, "AAAA")

        fork_id = f"card_{uuid.uuid4().hex}"
        assert await store.fork_card(p, fork_id, user_b, None) is not None, "夹具没建出 fork"
        await store.update_card_visibility(fork_id, "public")
        assert await store.get_card_avatar_owned(fork_id, user_b) == "AAAA", "fork 该继承原卡头像"

        await store.save_card_avatar(p, user_a, "NEWAV")

        assert await store.get_card_avatar_owned(p, user_a) == "NEWAV", "本卡头像必须写进去"
        assert await store.get_card_avatar_owned(fork_id, user_b) == "AAAA", \
            "改原卡头像，向下同步写进了他人的公开 fork（跨属主写入）"


class TestOwnForkDoesNotDesyncDraft:
    """后果 2：A 自 fork 出的私有卡，改头像不得反向改掉作为 fork 源的公开卡。"""

    async def test_private_self_fork_avatar_save_leaves_origin_alone(self, store, user_a):
        tid = await _text(store, user_a)
        p = await _public_card(store, user_a, tid)
        await store.save_card_avatar(p, user_a, "AAAA")

        fork_id = f"card_{uuid.uuid4().hex}"
        fork = await store.fork_card(p, fork_id, user_a, None)  # fork_card 不禁自 fork
        assert fork is not None and fork["visibility"] == "private", "夹具没建出私有 fork"
        assert fork["user_id"] == user_a

        await store.save_card_avatar(fork_id, user_a, "CCCC")

        assert await store.get_card_avatar_owned(fork_id, user_a) == "CCCC"
        assert await store.get_card_avatar_owned(p, user_a) == "AAAA", \
            "私有 fork 改头像，向上同步把 fork 源（公开卡）也改了 —— 草稿与副本脱节"


class TestAuthorOwnPublishedCopyStillSyncs:
    """正向：功能没被删 —— 作者自己的草稿 ↔ 发布副本仍两向同步、仍是彼此的发布副本。

    这四条在修复前后都绿（它们是回归护栏，不是红源）：判据收窄时最容易顺手把真副本也
    排除掉（例如把「同属主」写成「公开卡才同步」），那时这类正向用例会先红。
    """

    @staticmethod
    async def _published(store, user_a) -> tuple[str, str]:
        tid = await _text(store, user_a)
        draft = f"card_{uuid.uuid4().hex}"
        await store.save_card(draft, tid, "张三", json.dumps({"name": "张三"}), user_id=user_a)
        copy_id = await store.publish_card(draft, user_a, "desc", "tag", "v1", '{"name": "张三"}')
        assert copy_id, "夹具没发布出副本，本用例会恒绿"
        return draft, copy_id

    async def test_draft_avatar_syncs_down_to_published_copy(self, store, user_a):
        draft, copy_id = await self._published(store, user_a)
        await store.save_card_avatar(draft, user_a, "DRAFT_AVATAR")
        assert await store.get_card_avatar_owned(copy_id, user_a) == "DRAFT_AVATAR", \
            "改草稿头像不再向下同步到作者的发布副本"

    async def test_published_copy_avatar_syncs_up_to_draft(self, store, user_a):
        draft, copy_id = await self._published(store, user_a)
        await store.save_card_avatar(copy_id, user_a, "COPY_AVATAR")
        assert await store.get_card_avatar_owned(draft, user_a) == "COPY_AVATAR", \
            "改发布副本头像不再向上同步到作者的草稿"

    async def test_published_id_is_the_authors_published_copy(self, store, user_a):
        draft, copy_id = await self._published(store, user_a)
        assert (await store.get_card_owned(draft, user_a))["published_id"] == copy_id, \
            "作者的发布副本没被认出来"

    async def test_republish_reuses_the_authors_published_copy(self, store, user_a):
        draft, copy_id = await self._published(store, user_a)
        again = await store.publish_card(draft, user_a, "desc2", "tag", "v2", '{"name": "张三"}')
        assert again == copy_id, "重新发布没复用作者自己的发布副本（原地更新语义丢了）"


class TestOtherUserPublicForkIsNotThePublishedCopy:
    """后果 3：B 把自己对 P 的 fork 设为公开，它仍不是「P 的发布副本」。"""

    @staticmethod
    async def _b_public_fork(store, user_a, user_b) -> tuple[str, str, str]:
        tid = await _text(store, user_a)
        p = await _public_card(store, user_a, tid)
        fork_id = f"card_{uuid.uuid4().hex}"
        assert await store.fork_card(p, fork_id, user_b, None) is not None
        await store.update_card_visibility(fork_id, "public")
        return p, tid, fork_id

    async def test_published_id_ignores_other_users_public_fork(self, store, user_a, user_b):
        p, tid, fork_id = await self._b_public_fork(store, user_a, user_b)

        for label, got in (
            ("get_card_owned", await store.get_card_owned(p, user_a)),
            ("get_card_unscoped", await store.get_card_unscoped(p)),
        ):
            assert not got.get("published_id"), \
                f"{label} 把他人 fork {fork_id} 当成了 P 的发布副本：{got.get('published_id')!r}"

        listed = [c for c in await store.list_cards(tid, user_a) if c["id"] == p]
        assert listed, "夹具没在 list_cards 里看到 P，本用例会恒绿"
        assert not listed[0].get("published_id"), \
            f"list_cards 把他人 fork 当成了 P 的发布副本：{listed[0].get('published_id')!r}"

    async def test_publish_does_not_reuse_other_users_public_fork(self, store, user_a, user_b):
        p, _tid, fork_id = await self._b_public_fork(store, user_a, user_b)

        new_id = await store.publish_card(p, user_a, "A的发布", "tag", "v1", '{"name": "张三"}')

        assert new_id and new_id != fork_id, "发布复用了他人 fork 当自己的发布副本"
        published = await store.get_card_owned(new_id, user_a)
        assert published is not None, "发布出来的副本不属于发布者"
        assert await store.get_card_owned(fork_id, user_b) is not None, "他人 fork 被发布动作弄丢了"
        b_fork = await store.get_card_owned(fork_id, user_b)
        assert b_fork["market_description"] != "A的发布", "他人 fork 的发布字段被 A 的发布覆盖"
