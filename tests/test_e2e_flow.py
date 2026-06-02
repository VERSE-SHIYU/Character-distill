"""End-to-end flow test: full user lifecycle on PostgreSQL.

Simulates the real application flow:
  register → upload text → distill (save card) → chat → history → delete → cleanup

Run:
  STORAGE_BACKEND=postgres DATABASE_URL=postgresql://... python -m pytest tests/test_e2e_flow.py -v
"""

from __future__ import annotations

import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytestmark = [
    pytest.mark.skipif(
        os.getenv("STORAGE_BACKEND", "sqlite").strip().lower() != "postgres",
        reason="requires STORAGE_BACKEND=postgres",
    ),
    pytest.mark.asyncio,
]


@pytest.fixture
async def store():
    from storage.postgres_store import PostgresStore

    dsn = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/charsim_test")
    st = PostgresStore(dsn)
    await st._ensure_initialized()
    # Clean all tables
    async with await st._connect() as conn:
        for t in ["messages", "sessions", "cards", "texts", "users",
                   "group_messages", "group_sessions", "direct_messages", "user_follows"]:
            await conn.execute(f"DELETE FROM {t}")
    yield st
    await st.close()


class TestFullUserFlow:
    """Complete user lifecycle end-to-end on PostgreSQL."""

    async def test_01_register_and_login(self, store):
        """Register user, then fetch by username and by ID."""
        uid = f"usr_{uuid.uuid4().hex}"
        username = f"alice_{uuid.uuid4().hex[:8]}"

        created = await store.create_user(uid, username, "bcrypt_hash_here")
        assert created["id"] == uid
        assert created["username"] == username

        # Fetch by username (login)
        by_name = await store.get_user_by_username(username)
        assert by_name is not None
        assert by_name["id"] == uid

        # Fetch by ID
        by_id = await store.get_user_by_id(uid)
        assert by_id is not None
        assert by_id["username"] == username

    async def test_02_upload_text(self, store):
        """Upload a text and verify storage."""
        uid = f"usr_{uuid.uuid4().hex}"
        await store.create_user(uid, f"author_{uuid.uuid4().hex[:8]}", "hash")

        text_id = f"txt_{uuid.uuid4().hex}"
        content = "This is a test novel content. " * 100

        saved = await store.save_text(
            text_id, "my_novel.txt", content,
            title="My E2E Novel", description="End-to-end test",
            text_type="story", user_id=uid,
        )
        assert saved["id"] == text_id
        assert saved["filename"] == "my_novel.txt"
        assert saved["title"] == "My E2E Novel"

        # Read back
        got = await store.get_text(text_id)
        assert got is not None
        assert got["content"] == content

        # List
        all_texts = await store.list_texts(user_id=uid)
        assert any(t["id"] == text_id for t in all_texts)

    async def test_03_distill_and_create_cards(self, store):
        """Save character cards (distillation output) and verify."""
        uid = f"usr_{uuid.uuid4().hex}"
        await store.create_user(uid, f"distiller_{uuid.uuid4().hex[:8]}", "hash")

        text_id = f"txt_{uuid.uuid4().hex}"
        await store.save_text(text_id, "novel.txt", "Content", user_id=uid)

        char_data = [
            ("Alice", {"name": "Alice", "age": 25, "traits": ["curious", "brave"]}),
            ("Bob", {"name": "Bob", "age": 30, "traits": ["wise", "kind"]}),
        ]
        card_ids = []
        for name, data in char_data:
            cid = f"card_{uuid.uuid4().hex}"
            await store.save_card(cid, text_id, name, json.dumps(data, ensure_ascii=False), user_id=uid)
            card_ids.append(cid)

        # List cards
        cards = await store.list_cards(text_id)
        assert len(cards) == 2

        # Get individual card
        card = await store.get_card(card_ids[0])
        assert card is not None
        assert card["name"] == "Alice"

        # Update card
        updated = json.dumps({"name": "Alice", "age": 26}, ensure_ascii=False)
        result = await store.update_card(card_ids[0], json.loads(updated))
        assert result is not None

    async def test_04_chat_session_and_messages(self, store):
        """Create chat session, send messages, read history."""
        uid = f"usr_{uuid.uuid4().hex}"
        await store.create_user(uid, f"chatter_{uuid.uuid4().hex[:8]}", "hash")

        text_id = f"txt_{uuid.uuid4().hex}"
        await store.save_text(text_id, "src.txt", "Src", user_id=uid)

        card_id = f"card_{uuid.uuid4().hex}"
        await store.save_card(card_id, text_id, "Bot",
                               json.dumps({"name": "Bot"}), user_id=uid)

        # Create session
        session_id = f"ses_{uuid.uuid4().hex}"
        session = await store.save_session(session_id, card_id, "", "", user_id=uid)
        assert session["id"] == session_id

        # Verify session
        got = await store.get_session(session_id)
        assert got is not None
        assert got["card_id"] == card_id

        # Send messages
        conversation = [
            ("user", "Hi!"),
            ("assistant", "Hello!"),
            ("user", "How are you?"),
            ("assistant", "I'm fine, thanks!"),
        ]
        msg_ids = []
        for role, content in conversation:
            msg = await store.save_message(session_id, role, content, "")
            assert "id" in msg
            msg_ids.append(msg["id"])

        # Verify sequential
        assert all(msg_ids[i] < msg_ids[i + 1] for i in range(len(msg_ids) - 1))

        # Read history
        history = await store.get_messages(session_id)
        assert len(history) == len(conversation)
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "Hi!"

    async def test_05_delete_and_restore_session(self, store):
        """Delete messages, soft-delete session, restore, purge."""
        uid = f"usr_{uuid.uuid4().hex}"
        await store.create_user(uid, f"cleaner_{uuid.uuid4().hex[:8]}", "hash")

        text_id = f"txt_{uuid.uuid4().hex}"
        card_id = f"card_{uuid.uuid4().hex}"
        session_id = f"ses_{uuid.uuid4().hex}"

        await store.save_text(text_id, "src.txt", "Src", user_id=uid)
        await store.save_card(card_id, text_id, "Char",
                               json.dumps({"name": "Char"}), user_id=uid)
        await store.save_session(session_id, card_id, "", "", user_id=uid)

        # Messages
        m1 = await store.save_message(session_id, "user", "keep", "")
        m2 = await store.save_message(session_id, "user", "delete", "")
        m3 = await store.save_message(session_id, "user", "keep2", "")

        # Delete messages after m2
        await store.delete_messages_after(session_id, m2["id"])  # deletes m2 AND m3
        remaining = await store.get_messages(session_id)
        assert len(remaining) == 1  # only m1 left
        assert remaining[0]["content"] == "keep"

        # Soft-delete session
        assert await store.delete_session(session_id) is True
        trash = await store.list_trash_sessions()
        assert any(s["id"] == session_id for s in trash)

        # Restore
        assert await store.restore_session(session_id) is True
        restored = await store.get_session(session_id)
        assert restored is not None

        # Purge
        await store.delete_session(session_id)
        purged = await store.purge_trash()
        assert purged >= 1
        trash2 = await store.list_trash_sessions()
        assert not any(s["id"] == session_id for s in trash2)

    async def test_06_cleanup_empty_cards(self, store):
        """Cleanup empty cards (distillation failure path)."""
        uid = f"usr_{uuid.uuid4().hex}"
        await store.create_user(uid, f"cleanup_{uuid.uuid4().hex[:8]}", "hash")

        text_id = f"txt_{uuid.uuid4().hex}"
        await store.save_text(text_id, "src.txt", "Src", user_id=uid)

        # Valid card
        await store.save_card(f"card_{uuid.uuid4().hex}", text_id, "Valid",
                               json.dumps({"name": "Valid"}), user_id=uid)
        # Empty card (distillation failure)
        empty_id = f"card_{uuid.uuid4().hex}"
        await store.save_card(empty_id, text_id, "Empty", json.dumps({}), user_id=uid)

        cleaned = await store.cleanup_empty_cards(text_id, uid)
        assert cleaned >= 1

        cards = await store.list_cards(text_id)
        assert all(c["name"] != "Empty" for c in cards)

    async def test_07_full_integration_story(self, store):
        """Complete end-to-end story: one continuous flow."""
        uid = f"usr_{uuid.uuid4().hex}"
        username = f"hero_{uuid.uuid4().hex[:8]}"

        # ── Register ──
        await store.create_user(uid, username, "hash")
        user = await store.get_user_by_username(username)
        assert user is not None

        # ── Upload text ──
        text_id = f"txt_{uuid.uuid4().hex}"
        story = "In a galaxy far away... " * 50
        await store.save_text(text_id, "space_opera.txt", story,
                               title="Space Opera", text_type="story", user_id=uid)
        text = await store.get_text(text_id)
        assert text["content"] == story

        # ── Distill → cards ──
        chars = [
            ("Luke", json.dumps({"name": "Luke", "role": "jedi"})),
            ("Vader", json.dumps({"name": "Vader", "role": "sith"})),
        ]
        card_ids = []
        for name, data in chars:
            cid = f"card_{uuid.uuid4().hex}"
            await store.save_card(cid, text_id, name, data, user_id=uid)
            card_ids.append(cid)
        assert len(await store.list_cards(text_id)) == 2

        # ── Chat ──
        session_id = f"ses_{uuid.uuid4().hex}"
        await store.save_session(session_id, card_ids[0], "", "", user_id=uid)

        script = [
            ("user", "Hello there!"),
            ("assistant", "General Kenobi!"),
            ("user", "Tell me about the Force"),
            ("assistant", "It surrounds us and binds us."),
        ]
        msg_ids = []
        for role, content in script:
            msg = await store.save_message(session_id, role, content, "")
            msg_ids.append(msg["id"])

        history = await store.get_messages(session_id)
        assert len(history) == 4
        assert history[-1]["content"] == "It surrounds us and binds us."

        # ── Delete last message ──
        await store.delete_messages_after(session_id, msg_ids[-1])
        assert len(await store.get_messages(session_id)) == 3

        # ── Delete session ──
        await store.delete_session(session_id)
        assert any(s["id"] == session_id for s in await store.list_trash_sessions())

        # ── Restore ──
        await store.restore_session(session_id)
        assert (await store.get_session(session_id)) is not None

        # ── Final cleanup ──
        await store.delete_session(session_id)
        purged = await store.purge_trash()
        assert purged >= 1
        assert not any(s["id"] == session_id for s in await store.list_trash_sessions())
