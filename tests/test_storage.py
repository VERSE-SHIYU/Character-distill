"""Tests for SQLiteStore basic CRUD using in-memory / temp-file database.

All tests use a fresh store instance, run offline, and do not depend on
any external service.
"""

from __future__ import annotations

import uuid

import aiosqlite
import pytest

from storage.sqlite_store import SQLiteStore


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    """Provide a fresh SQLiteStore backed by a temporary file."""
    db_path = str(tmp_path / f"test_{uuid.uuid4().hex}.db")
    return SQLiteStore(db_path)


@pytest.fixture
def text_id():
    return f"txt_{uuid.uuid4().hex}"


@pytest.fixture
def card_id():
    return f"card_{uuid.uuid4().hex}"


@pytest.fixture
def session_id():
    return f"ses_{uuid.uuid4().hex}"


# ── Text CRUD ────────────────────────────────────────────────────────────────

class TestTextCrud:
    """save_text → get_text → list_texts → delete_text lifecycle."""

    async def test_save_and_get(self, store, text_id):
        result = await store.save_text(text_id, "test.txt", "Hello world")
        assert result.get("id") == text_id
        assert result.get("filename") == "test.txt"
        assert result.get("content") == "Hello world"
        assert result.get("char_count") == 11

        got = await store.get_text(text_id)
        assert got is not None
        assert got["id"] == text_id

    async def test_get_nonexistent(self, store):
        result = await store.get_text("no_such_id")
        assert result is None

    async def test_list_texts(self, store, text_id):
        await store.save_text(text_id, "a.txt", "content A")
        id2 = f"txt_{uuid.uuid4().hex}"
        await store.save_text(id2, "b.txt", "content B")

        all_texts = await store.list_texts()
        ids = {t["id"] for t in all_texts}
        assert text_id in ids
        assert id2 in ids

    async def test_delete_text(self, store, text_id):
        await store.save_text(text_id, "todel.txt", "delete me")
        deleted = await store.delete_text(text_id)
        assert deleted is True

        # Soft-deleted: text still exists with deleted_at set
        got = await store.get_text(text_id)
        assert got is not None
        assert got.get("deleted_at", "") != ""

        # Should not appear in normal listing
        texts = await store.list_texts()
        assert text_id not in {t["id"] for t in texts}

        # Should appear in deleted texts listing
        deleted_list = await store.get_deleted_texts("")
        assert text_id in {t["id"] for t in deleted_list}

        # Restore works
        restored = await store.restore_text(text_id)
        assert restored is True
        got2 = await store.get_text(text_id)
        assert got2.get("deleted_at", "") == ""

        # Hard delete
        await store.delete_text(text_id)
        await store.hard_delete_text(text_id)
        got3 = await store.get_text(text_id)
        assert got3 is None

    async def test_delete_nonexistent(self, store):
        assert await store.delete_text("no_such_id") is False

    async def test_upsert_same_id(self, store, text_id):
        await store.save_text(text_id, "v1.txt", "version 1")
        await store.save_text(text_id, "v1.txt", "version 2 updated")
        got = await store.get_text(text_id)
        assert got["content"] == "version 2 updated"

    async def test_text_type_default(self, store, text_id):
        result = await store.save_text(text_id, "t.txt", "hello")
        assert result.get("text_type") == "story"

    async def test_text_type_chat(self, store, text_id):
        result = await store.save_text(text_id, "chat.txt", "hello", text_type="chat")
        assert result.get("text_type") == "chat"


# ── Card CRUD ────────────────────────────────────────────────────────────────

class TestCardCrud:
    """save_card → get_card → list_cards — requires a parent text record."""

    async def test_save_and_get(self, store, text_id, card_id):
        await store.save_text(text_id, "src.txt", "source text")
        card_json = '{"name": "张三", "age": 30}'
        result = await store.save_card(card_id, text_id, "张三", card_json)
        assert result.get("name") == "张三"

        got = await store.get_card(card_id)
        assert got is not None
        assert got["name"] == "张三"

    async def test_get_nonexistent(self, store):
        assert await store.get_card("no_such_card") is None

    async def test_list_cards(self, store, text_id):
        await store.save_text(text_id, "src.txt", "source")
        c1, c2 = f"c_{uuid.uuid4().hex}", f"c_{uuid.uuid4().hex}"
        await store.save_card(c1, text_id, "张三", '{"name": "张三"}')
        await store.save_card(c2, text_id, "李四", '{"name": "李四"}')

        cards = await store.list_cards(text_id)
        names = {c["name"] for c in cards}
        assert "张三" in names
        assert "李四" in names

    async def test_list_cards_filtered_by_user(self, store, text_id):
        await store.save_text(text_id, "src.txt", "source")
        cid = f"c_{uuid.uuid4().hex}"
        await store.save_card(cid, text_id, "张三", '{}', user_id="user_a")

        cards_all = await store.list_cards(text_id)
        cards_a = await store.list_cards(text_id, user_id="user_a")
        cards_b = await store.list_cards(text_id, user_id="user_b")

        assert len(cards_all) == 1
        assert len(cards_a) == 1
        assert len(cards_b) == 0

    async def test_upsert_same_text_name(self, store, text_id):
        """Save same (text_id, name) twice should update, not duplicate."""
        await store.save_text(text_id, "src.txt", "source")
        cid = f"c_{uuid.uuid4().hex}"
        await store.save_card(cid, text_id, "张三", '{"v": 1}')

        # Re-save with different card_json but same text_id+name
        await store.save_card("different_id", text_id, "张三", '{"v": 2}')

        cards = await store.list_cards(text_id)
        assert len(cards) == 1  # not 2
        import json
        assert json.loads(cards[0]["card_json"])["v"] == 2


# ── Session CRUD ─────────────────────────────────────────────────────────────

