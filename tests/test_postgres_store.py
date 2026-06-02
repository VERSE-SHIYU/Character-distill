"""Tests for PostgresStore CRUD using a real PostgreSQL database.

These tests require a running PostgreSQL instance.  Set these env vars:

  STORAGE_BACKEND=postgres
  DATABASE_URL=postgresql://postgres:postgres@localhost:5432/charsim_test

Skip: export SKIP_PG_TESTS=1 to skip all PostgresStore tests.
"""

from __future__ import annotations

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
            "group_messages", "group_sessions",
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
            visibility="public",
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
        texts = await store.list_texts(limit=10)
        assert len(texts) >= 3

    async def test_list_texts_pagination(self, store):
        ids = []
        for i in range(5):
            tid = f"txt_{uuid.uuid4().hex}"
            await store.save_text(tid, f"f{i}.txt", f"c{i}")
            ids.append(tid)
        page1 = await store.list_texts(limit=2)
        assert len(page1) == 2

    async def test_get_text_by_filename(self, store, text_id):
        await store.save_text(text_id, "unique_name.txt", "content")
        results = await store.get_text_by_filename("unique_name.txt")
        assert len(results) >= 1
        assert any(r["id"] == text_id for r in results)


# ── Card CRUD ────────────────────────────────────────────────────────────────

class TestCardCrud:
    async def test_save_and_get_card(self, store, text_id, card_id):
        await store.save_text(text_id, "src.txt", "source")
        card = {"name": "Alice", "description": "A character"}
        result = await store.save_card(card_id, text_id, "Alice", card, user_id="usr1")
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
            await store.save_card(cid, text_id, f"Char{i}", {"name": f"Char{i}"})
        cards = await store.get_cards_by_text(text_id)
        assert len(cards) >= 3


# ── Session CRUD ─────────────────────────────────────────────────────────────

class TestSessionCrud:
    async def test_save_and_get_session(self, store, text_id, card_id, session_id):
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, "Bob", {"name": "Bob"})
        result = await store.save_session(session_id, card_id)
        assert result.get("id") == session_id

        got = await store.get_session(session_id)
        assert got is not None
        assert got["card_id"] == card_id

    async def test_list_sessions_by_card(self, store, text_id, card_id):
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, "Bob", {"name": "Bob"})
        sids = []
        for i in range(3):
            sid = f"ses_{uuid.uuid4().hex}"
            await store.save_session(sid, card_id)
            sids.append(sid)
        sessions = await store.list_sessions({"card_id": card_id})
        assert len(sessions) >= 3


# ── Message CRUD ─────────────────────────────────────────────────────────────

class TestMessageCrud:
    async def test_save_and_get_messages(self, store, text_id, card_id, session_id):
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, "Char", {"name": "Char"})
        await store.save_session(session_id, card_id)

        msg_id = await store.save_message(session_id, "user", "Hello")
        assert msg_id is not None
        assert isinstance(msg_id, int)

        msg_id2 = await store.save_message(session_id, "assistant", "Hi there")
        assert msg_id2 > msg_id

        msgs = await store.get_session_messages(session_id)
        assert len(msgs) == 2
        assert msgs[0]["content"] == "Hello"
        assert msgs[1]["content"] == "Hi there"

    async def test_delete_messages(self, store, text_id, card_id, session_id):
        await store.save_text(text_id, "src.txt", "source")
        await store.save_card(card_id, text_id, "Char", {"name": "Char"})
        await store.save_session(session_id, card_id)
        await store.save_message(session_id, "user", "msg1")
        await store.save_message(session_id, "user", "msg2")

        await store.delete_session_messages(session_id)
        msgs = await store.get_session_messages(session_id)
        assert len(msgs) == 0


# ── User CRUD ────────────────────────────────────────────────────────────────

class TestUserCrud:
    async def test_save_and_get_user(self, store, user_id):
        user = {
            "id": user_id,
            "username": f"testuser_{uuid.uuid4().hex[:8]}",
            "password_hash": "hash",
        }
        await store.save_user(user)
        got = await store.get_user(user_id)
        assert got is not None
        assert got["id"] == user_id

    async def test_get_user_by_username(self, store, user_id):
        username = f"unique_{uuid.uuid4().hex[:8]}"
        await store.save_user({"id": user_id, "username": username, "password_hash": "hash"})
        got = await store.get_user_by_username(username)
        assert got is not None
        assert got["id"] == user_id

    async def test_get_user_not_found(self, store):
        got = await store.get_user("nonexistent")
        assert got is None

    async def test_list_users(self, store, user_id):
        await store.save_user({"id": user_id, "username": "listuser", "password_hash": "hash"})
        users = await store.list_users(limit=10)
        assert len(users) >= 1


# ── Group Session CRUD ───────────────────────────────────────────────────────

class TestGroupSessionCrud:
    async def test_save_and_get_group(self, store):
        gid = f"grp_{uuid.uuid4().hex}"
        result = await store.save_group_session(gid, "Test Group", ["card1", "card2"])
        assert result.get("id") == gid

        got = await store.get_group_session(gid)
        assert got is not None
        assert got["name"] == "Test Group"

    async def test_list_group_sessions(self, store):
        gid = f"grp_{uuid.uuid4().hex}"
        await store.save_group_session(gid, "Group A", [])
        sessions = await store.list_group_sessions()
        assert any(s["id"] == gid for s in sessions)


# ── Follow / DM ──────────────────────────────────────────────────────────────

class TestFollowAndDM:
    async def test_follow_user(self, store, user_id):
        other = f"usr_{uuid.uuid4().hex}"
        await store.save_user({"id": user_id, "username": "f1", "password_hash": "h"})
        await store.save_user({"id": other, "username": "f2", "password_hash": "h"})
        await store.follow_user(user_id, other)
        followers = await store.get_followers(other)
        assert any(f["follower_id"] == user_id for f in followers)

    async def test_unfollow_user(self, store, user_id):
        other = f"usr_{uuid.uuid4().hex}"
        await store.save_user({"id": user_id, "username": "u1", "password_hash": "h"})
        await store.save_user({"id": other, "username": "u2", "password_hash": "h"})
        await store.follow_user(user_id, other)
        await store.unfollow_user(user_id, other)
        following = await store.get_following(user_id)
        assert not any(f["following_id"] == other for f in following)


class TestDMCrud:
    async def test_send_and_get_dm(self, store, user_id):
        other = f"usr_{uuid.uuid4().hex}"
        await store.save_user({"id": user_id, "username": "d1", "password_hash": "h"})
        await store.save_user({"id": other, "username": "d2", "password_hash": "h"})

        mid = f"dm_{uuid.uuid4().hex}"
        await store.send_dm(mid, user_id, other, "Hello DM")
        msgs = await store.get_conversation(user_id, other)
        assert any(m["content"] == "Hello DM" for m in msgs)
