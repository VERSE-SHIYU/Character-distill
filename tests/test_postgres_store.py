"""Tests for PostgresStore CRUD using a real PostgreSQL database.

These tests require a running PostgreSQL instance.  Set these env vars:

  STORAGE_BACKEND=postgres
  DATABASE_URL=postgresql://postgres:postgres@localhost:5432/charsim_test

Skip: export SKIP_PG_TESTS=1 to skip all PostgresStore tests.
"""

from __future__ import annotations

import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from storage.postgres_store import PostgresStore


pytestmark = pytest.mark.skipif(
    os.getenv("SKIP_PG_TESTS") == "1",
    reason="SKIP_PG_TESTS=1 set — skipping PostgresStore tests",
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _dsn() -> str:
    return os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/charsim_test")


async def _clean_tables(store: PostgresStore) -> None:
    """Truncate all tables for a clean test state."""
    async with await store._connect() as conn:
        tables = [
            "messages", "sessions", "cards", "texts", "users",
            "group_messages", "group_sessions", "direct_messages",
            "user_follows",
        ]
        for t in tables:
            await conn.execute(f"DELETE FROM {t}")


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
async def store():
    st = PostgresStore(_dsn())
    await st._ensure_initialized()
    await _clean_tables(st)
    yield st
    await st.close()


@pytest.fixture
def text_id():
    return f"txt_{uuid.uuid4().hex}"


@pytest.fixture
def card_id():
    return f"card_{uuid.uuid4().hex}"


@pytest.fixture
def session_id():
    return f"ses_{uuid.uuid4().hex}"


@pytest.fixture
def user_id():
    return f"usr_{uuid.uuid4().hex}"


# ── Text CRUD ────────────────────────────────────────────────────────────────

class TestTextCrud:
    async def test_save_and_get(self, store, text_id):
        result = await store.save_text(text_id, "test.txt", "Hello world")
        assert result.get("id") == text_id
        assert result.get("filename") == "test.txt"
        assert result.get("content") == "Hello world"

        got = await store.get_text(text_id)
        assert got is not None
        assert got["id"] == text_id
        assert got["content"] == "Hello world"

    async def test_save_with_metadata(self, store, text_id):
        result = await store.save_text(
            text_id, "novel.txt", "Long content",
            title="My Novel", description="A test",
            text_type="story", user_id="usr1",
        )
        assert result["title"] == "My Novel"
        assert result["text_type"] == "story"
        assert result["user_id"] == "usr1"

    async def test_delete_text(self, store, text_id):
        await store.save_text(text_id, "del.txt", "To be deleted")
        await store.delete_text(text_id)
        got = await store.get_text(text_id)
        assert got is None or got.get("deleted_at", "")

    async def test_get_text_not_found(self, store):
        got = await store.get_text("nonexistent")
        assert got is None

    async def test_list_texts(self, store):
        for i in range(3):
            tid = f"txt_{uuid.uuid4().hex}"
            await store.save_text(tid, f"file{i}.txt", f"content{i}")
        texts = await store.list_texts()
        assert len(texts) >= 3


# ── Card CRUD ────────────────────────────────────────────────────────────────

class TestCardCrud:
    async def test_save_and_get_card(self, store, text_id, card_id):
        await store.save_text(text_id, "src.txt", "source")
        card_json = json.dumps({"name": "Alice", "description": "A character"}, ensure_ascii=False)
        result = await store.save_card(card_id, text_id, "Alice", card_json, user_id="usr1")
        assert result.get("id") == card_id

        got = await store.get_card(card_id)
        assert got is not None
        assert got["name"] == "Alice"

    async def test_get_card_not_found(self, store):
        got = await store.get_card("nonexistent")
        assert got is None

    async def test_get_cards_by_text(self, store, text_id):
        await store.save_text(text_id, "src.txt", "source")
        for i in range(3):
            cid = f"card_{uuid.uuid4().hex}"
            await store.save_card(cid, text_id, f"Char{i}", json.dumps({"name": f"Char{i}"}))
        cards = await store.list_cards(text_id)
        assert len(cards) >= 3

    async def test_get_card_detail(self, store, text_id, card_id, user_id):
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, "Alice", json.dumps({"name": "Alice"}), user_id=user_id)
        detail = await store.get_card_detail(card_id, user_id)
        assert detail is not None


# ── Session CRUD ─────────────────────────────────────────────────────────────

class TestSessionCrud:
    async def test_save_and_get_session(self, store, text_id, card_id, session_id):
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, "Bob", json.dumps({"name": "Bob"}))
        result = await store.save_session(session_id, card_id, "", "")
        assert result.get("id") == session_id

        got = await store.get_session(session_id)
        assert got is not None
        assert got["card_id"] == card_id

    async def test_list_sessions_by_card(self, store, text_id, card_id):
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, "Bob", json.dumps({"name": "Bob"}))
        for i in range(3):
            sid = f"ses_{uuid.uuid4().hex}"
            await store.save_session(sid, card_id, "", "")
        # list_sessions filters by keyword, character, text_id, paginated
        result = await store.list_sessions("", "", text_id, 1, 10)
        assert isinstance(result, dict)
        assert len(result["items"]) >= 3