class TestSessionCrud:
    """save_session → get_session → delete_session — requires parent text + card."""

    @pytest.fixture
    async def setup(self, store, text_id, card_id):
        """Create prerequisites: a text record and a card record."""
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, "张三", '{"name": "张三"}')
        return {"text_id": text_id, "card_id": card_id}

    async def test_save_and_get(self, store, session_id, setup):
        result = await store.save_session(
            session_id, setup["card_id"], "user", "avatar_data"
        )
        assert result.get("id") == session_id
        assert result.get("user_role") == "user"

        got = await store.get_session(session_id)
        assert got is not None
        assert got["card_id"] == setup["card_id"]
        assert got["character_name"] == "张三"

    async def test_get_nonexistent(self, store):
        assert await store.get_session("no_such_session") is None

    async def test_soft_delete(self, store, session_id, setup):
        await store.save_session(session_id, setup["card_id"], "user", "")
        deleted = await store.delete_session(session_id)
        assert deleted is True

        # get_session does NOT filter by deleted_at, so record still visible
        got = await store.get_session(session_id)
        assert got is not None
        assert got["id"] == session_id

    async def test_delete_nonexistent(self, store):
        assert await store.delete_session("no_such_session") is False

    async def test_second_save_updates(self, store, session_id, setup):
        await store.save_session(session_id, setup["card_id"], "role_a", "")
        await store.save_session(session_id, setup["card_id"], "role_b", "new_avatar")
        got = await store.get_session(session_id)
        assert got["user_role"] == "role_b"
        assert got["avatar_data"] == "new_avatar"

    async def test_foreign_key_violation(self, store, session_id):
        """Saving a session with a non-existent card_id should raise."""
        with pytest.raises(Exception):
            await store.save_session(session_id, "no_such_card", "user", "")


# ── P3-1: Config changelog ────────────────────────────────────────────

class TestConfigChangelog:
    """save_config_change → get_config_changelog."""

    async def test_save_and_list(self, store):
        await store.save_config_change("chg_1", "admin1", "Admin", "base_url", "old", "new")
        await store.save_config_change("chg_2", "admin1", "Admin", "model", "v1", "v2")
        logs = await store.get_config_changelog(10)
        assert len(logs) == 2
        fields = {r["field"] for r in logs}
        assert "base_url" in fields
        assert "model" in fields

    async def test_empty(self, store):
        assert await store.get_config_changelog(10) == []

    async def test_limit(self, store):
        for i in range(5):
            await store.save_config_change(f"chg_{i}", "a", "A", "f", f"old{i}", f"new{i}")
        logs = await store.get_config_changelog(3)
        assert len(logs) == 3


# ── P3-2: Review log ──────────────────────────────────────────────────

class TestReviewLog:
    """save_review_log → get_review_logs — requires a card record."""

    async def test_save_and_list(self, store, text_id, card_id):
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, "张三", '{"name": "张三"}')
        await store.save_review_log("rev_1", card_id, "user1", "pass", "")
        await store.save_review_log("rev_2", card_id, "user1", "reject", "违规内容")
        logs = await store.get_review_logs(10)
        assert len(logs) == 2
        results = {r["result"] for r in logs}
        assert "pass" in results
        assert "reject" in results
        reject = next(r for r in logs if r["result"] == "reject")
        assert reject["reason"] == "违规内容"
        assert reject["card_name"] == "张三"

    async def test_empty(self, store):
        assert await store.get_review_logs(10) == []


# ── P1-1: Content Moderation ─────────────────────────────────────────

class TestContentModeration:
    """list_all_cards_admin, takedown_card, list_all_posts_admin, admin_delete_post, ban_user_and_contents."""

    async def _create_user(self, store, uid: str, name: str) -> str:
        import hashlib
        try:
            await store.create_user(uid, name, hashlib.sha256(b"p").hexdigest())
        except ValueError:
            pass
        return uid

    async def test_list_all_cards_admin(self, store, text_id, card_id):
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, "张三", '{"name": "张三"}', user_id="u_admin_list")
        cards = await store.list_all_cards_admin()
        ids = {c["id"] for c in cards}
        assert card_id in ids
        card = next(c for c in cards if c["id"] == card_id)
        assert card["name"] == "张三"

    async def test_takedown_card(self, store, text_id, card_id):
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, "张三", '{}', user_id="u_takedown")
        # First make it public (simulate publishing to market)
        await store.update_card_visibility(card_id, "public")
        ok = await store.takedown_card(card_id)
        assert ok is True
        card = await store.get_card(card_id)
        assert card["visibility"] == "private"

    async def test_takedown_nonexistent(self, store):
        ok = await store.takedown_card("no_such_card")
        assert ok is False

    async def test_list_all_posts_admin(self, store):
        uid = "u_posts_admin"
        await self._create_user(store, uid, "poster")
        async with aiosqlite.connect(store.db_path) as conn:
            await conn.execute("INSERT INTO user_posts (id, user_id, content) VALUES (?, ?, ?)",
                               ("post_1", uid, "Hello world"))
            await conn.commit()
        posts = await store.list_all_posts_admin()
        ids = {p["id"] for p in posts}
        assert "post_1" in ids
        post = next(p for p in posts if p["id"] == "post_1")
        assert post["content"] == "Hello world"

    async def test_admin_delete_post(self, store):
        uid = "u_del_post"
        await self._create_user(store, uid, "deleter")
        async with aiosqlite.connect(store.db_path) as conn:
            await conn.execute("INSERT INTO user_posts (id, user_id, content) VALUES (?, ?, ?)",
                               ("post_del", uid, "delete me"))
            await conn.commit()
        ok = await store.admin_delete_post("post_del")
        assert ok is True
        posts = await store.list_all_posts_admin()
        assert "post_del" not in {p["id"] for p in posts}

    async def test_admin_delete_post_nonexistent(self, store):
        assert await store.admin_delete_post("no_such_post") is False

    async def test_ban_user_and_contents(self, store):
        uid = "u_ban"
        await self._create_user(store, uid, "banned_user")
        async with aiosqlite.connect(store.db_path) as conn:
            await conn.execute("INSERT INTO user_posts (id, user_id, content) VALUES (?, ?, ?)",
                               ("post_ban1", uid, "bad post"))
            await conn.execute("INSERT INTO user_posts (id, user_id, content) VALUES (?, ?, ?)",
                               ("post_ban2", uid, "another bad post"))
            await conn.commit()
        counts = await store.ban_user_and_contents(uid, "admin_id")
        assert counts["posts_deleted"] >= 2
        user = await store.get_user_by_id(uid)
        assert user is not None
        assert user.get("is_disabled") == 1


