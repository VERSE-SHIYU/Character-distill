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
