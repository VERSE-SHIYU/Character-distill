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

        got = await store.get_text(text_id)
        assert got is None

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