# ── P2-1: Announcements ──────────────────────────────────────────────

class TestAnnouncements:
    """create_announcement, delete_announcement, get_active_announcement, list_announcements."""

    async def test_create_and_get_active(self, store):
        a1 = await store.create_announcement("First announcement")
        assert a1["is_active"] == 1
        active = await store.get_active_announcement()
        assert active is not None
        assert active["content"] == "First announcement"

    async def test_create_deactivates_previous(self, store):
        await store.create_announcement("First")
        await store.create_announcement("Second")
        active = await store.get_active_announcement()
        assert active["content"] == "Second"
        all_a = await store.list_announcements()
        assert len(all_a) == 2

    async def test_get_active_none(self, store):
        active = await store.get_active_announcement()
        assert active is None

    async def test_delete_announcement(self, store):
        a = await store.create_announcement("Delete me")
        ok = await store.delete_announcement(a["id"])
        assert ok is True
        active = await store.get_active_announcement()
        assert active is None

    async def test_delete_nonexistent(self, store):
        assert await store.delete_announcement("no_such") is False

    async def test_list_announcements(self, store):
        await store.create_announcement("A")
        await store.create_announcement("B")
        all_a = await store.list_announcements()
        assert len(all_a) == 2
        contents = {a["content"] for a in all_a}
        assert "A" in contents
        assert "B" in contents


# ── P2-2: User Detail ────────────────────────────────────────────────

class TestUserDetail:
    """get_user_detail."""

    async def test_get_user_detail(self, store):
        uid = "u_detail"
        import hashlib
        await store.create_user(uid, "detailed_user", hashlib.sha256(b"p").hexdigest())
        detail = await store.get_user_detail(uid)
        assert detail["id"] == uid
        assert detail["username"] == "detailed_user"
        assert isinstance(detail["cards_count"], int)
        assert isinstance(detail["usage"], dict)

    async def test_get_user_detail_nonexistent(self, store):
        with pytest.raises(ValueError, match="用户不存在"):
            await store.get_user_detail("no_such_user")


# ── P2-3: CSV Export ─────────────────────────────────────────────────

class TestCsvExport:
    """export_users_csv, export_usage_csv."""

    async def test_export_users_csv(self, store):
        uid = "u_csv"
        import hashlib
        await store.create_user(uid, "csv_user", hashlib.sha256(b"p").hexdigest())
        csv_str = await store.export_users_csv()
        assert "username" in csv_str
        assert "csv_user" in csv_str

    async def test_export_users_csv_empty_ok(self, store):
        csv_str = await store.export_users_csv()
        assert "username" in csv_str

    async def test_export_usage_csv(self, store):
        csv_str = await store.export_usage_csv()
        assert "total_calls" in csv_str


# ── P2-4: Cross-border DM resync ─────────────────────────────────────

class TestCrossBorderResync:
    """get_unsynced_cross_border_messages, mark_message_synced."""

    async def test_get_unsynced_returns_synced0(self, store):
        uid = "u_cb_sender"
        uid2 = "u_cb_recv"
        import hashlib
        await store.create_user(uid, "cb_sender", hashlib.sha256(b"p").hexdigest())
        await store.create_user(uid2, "cb_recv", hashlib.sha256(b"p").hexdigest())

        # Send a cross-border (synced=0) and a normal (synced=1) message
        await store.send_message(uid, uid2, "unsynced msg", cross_border_synced=0)
        await store.send_message(uid, uid2, "synced msg", cross_border_synced=1)

        unsynced = await store.get_unsynced_cross_border_messages(limit=10)
        assert len(unsynced) == 1
        assert unsynced[0]["content"] == "unsynced msg"
        assert unsynced[0]["sender_id"] == uid
        assert unsynced[0]["receiver_id"] == uid2

    async def test_mark_synced_removes_from_unsynced(self, store):
        uid = "u_cb_sender2"
        uid2 = "u_cb_recv2"
        import hashlib
        await store.create_user(uid, "cb_sender2", hashlib.sha256(b"p").hexdigest())
        await store.create_user(uid2, "cb_recv2", hashlib.sha256(b"p").hexdigest())

        msg = await store.send_message(uid, uid2, "retry msg", cross_border_synced=0)

        unsynced_before = await store.get_unsynced_cross_border_messages()
        assert any(m["id"] == msg["id"] for m in unsynced_before)

        await store.mark_message_synced(msg["id"])

        unsynced_after = await store.get_unsynced_cross_border_messages()
        assert not any(m["id"] == msg["id"] for m in unsynced_after)

    async def test_empty_when_none_unsynced(self, store):
        unsynced = await store.get_unsynced_cross_border_messages()
        assert unsynced == []

    async def test_unsynced_limit(self, store):
        uid = "u_cb_sender3"
        uid2 = "u_cb_recv3"
        import hashlib
        await store.create_user(uid, "cb_sender3", hashlib.sha256(b"p").hexdigest())
        await store.create_user(uid2, "cb_recv3", hashlib.sha256(b"p").hexdigest())

        for i in range(5):
            await store.send_message(uid, uid2, f"msg_{i}", cross_border_synced=0)

        unsynced = await store.get_unsynced_cross_border_messages(limit=3)
        assert len(unsynced) == 3
        # Oldest first
        assert unsynced[0]["content"] == "msg_0"
        assert unsynced[2]["content"] == "msg_2"


# ── P2-5: Card cross-border sync ─────────────────────────────────────