# ── Message CRUD ─────────────────────────────────────────────────────────────

class TestMessageCrud:
    async def test_save_and_get_messages(self, store, text_id, card_id, session_id):
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, "Char", json.dumps({"name": "Char"}))
        await store.save_session(session_id, card_id, "", "")

        msg1 = await store.save_message(session_id, "user", "Hello", "")
        assert msg1.get("id") is not None

        msg2 = await store.save_message(session_id, "assistant", "Hi there", "")
        assert msg2["id"] > msg1["id"]

        msgs = await store.get_messages(session_id)
        assert len(msgs) == 2
        assert msgs[0]["content"] == "Hello"
        assert msgs[1]["content"] == "Hi there"

    async def test_delete_messages_after(self, store, text_id, card_id, session_id):
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, "Char", json.dumps({"name": "Char"}))
        await store.save_session(session_id, card_id, "", "")
        m1 = await store.save_message(session_id, "user", "keep", "")
        m2 = await store.save_message(session_id, "user", "delete_this", "")

        # delete_messages_after includes the given message_id
        await store.delete_messages_after(session_id, m2["id"])
        msgs = await store.get_messages(session_id)
        assert len(msgs) == 1
        assert msgs[0]["content"] == "keep"


# ── User CRUD ────────────────────────────────────────────────────────────────

class TestUserCrud:
    async def test_create_and_get_user(self, store, user_id):
        username = f"testuser_{uuid.uuid4().hex[:8]}"
        result = await store.create_user(user_id, username, "hash")
        assert result is not None
        assert result.get("id") == user_id

        got = await store.get_user_by_id(user_id)
        assert got is not None
        assert got["id"] == user_id

    async def test_get_user_by_username(self, store, user_id):
        username = f"unique_{uuid.uuid4().hex[:8]}"
        await store.create_user(user_id, username, "hash")
        got = await store.get_user_by_username(username)
        assert got is not None
        assert got["id"] == user_id

    async def test_get_user_not_found(self, store):
        got = await store.get_user_by_id("nonexistent")
        assert got is None

    async def test_list_users(self, store, user_id):
        await store.create_user(user_id, f"listuser_{uuid.uuid4().hex[:8]}", "hash")
        users = await store.get_all_users()
        assert len(users) >= 1


# ── Group Session CRUD ───────────────────────────────────────────────────────

class TestGroupSessionCrud:
    async def test_create_and_get_group(self, store):
        gid = f"grp_{uuid.uuid4().hex}"
        await store.create_group_session(gid, "Test Group", ["card1", "card2"], user_id="")

        got = await store.get_group_session(gid)
        assert got is not None
        assert got["name"] == "Test Group"

    async def test_list_group_sessions(self, store):
        gid = f"grp_{uuid.uuid4().hex}"
        uid = f"usr_{uuid.uuid4().hex}"
        await store.create_group_session(gid, "Group A", [], user_id=uid)
        sessions = await store.list_group_sessions(uid)
        assert any(s["id"] == gid for s in sessions)


# ── Follow / DM ──────────────────────────────────────────────────────────────

class TestFollowAndDM:
    async def test_follow_user(self, store, user_id):
        other = f"usr_{uuid.uuid4().hex}"
        await store.create_user(user_id, f"f1_{uuid.uuid4().hex[:8]}", "h")
        await store.create_user(other, f"f2_{uuid.uuid4().hex[:8]}", "h")
        await store.toggle_follow(user_id, other)
        followers = await store.get_followers(other)
        assert user_id in followers

    async def test_unfollow_user(self, store, user_id):
        other = f"usr_{uuid.uuid4().hex}"
        await store.create_user(user_id, f"u1_{uuid.uuid4().hex[:8]}", "h")
        await store.create_user(other, f"u2_{uuid.uuid4().hex[:8]}", "h")
        await store.toggle_follow(user_id, other)
        following = await store.get_following(user_id)
        assert other in following

        await store.toggle_follow(user_id, other)
        following = await store.get_following(user_id)
        assert other not in following


class TestDMCrud:
    async def test_send_and_get_dm(self, store, user_id):
        other = f"usr_{uuid.uuid4().hex}"
        await store.create_user(user_id, f"d1_{uuid.uuid4().hex[:8]}", "h")
        await store.create_user(other, f"d2_{uuid.uuid4().hex[:8]}", "h")

        result = await store.send_message(user_id, other, "Hello DM")
        assert "Hello DM" in str(result.get("content", ""))

        msgs = await store.get_conversation_messages(user_id, other)
        assert any(m["content"] == "Hello DM" for m in msgs)
