"""Test DM reaction storage methods (SQLite)."""
from __future__ import annotations

import uuid

import pytest

from storage.sqlite_store import SQLiteStore


@pytest.fixture
def store(tmp_path):
    """Fresh SQLiteStore backed by a temporary file."""
    db_path = str(tmp_path / f"test_{uuid.uuid4().hex}.db")
    return SQLiteStore(db_path)


@pytest.fixture
def user_a():
    return "test-dm-user-a"


@pytest.fixture
def user_b():
    return "test-dm-user-b"


def uniq():
    return uuid.uuid4().hex[:12]


async def test_toggle_add_and_remove(store, user_a):
    """Toggle should add then remove."""
    mid = uniq()
    added = await store.toggle_dm_reaction(mid, user_a, "\U0001f44d")
    assert added is True
    added = await store.toggle_dm_reaction(mid, user_a, "\U0001f44d")
    assert added is False


async def test_get_dm_reactions_empty(store, user_a, user_b):
    """get_dm_reactions returns {} for conversation with no reactions."""
    reactions = await store.get_dm_reactions(user_a, user_b)
    assert reactions == {}


async def test_get_dm_reactions_aggregation(store, user_a, user_b):
    """get_dm_reactions returns correctly aggregated reactions."""
    msg = await store.send_message(user_a, user_b, "hello")
    mid = msg["id"]

    await store.toggle_dm_reaction(mid, user_a, "\U0001f44d")
    await store.toggle_dm_reaction(mid, user_b, "\U0001f44d")

    reactions = await store.get_dm_reactions(user_a, user_b)
    assert mid in reactions
    thumbs = [r for r in reactions[mid] if r["emoji"] == "\U0001f44d"]
    assert len(thumbs) == 1
    assert thumbs[0]["count"] == 2
    assert set(thumbs[0]["users"]) == {user_a, user_b}

    # Remove user_a's reaction, count drops to 1
    await store.toggle_dm_reaction(mid, user_a, "\U0001f44d")
    reactions = await store.get_dm_reactions(user_a, user_b)
    thumbs = [r for r in reactions.get(mid, []) if r["emoji"] == "\U0001f44d"]
    assert len(thumbs) == 1
    assert thumbs[0]["count"] == 1

    # Clean up
    await store.toggle_dm_reaction(mid, user_b, "\U0001f44d")


async def test_get_dm_reactions_conversation_scoped(store, user_a, user_b):
    """get_dm_reactions only returns reactions for the given conversation."""
    msg = await store.send_message(user_a, user_b, "ab msg")
    await store.toggle_dm_reaction(msg["id"], user_a, "\U0001f44d")

    user_c = "test-dm-user-c"
    reactions_c = await store.get_dm_reactions(user_a, user_c)
    assert msg["id"] not in reactions_c

    # Clean up
    await store.toggle_dm_reaction(msg["id"], user_a, "\U0001f44d")


async def test_get_dm_message(store, user_a, user_b):
    """get_dm_message returns message by id."""
    msg = await store.send_message(user_a, user_b, "test")
    found = await store.get_dm_message(msg["id"])
    assert found is not None
    assert found["sender_id"] == user_a
    assert found["receiver_id"] == user_b

    not_found = await store.get_dm_message("nonexistent")
    assert not_found is None