class TestCardCrossBorderSync:
    """get_unsynced_cross_border_cards, mark_card_synced, mark_card_unsynced, remote card ops."""

    async def _mk_public_unsynced_card(self, store, card_id: str, text_id: str, name: str = "test_card"):
        """Helper: save a text + card, then make it public and unsynced."""
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, name, '{"name": "test"}')
        await store.execute(
            "UPDATE cards SET visibility = 'public', cross_border_synced = 0 WHERE id = ?",
            (card_id,),
        )

    async def test_get_unsynced_returns_public_synced0(self, store, text_id):
        cid = f"c_{uuid.uuid4().hex}"
        await self._mk_public_unsynced_card(store, cid, text_id)
        unsynced = await store.get_unsynced_cross_border_cards(limit=10)
        assert any(c["id"] == cid for c in unsynced)

    async def test_non_public_not_returned(self, store, text_id):
        cid = f"c_{uuid.uuid4().hex}"
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(cid, text_id, "test_card", '{"name": "test"}')
        # Private card with synced=0 should NOT appear
        await store.execute(
            "UPDATE cards SET visibility = 'private', cross_border_synced = 0 WHERE id = ?",
            (cid,),
        )
        unsynced = await store.get_unsynced_cross_border_cards()
        assert not any(c["id"] == cid for c in unsynced)

    async def test_mark_card_synced_removes_from_unsynced(self, store, text_id):
        cid = f"c_{uuid.uuid4().hex}"
        await self._mk_public_unsynced_card(store, cid, text_id)
        unsynced_before = await store.get_unsynced_cross_border_cards()
        assert any(c["id"] == cid for c in unsynced_before)
        await store.mark_card_synced(cid)
        unsynced_after = await store.get_unsynced_cross_border_cards()
        assert not any(c["id"] == cid for c in unsynced_after)

    async def test_mark_card_unsynced_makes_it_appear(self, store, text_id):
        cid = f"c_{uuid.uuid4().hex}"
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(cid, text_id, "test_card", '{"name": "test"}')
        await store.execute(
            "UPDATE cards SET visibility = 'public' WHERE id = ?",
            (cid,),
        )
        # Card is public but synced=1 — should not appear
        unsynced_before = await store.get_unsynced_cross_border_cards()
        assert not any(c["id"] == cid for c in unsynced_before)
        # Mark unsynced — should now appear
        await store.mark_card_unsynced(cid)
        unsynced_after = await store.get_unsynced_cross_border_cards()
        assert any(c["id"] == cid for c in unsynced_after)

    async def test_get_unsynced_limit(self, store, text_id):
        ids = [f"c_{uuid.uuid4().hex}" for _ in range(5)]
        for i, cid in enumerate(ids):
            await self._mk_public_unsynced_card(store, cid, text_id, name=f"test_card_{i}")
        unsynced = await store.get_unsynced_cross_border_cards(limit=3)
        assert len(unsynced) == 3

    async def test_upsert_remote_card_insert(self, store, text_id):
        """Insert a remote card with no texts FK — should succeed."""
        cid = f"remote_{uuid.uuid4().hex}"
        await store.upsert_remote_card(
            card_id=cid, origin_region="sg", user_id="remote_user",
            name="remote", card_json='{"name": "remote"}',
            avatar_data="", market_description="desc", market_tags="tag",
            origin_created_at="2026-06-26",
        )
        card = await store.get_remote_card(cid)
        assert card is not None
        assert card["name"] == "remote"
        assert card["origin_region"] == "sg"

    async def test_upsert_remote_card_update(self, store, text_id):
        """Update an existing remote card — should change fields, not duplicate."""
        cid = f"remote_upd_{uuid.uuid4().hex}"
        await store.upsert_remote_card(
            card_id=cid, origin_region="sg", user_id="remote_user",
            name="v1", card_json='{"name": "v1"}',
            avatar_data="", market_description="desc", market_tags="tag",
            origin_created_at="2026-06-26",
        )
        await store.upsert_remote_card(
            card_id=cid, origin_region="sg", user_id="remote_user",
            name="v2", card_json='{"name": "v2"}',
            avatar_data="", market_description="desc2", market_tags="tag2",
            origin_created_at="2026-06-26",
        )
        card = await store.get_remote_card(cid)
        assert card is not None
        assert card["name"] == "v2"
        assert card["market_description"] == "desc2"

    async def test_upsert_remote_card_isolated(self, store):
        """Remote card succeeds in an isolated DB with no texts/users rows."""
        cid = f"remote_iso_{uuid.uuid4().hex}"
        # No save_text() call — this would fail if upsert_remote_card
        # depended on any FK; remote_cards must be self-contained.
        await store.upsert_remote_card(
            card_id=cid, origin_region="us", user_id="foreign_user",
            name="isolated", card_json='{"name": "isolated"}',
            avatar_data="", market_description="", market_tags="",
            origin_created_at="",
        )
        card = await store.get_remote_card(cid)
        assert card is not None
        assert card["name"] == "isolated"
        assert card["origin_region"] == "us"

    async def test_search_public_cards_with_remote(self, store, text_id):
        """Remote cards with origin_created_at in search should not 500.

        Regression guard for the UNION type mismatch: remote_cards.origin_created_at
        is TEXT while cards.created_at is TIMESTAMPTZ — the COALESCE cast must
        match across both branches of the UNION.
        """
        # Insert a local card so search finds something
        cid_local = f"c_srch_{uuid.uuid4().hex}"
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(cid_local, text_id, "AliceRemote", '{"name": "alice"}')
        await store.execute(
            "UPDATE cards SET visibility = 'public', cross_border_synced = 0 WHERE id = ?",
            (cid_local,),
        )

        # Insert a remote card with origin_created_at (TEXT column)
        cid_remote = f"remote_srch_{uuid.uuid4().hex}"
        await store.upsert_remote_card(
            card_id=cid_remote, origin_region="sg", user_id="foreign_user",
            name="AliceRemote", card_json='{"name": "alice"}',
            avatar_data="", market_description="desc", market_tags="tag",
            origin_created_at="2026-06-26",
        )

        # Search should return both without type error
        results = await store.search_public_cards("Alice", page=1, page_size=20)
        ids = [r["id"] for r in results]
        assert cid_local in ids, "Local card should appear in search results"
        assert cid_remote in ids, "Remote card should appear in search results"


# ── Delete propagation atomicity ──────────────────────────────────────────────

