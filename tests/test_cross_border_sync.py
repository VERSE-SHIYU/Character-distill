"""Tests for cross_border_sync forwarding functions.

Tests cover both forward_dm_to_peer and forward_card_to_peer.
Uses monkeypatch for env vars and unittest.mock for httpx so no real
network calls are made.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _ensure_no_env_leak(monkeypatch):
    """Isolate env vars per test."""
    monkeypatch.delenv("PEER_NODE_URL", raising=False)
    monkeypatch.setenv("INTER_NODE_SECRET", "test-inter-node-secret")


class MockResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


@pytest.mark.parametrize("status_code,expected_marked", [
    (200, True),
    (401, False),
    (500, False),
])
async def test_forward_dm_to_peer(monkeypatch, status_code, expected_marked):
    """forward_dm_to_peer returns True only on 200, and caller marks synced."""
    from cross_border_sync import forward_dm_to_peer

    monkeypatch.setenv("PEER_NODE_URL", "http://sg-node:7860")

    msg = {
        "id": "test123",
        "sender_id": "user_a",
        "receiver_id": "user_b",
        "content": "hello",
        "created_at": "2026-06-26 08:00:00",
    }

    storage = MagicMock()
    storage.mark_message_synced = AsyncMock()

    mock_post = AsyncMock(return_value=MockResponse(status_code))
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = mock_post

    with patch("httpx.AsyncClient", return_value=mock_client):
        ok = await forward_dm_to_peer(msg, storage)

    assert ok is expected_marked

    # Verify POST was called with correct URL and JSON
    mock_post.assert_awaited_once()
    call_kwargs = mock_post.call_args[1]
    assert "api/inter-node/dm/receive" in str(mock_post.call_args[0][0])
    assert call_kwargs["json"] == msg
    assert "Authorization" in call_kwargs["headers"]
    assert call_kwargs["headers"]["Authorization"].startswith("HMAC-SHA256")


async def test_forward_dm_to_peer_no_peer_url():
    """No PEER_NODE_URL => no-op, returns False."""
    from cross_border_sync import forward_dm_to_peer

    storage = MagicMock()
    ok = await forward_dm_to_peer({"id": "x"}, storage)
    assert ok is False


async def test_forward_dm_to_peer_empty_peer_url(monkeypatch):
    """Empty PEER_NODE_URL => no-op, returns False."""
    from cross_border_sync import forward_dm_to_peer

    monkeypatch.setenv("PEER_NODE_URL", "")
    storage = MagicMock()
    ok = await forward_dm_to_peer({"id": "x"}, storage)
    assert ok is False


async def test_forward_dm_to_peer_connection_error(monkeypatch):
    """Connection error => returns False (message not lost)."""
    from cross_border_sync import forward_dm_to_peer

    monkeypatch.setenv("PEER_NODE_URL", "http://unreachable.invalid:7860")
    msg = {
        "id": "err123",
        "sender_id": "user_a",
        "receiver_id": "user_b",
        "content": "hello",
        "created_at": "2026-06-26 08:00:00",
    }
    storage = MagicMock()
    ok = await forward_dm_to_peer(msg, storage)
    assert ok is False


# ── forward_card_to_peer tests ──────────────────────────────────────────

CARD_FIXTURE = {
    "id": "card001",
    "user_id": "author_x",
    "text_id": "text_99",
    "name": "Test Card",
    "card_json": '{"title":"hello"}',
    "avatar_data": "data:image/png;base64,abc",
    "visibility": "public",
    "market_description": "A test card",
    "market_tags": "fantasy",
    "created_at": "2026-06-25 12:00:00",
}


@pytest.mark.parametrize("status_code,expected_marked", [
    (200, True),
    (401, False),
    (500, False),
])
async def test_forward_card_to_peer(monkeypatch, status_code, expected_marked):
    """forward_card_to_peer returns True only on 200."""
    from cross_border_sync import forward_card_to_peer

    monkeypatch.setenv("PEER_NODE_URL", "http://sg-node:7860")

    storage = MagicMock()
    mock_post = AsyncMock(return_value=MockResponse(status_code))
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = mock_post

    with patch("httpx.AsyncClient", return_value=mock_client):
        ok = await forward_card_to_peer(CARD_FIXTURE, storage)

    assert ok is expected_marked

    mock_post.assert_awaited_once()
    call_kwargs = mock_post.call_args[1]
    assert "api/inter-node/card/receive" in str(mock_post.call_args[0][0])
    # All fields from CARD_FIXTURE must be in the POST JSON
    for key in ("id", "user_id", "text_id", "name", "card_json",
                "avatar_data", "visibility", "market_description",
                "market_tags"):
        assert call_kwargs["json"][key] == CARD_FIXTURE[key]
    assert "Authorization" in call_kwargs["headers"]
    assert call_kwargs["headers"]["Authorization"].startswith("HMAC-SHA256")


async def test_forward_card_to_peer_no_peer_url():
    """No PEER_NODE_URL => no-op, returns False."""
    from cross_border_sync import forward_card_to_peer

    storage = MagicMock()
    ok = await forward_card_to_peer(CARD_FIXTURE, storage)
    assert ok is False


async def test_forward_card_to_peer_empty_peer_url(monkeypatch):
    """Empty PEER_NODE_URL => no-op, returns False."""
    from cross_border_sync import forward_card_to_peer

    monkeypatch.setenv("PEER_NODE_URL", "")
    storage = MagicMock()
    ok = await forward_card_to_peer(CARD_FIXTURE, storage)
    assert ok is False


async def test_forward_card_to_peer_connection_error(monkeypatch):
    """Connection error => returns False (card not lost)."""
    from cross_border_sync import forward_card_to_peer

    monkeypatch.setenv("PEER_NODE_URL", "http://unreachable.invalid:7860")
    storage = MagicMock()
    ok = await forward_card_to_peer(CARD_FIXTURE, storage)
    assert ok is False