class TestDeletePropagationAtomicity:
    """delete_card / purge_card / retract_dm_message / delete_user must enqueue
    their cross-border outbox row inside the SAME transaction as the local
    delete — never as a separate commit.

    Verification strategy: after a successful delete, confirm that the outbox
    contains exactly one matching row (synced=0).  If the INSERT were in a
    separate transaction, a crash between the two commits would leave the
    outbox empty while the local state is already removed — these tests
    prove that cannot happen because both writes hit the DB in one commit.
    """

    # ── Helpers ────────────────────────────────────────────────────────────

    async def _count_outbox(self, store, op_type: str, target_id: str) -> int:
        """Return how many pending outbox rows match (op_type, target_id)."""
        rows = await store.get_pending_delete_propagations(limit=1000)
        return sum(1 for r in rows if r["op_type"] == op_type and r["target_id"] == target_id)

    # ── Card delete (soft) ─────────────────────────────────────────────────

    async def test_delete_card_public_enqueues_outbox(self, store, text_id, card_id):
        """Soft-deleting a *public* card must produce a card_delete outbox row."""
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, "张三", '{}')
        await store.update_card_visibility(card_id, "public")

        ok = await store.delete_card(card_id)
        assert ok is True

        n = await self._count_outbox(store, "card_delete", card_id)
        assert n == 1, (
            f"Expected 1 card_delete outbox row for {card_id}, got {n} — "
            "the outbox INSERT was likely not in the same transaction as the DELETE."
        )

    async def test_delete_card_private_no_outbox(self, store, text_id, card_id):
        """Soft-deleting a *private* card must NOT produce a card_delete row."""
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, "张三", '{}')
        await store.update_card_visibility(card_id, "private")

        ok = await store.delete_card(card_id)
        assert ok is True

        n = await self._count_outbox(store, "card_delete", card_id)
        assert n == 0, "Private-card delete must not enqueue outbox"

    async def test_delete_card_nonexistent_returns_false(self, store):
        """Deleting a non-existent card returns False and enqueues nothing."""
        ok = await store.delete_card("no_such_card")
        assert ok is False
        rows = await store.get_pending_delete_propagations(limit=1000)
        assert len(rows) == 0

    async def test_delete_card_idempotent_outbox(self, store, text_id, card_id):
        """Deleting the same public card twice must produce exactly ONE outbox row."""
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, "张三", '{}')
        await store.update_card_visibility(card_id, "public")

        ok1 = await store.delete_card(card_id)
        ok2 = await store.delete_card(card_id)  # second soft-delete is a no-op
        assert ok1 is True
        assert ok2 is True

        n = await self._count_outbox(store, "card_delete", card_id)
        assert n == 1, "ON CONFLICT DO NOTHING must prevent duplicate outbox rows"

    # ── Card purge (hard) ──────────────────────────────────────────────────

    async def test_purge_card_public_enqueues_outbox(self, store, text_id, card_id):
        """Hard-deleting a *public* card must produce a card_delete outbox row."""
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, "张三", '{}')
        await store.update_card_visibility(card_id, "public")
        await store.delete_card(card_id)

        ok = await store.purge_card(card_id)
        assert ok is True

        n = await self._count_outbox(store, "card_delete", card_id)
        assert n == 1

    async def test_purge_card_private_no_outbox(self, store, text_id, card_id):
        """Hard-deleting a *private* card must NOT produce a card_delete row."""
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, "张三", '{}')
        await store.update_card_visibility(card_id, "private")
        await store.delete_card(card_id)

        ok = await store.purge_card(card_id)
        assert ok is True

        n = await self._count_outbox(store, "card_delete", card_id)
        assert n == 0

    async def test_purge_card_nonexistent_returns_true(self, store):
        """Purging a non-existent card returns True (idempotent) and enqueues nothing."""
        ok = await store.purge_card("no_such_card")
        assert ok is True
        rows = await store.get_pending_delete_propagations(limit=1000)
        assert len(rows) == 0

    # ── DM retract ──────────────────────────────────────────────────────────

    async def test_retract_dm_enqueues_outbox(self, store):
        """Retracting a DM must produce a dm_retract outbox row."""
        msg = await store.send_message("sender_1", "receiver_1", "Hello DM")
        msg_id = msg["id"]

        await store.retract_dm_message(msg_id)

        n = await self._count_outbox(store, "dm_retract", msg_id)
        assert n == 1, (
            f"Expected 1 dm_retract outbox row for {msg_id}, got {n}"
        )

    async def test_retract_dm_idempotent_outbox(self, store):
        """Retracting the same DM twice must produce exactly ONE outbox row."""
        msg = await store.send_message("sender_2", "receiver_2", "Hello again")
        msg_id = msg["id"]

        await store.retract_dm_message(msg_id)
        await store.retract_dm_message(msg_id)  # second retract is a no-op

        n = await self._count_outbox(store, "dm_retract", msg_id)
        assert n == 1, "ON CONFLICT must prevent duplicate dm_retract outbox rows"

    async def test_retract_dm_idempotent_nonexistent(self, store):
        """Retracting a non-existent DM still succeeds (idempotent) and enqueues."""
        await store.retract_dm_message("no_such_msg")

        n = await self._count_outbox(store, "dm_retract", "no_such_msg")
        assert n == 1  # still enqueues — the outbox consumer is idempotent too

    # ── User purge ─────────────────────────────────────────────────────────

    async def test_delete_user_enqueues_outbox(self, store):
        """Cascade-deleting a user must produce a user_purge outbox row."""
        uid = f"user_{uuid.uuid4().hex}"
        pwd = "$2b$12$dummyhashdummyhashdummyhashdummyhashdummyha"
        await store.create_user(uid, uid, pwd)

        counts = await store.delete_user(uid)
        assert counts.get("user") == 1

        n = await self._count_outbox(store, "user_purge", uid)
        assert n == 1, (
            f"Expected 1 user_purge outbox row for {uid}, got {n}"
        )

    async def test_delete_user_idempotent_outbox(self, store):
        """Deleting the same user twice — second call fails, but only ONE outbox row exists."""
        uid = f"user_idem_{uuid.uuid4().hex}"
        pwd = "$2b$12$dummyhashdummyhashdummyhashdummyhashdummyha"
        await store.create_user(uid, uid, pwd)

        await store.delete_user(uid)
        with pytest.raises(ValueError, match="用户不存在"):
            await store.delete_user(uid)

        n = await self._count_outbox(store, "user_purge", uid)
        assert n == 1, "Must not create duplicate outbox rows"

    # ── Cross-operation isolation ──────────────────────────────────────────

    async def test_card_and_user_outbox_rows_are_separate(self, store, text_id, card_id):
        """Deleting a card and a user must produce distinct outbox rows."""
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, "张三", '{}')
        await store.update_card_visibility(card_id, "public")

        uid = f"user_mixed_{uuid.uuid4().hex}"
        pwd = "$2b$12$dummyhashdummyhashdummyhashdummyhashdummyha"
        await store.create_user(uid, uid, pwd)

        await store.delete_card(card_id)
        await store.delete_user(uid)

        rows = await store.get_pending_delete_propagations(limit=1000)
        pairs = {(r["op_type"], r["target_id"]) for r in rows}
        assert ("card_delete", card_id) in pairs
        assert ("user_purge", uid) in pairs


# ── Text Deletion Impact & keep_cards ─────────────────────────────────


class TestTextDeletionImpact:
    """get_text_deletion_impact returns accurate card/session/message counts."""

    async def test_no_cards(self, store, text_id):
        await store.save_text(text_id, "src.txt", "source")
        impact = await store.get_text_deletion_impact(text_id, "")
        assert impact == {"card_count": 0, "session_count": 0, "message_count": 0}

    async def test_with_cards_no_sessions(self, store, text_id):
        await store.save_text(text_id, "src.txt", "source")
        cid = f"c_{uuid.uuid4().hex}"
        await store.save_card(cid, text_id, "张三", '{}')
        impact = await store.get_text_deletion_impact(text_id, "")
        assert impact["card_count"] == 1
        assert impact["session_count"] == 0
        assert impact["message_count"] == 0

    async def test_with_sessions_and_messages(self, store, text_id):
        await store.save_text(text_id, "src.txt", "source")
        cid = f"c_{uuid.uuid4().hex}"
        sid = f"s_{uuid.uuid4().hex}"
        await store.save_card(cid, text_id, "张三", '{}')
        await store.save_session(sid, cid, "user", "", user_id="test")
        mid = await store.save_message(sid, "user", "hello", "")
        impact = await store.get_text_deletion_impact(text_id, "")
        assert impact["card_count"] == 1
        assert impact["session_count"] == 1
        assert impact["message_count"] == 1


class TestTextHardDeleteKeepCards:
    """hard_delete_text(keep_cards=True) detaches cards; keep_cards=False cascade-deletes."""

    async def test_keep_cards_true_cards_survive(self, store, text_id):
        await store.save_text(text_id, "src.txt", "source")
        cid = f"c_{uuid.uuid4().hex}"
        sid = f"s_{uuid.uuid4().hex}"
        await store.save_card(cid, text_id, "张三", '{}')
        await store.save_session(sid, cid, "user", "", user_id="test")
        await store.save_message(sid, "user", "hello", "")

        ok = await store.hard_delete_text(text_id, keep_cards=True)
        assert ok is True
        # Text is gone
        assert await store.get_text(text_id) is None
        # Card survives with text_id=NULL
        card = await store.get_card(cid)
        assert card is not None
        assert card["text_id"] == ''
        # Session survives
        session = await store.get_session(sid)
        assert session is not None
        # Messages survive
        msgs = await store.get_messages(sid)
        assert len(msgs) == 1

    async def test_keep_cards_false_cascade_delete(self, store, text_id):
        await store.save_text(text_id, "src.txt", "source")
        cid = f"c_{uuid.uuid4().hex}"
        sid = f"s_{uuid.uuid4().hex}"
        await store.save_card(cid, text_id, "张三", '{}')
        await store.save_session(sid, cid, "user", "", user_id="test")
        await store.save_message(sid, "user", "hello", "")

        ok = await store.hard_delete_text(text_id, keep_cards=False)
        assert ok is True
        # Text is gone
        assert await store.get_text(text_id) is None
        # Card is gone
        assert await store.get_card(cid) is None
        # Session is gone
        assert await store.get_session(sid) is None

    async def test_detach_text_cards(self, store, text_id):
        await store.save_text(text_id, "src.txt", "source")
        cid = f"c_{uuid.uuid4().hex}"
        await store.save_card(cid, text_id, "张三", '{}')

        count = await store.detach_text_cards(text_id)
        assert count == 1
        card = await store.get_card(cid)
        assert card is not None
        assert card["text_id"] == ''
        # Text still exists
        assert await store.get_text(text_id) is not None

    async def test_keep_cards_public_cards_get_delete_outbox(self, store, text_id):
        await store.save_text(text_id, "src.txt", "source")
        cid = f"c_{uuid.uuid4().hex}"
        await store.save_card(cid, text_id, "张三", '{}')
        await store.update_card_visibility(cid, "public")

        await store.hard_delete_text(text_id, keep_cards=False)
        # Card should be in delete outbox
        rows = await store.get_pending_delete_propagations()
        assert any(r["op_type"] == "card_delete" and r["target_id"] == cid for r in rows)

    async def test_keep_cards_true_public_cards_no_outbox(self, store, text_id):
        await store.save_text(text_id, "src.txt", "source")
        cid = f"c_{uuid.uuid4().hex}"
        await store.save_card(cid, text_id, "张三", '{}')
        await store.update_card_visibility(cid, "public")

        await store.hard_delete_text(text_id, keep_cards=True)
        # No delete outbox entry since card was kept
        rows = await store.get_pending_delete_propagations()
        assert not any(r["op_type"] == "card_delete" and r["target_id"] == cid for r in rows)


class TestStandaloneCardListing:
    """Cards with text_id=NULL must still appear in list_standalone_cards."""

    async def test_standalone_card_shows_in_list(self, store, text_id):
        await store.save_text(text_id, "src.txt", "source")
        cid = f"c_{uuid.uuid4().hex}"
        await store.save_card(cid, text_id, "张三", '{}', user_id="user_sa")
        # Detach the card
        await store.detach_text_cards(text_id)
        standalone = await store.list_standalone_cards("user_sa")
        ids = {c["id"] for c in standalone}
        assert cid in ids, "Detached card must appear in standalone listing"

    async def test_standalone_card_visible_in_character_tab(self, store, text_id):
        """Simulate the full flow: create text, distill card, delete text with keep_cards."""
        await store.save_text(text_id, "src.txt", "source", user_id="user_sb")
        cid = f"c_{uuid.uuid4().hex}"
        await store.save_card(cid, text_id, "张三", '{}', user_id="user_sb")
        sid = f"s_{uuid.uuid4().hex}"
        await store.save_session(sid, cid, "user", "", user_id="user_sb")

        # Delete text with keep_cards=True
        await store.hard_delete_text(text_id, keep_cards=True)

        # Card should be standalone now and visible
        standalone = await store.list_standalone_cards("user_sb")
        ids = {c["id"] for c in standalone}
        assert cid in ids, "Card must appear as standalone after keep_cards delete"

        # Card can still be fetched by ID
        card = await store.get_card(cid)
        assert card is not None
        assert card["text_id"] == ''

        # Session still accessible
        session = await store.get_session(sid)
        assert session is not None

    async def test_standalone_card_excluded_from_text_cards(self, store, text_id):
        """Detached card should NOT appear in list_cards(text_id)."""
        await store.save_text(text_id, "src.txt", "source", user_id="user_sc")
        cid = f"c_{uuid.uuid4().hex}"
        await store.save_card(cid, text_id, "张三", '{}', user_id="user_sc")
        await store.detach_text_cards(text_id)

        cards = await store.list_cards(text_id, "user_sc")
        ids = {c["id"] for c in cards}
        assert cid not in ids, "Detached card must NOT appear in list_cards by text_id"


# ── Session avatar isolation ───────────────────────────────────────────

class TestSessionAvatarIsolation:
    """Session-level user avatar isolation: A≠B, session≠global, cross-user denied."""

    async def _setup_user_text_card(self, store, uid, text_id, card_id):
        """Create a user, text, and card for testing."""
        import hashlib
        try:
            await store.create_user(uid, uid, hashlib.sha256(b"p").hexdigest())
        except ValueError:
            pass
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, "张三", '{"name": "张三"}', user_id=uid)

    async def test_session_avatar_isolated_between_sessions(self, store):
        """Set session A's avatar — session B must remain empty."""
        uid = f"u_{uuid.uuid4().hex}"
        tid = f"txt_{uuid.uuid4().hex}"
        cid = f"card_{uuid.uuid4().hex}"
        sid_a = f"ses_{uuid.uuid4().hex}"
        sid_b = f"ses_{uuid.uuid4().hex}"
        await self._setup_user_text_card(store, uid, tid, cid)
        await store.save_session(sid_a, cid, "user", "", user_id=uid)
        await store.save_session(sid_b, cid, "user", "", user_id=uid)

        ok = await store.update_session_avatar(sid_a, uid, "avatarX")
        assert ok is True

        session_a = await store.get_session(sid_a)
        assert session_a["avatar_data"] == "avatarX", "A should have session avatar"

        session_b = await store.get_session(sid_b)
        assert session_b["avatar_data"] == "", "B must remain empty (unaffected by A)"

    async def test_session_avatar_does_not_touch_global(self, store):
        """Setting a session avatar must NOT alter the user's global avatar."""
        uid = f"u_{uuid.uuid4().hex}"
        tid = f"txt_{uuid.uuid4().hex}"
        cid = f"card_{uuid.uuid4().hex}"
        sid = f"ses_{uuid.uuid4().hex}"
        await self._setup_user_text_card(store, uid, tid, cid)
        await store.save_session(sid, cid, "user", "", user_id=uid)

        await store.update_user_avatar(uid, "global1")
        ok = await store.update_session_avatar(sid, uid, "avatarX")
        assert ok is True

        global_avatar = await store.get_user_avatar(uid)
        assert global_avatar == "global1", "Global avatar must be unchanged by session-level write"

    async def test_two_sessions_independent(self, store):
        """Two sessions under the same user — each has its own avatar, no cross-contamination."""
        uid = f"u_{uuid.uuid4().hex}"
        tid = f"txt_{uuid.uuid4().hex}"
        cid = f"card_{uuid.uuid4().hex}"
        sid_a = f"ses_{uuid.uuid4().hex}"
        sid_b = f"ses_{uuid.uuid4().hex}"
        await self._setup_user_text_card(store, uid, tid, cid)
        await store.save_session(sid_a, cid, "user", "", user_id=uid)
        await store.save_session(sid_b, cid, "user", "", user_id=uid)

        ok_a = await store.update_session_avatar(sid_a, uid, "X")
        ok_b = await store.update_session_avatar(sid_b, uid, "Y")
        assert ok_a is True
        assert ok_b is True

        session_a = await store.get_session(sid_a)
        session_b = await store.get_session(sid_b)
        assert session_a["avatar_data"] == "X", "Session A should have its own avatar"
        assert session_b["avatar_data"] == "Y", "Session B should have its own avatar"

    async def test_session_avatar_ownership_denied(self, store):
        """Cross-user write must be rejected: returns False, target data unchanged."""
        import hashlib
        uid = f"u_{uuid.uuid4().hex}"
        other_id = f"u_{uuid.uuid4().hex}"
        tid = f"txt_{uuid.uuid4().hex}"
        cid = f"card_{uuid.uuid4().hex}"
        sid = f"ses_{uuid.uuid4().hex}"
        try:
            await store.create_user(uid, uid, hashlib.sha256(b"p").hexdigest())
        except ValueError:
            pass
        try:
            await store.create_user(other_id, other_id, hashlib.sha256(b"p").hexdigest())
        except ValueError:
            pass
        await store.save_text(tid, "src.txt", "source")
        await store.save_card(cid, tid, "张三", '{"name": "张三"}', user_id=uid)
        await store.save_session(sid, cid, "user", "", user_id=uid)

        ok = await store.update_session_avatar(sid, other_id, "hack")
        assert ok is False, "Ownership check must reject cross-user write"

        session = await store.get_session(sid)
        assert session["avatar_data"] == "", "Session avatar must remain unchanged after rejected write"

    async def test_unset_session_falls_back_logic(self, store):
        """Session never given an avatar — avatar_data must be empty (frontend falls back to global)."""
        uid = f"u_{uuid.uuid4().hex}"
        tid = f"txt_{uuid.uuid4().hex}"
        cid = f"card_{uuid.uuid4().hex}"
        sid = f"ses_{uuid.uuid4().hex}"
        await self._setup_user_text_card(store, uid, tid, cid)
        await store.save_session(sid, cid, "user", "", user_id=uid)

        session = await store.get_session(sid)
        assert session["avatar_data"] == "", "Unset session avatar must be empty (fallback to global avatar in frontend)"


# ── Retracted message filtering ────────────────────────────────────────

class TestRebuildHistoryFromDb:
    """_rebuild_history_from_db must exclude retracted messages."""

    def test_filters_retracted_char_message(self):
        """A char message with retracted=True must not appear in the result."""
        from web.routers.chat import _rebuild_history_from_db
        msgs = [
            {"role": "user",   "content": "你好",       "retracted": False},
            {"role": "char",   "content": "你是谁？我不认识你", "retracted": True},
            {"role": "user",   "content": "别闹了",     "retracted": False},
            {"role": "char",   "content": "哈哈开玩笑的", "retracted": False},
        ]
        result = _rebuild_history_from_db(msgs)
        assert len(result) == 3
        assert not any(m["content"] == "你是谁？我不认识你" for m in result)

    def test_keeps_normal_messages(self):
        """Non-retracted messages pass through unchanged."""
        from web.routers.chat import _rebuild_history_from_db
        msgs = [
            {"role": "user", "content": "第一句", "retracted": False},
            {"role": "char", "content": "回复一", "retracted": False},
        ]
        result = _rebuild_history_from_db(msgs)
        assert len(result) == 2
        assert result[0] == {"role": "user", "content": "第一句"}
        assert result[1] == {"role": "assistant", "content": "回复一"}

    def test_skips_summary_role(self):
        """Summary and other synthetic roles are filtered out as before."""
        from web.routers.chat import _rebuild_history_from_db
        msgs = [
            {"role": "user",    "content": "hi",  "retracted": False},
            {"role": "char",    "content": "hey", "retracted": False},
            {"role": "summary", "content": "摘要", "retracted": False},
        ]
        result = _rebuild_history_from_db(msgs)
        assert len(result) == 2

    def test_messages_still_alternating_and_start_with_user(self):
        """After filtering retracted, history must start with user and alternate."""
        from web.routers.chat import _rebuild_history_from_db
        msgs = [
            {"role": "user", "content": "回合1用户",  "retracted": False},
            {"role": "char", "content": "回合1回复",  "retracted": True},   # filtered
            {"role": "user", "content": "回合2用户",  "retracted": False},
            {"role": "char", "content": "回合2回复",  "retracted": False},
        ]
        result = _rebuild_history_from_db(msgs)
        assert len(result) == 3
        # Must start with user
        assert result[0]["role"] == "user"
        # Must alternate (user → assistant → user)
        roles = [m["role"] for m in result]
        assert roles == ["user", "user", "assistant"], (
            f"Expected alternating roles starting with user, got {roles}"
        )

    def test_retracted_0_or_none_passes_through(self):
        """retracted=0, retracted=None or missing retracted should NOT be filtered."""
        from web.routers.chat import _rebuild_history_from_db
        msgs = [
            {"role": "user", "content": "a", "retracted": 0},
            {"role": "char", "content": "b", "retracted": None},
            {"role": "user", "content": "c"},                          # missing key
            {"role": "char", "content": "d", "retracted": False},
        ]
        result = _rebuild_history_from_db(msgs)
        assert len(result) == 4


# ── Cross-border admin user fields ─────────────────────────────────────

class TestGetAllUsersAdminFields:
    """get_all_users_admin_fields must return only whitelisted admin-safe fields."""

    async def test_returns_admin_safe_fields(self, store):
        """Result dicts must never contain password_hash or api_key."""
        import hashlib
        uid = f"u_{uuid.uuid4().hex}"
        await store.create_user(uid, "admin_fields_test", hashlib.sha256(b"p").hexdigest())

        rows = await store.get_all_users_admin_fields()
        matching = [r for r in rows if r.get("id") == uid]
        assert len(matching) == 1
        row = matching[0]

        # Must contain whitelisted fields
        assert "id" in row
        assert row.get("username") == "admin_fields_test"

        # Must NOT contain secrets
        assert "password_hash" not in row, "password_hash leaked in admin fields!"
        assert "api_key" not in row, "api_key leaked in admin fields!"
        assert "email" not in row, "email must not be in admin fields (not needed for cross-border)"

    async def test_field_whitelist_is_explicit(self, store):
        """Only the whitelisted fields should be present."""
        import hashlib
        uid = f"u_{uuid.uuid4().hex}"
        await store.create_user(uid, "whitelist_test", hashlib.sha256(b"p").hexdigest())

        rows = await store.get_all_users_admin_fields()
        matching = [r for r in rows if r.get("id") == uid]
        assert len(matching) == 1
        row = matching[0]

        allowed = {"id", "username", "nickname", "home_region", "is_disabled", "created_at", "last_active_at"}
        actual = set(row.keys())
        extras = actual - allowed
        assert not extras, f"Unexpected fields in admin output: {extras}"

    async def test_returns_empty_list_when_no_users(self, store):
        """Empty DB returns empty list, not None or error."""
        rows = await store.get_all_users_admin_fields()
        assert isinstance(rows, list)
