"""Cross-border DM + card forwarding: shared functions + background resync loop.

All HMAC-signed peer forwarding goes through forward_dm_to_peer() or
forward_card_to_peer() so the payload-construction and signing logic has a
single source of truth per domain.
"""

from __future__ import annotations

import asyncio
import os

from storage.base import StorageBase


async def forward_dm_to_peer(msg: dict, storage: StorageBase) -> bool:
    """Forward one DM to the peer node via HMAC-signed HTTP POST.

    Builds an explicit string-typed payload (no datetime/dict surprises),
    signs it with inter-node HMAC, POSTs to the peer's receive endpoint.

    Returns True if the peer acknowledged (HTTP 200), False otherwise.
    The caller is responsible for updating cross_border_synced on success.
    """
    peer_url = os.getenv("PEER_NODE_URL", "").rstrip("/")
    if not peer_url:
        return False

    from inter_node_auth import create_auth_header

    payload = {
        "id": msg["id"],
        "sender_id": msg["sender_id"],
        "receiver_id": msg["receiver_id"],
        "content": msg["content"],
        "created_at": str(msg["created_at"]),
    }
    headers = create_auth_header(payload)

    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{peer_url}/api/inter-node/dm/receive",
                json=payload,
                headers=headers,
            )
        return resp.status_code == 200
    except Exception:
        return False


async def forward_card_to_peer(card: dict, storage: StorageBase) -> bool:
    """Forward one public card to the peer node via HMAC-signed HTTP POST.

    Builds an explicit string-typed payload and POSTs to the peer's
    card receive endpoint.  Separate from forward_dm_to_peer because
    the payload fields and endpoint path are different.
    """
    peer_url = os.getenv("PEER_NODE_URL", "").rstrip("/")
    if not peer_url:
        return False

    from inter_node_auth import create_auth_header

    payload = {
        "id": card["id"],
        "user_id": card.get("user_id", ""),
        "text_id": card.get("text_id", ""),
        "name": card.get("name", ""),
        "card_json": card.get("card_json", "{}"),
        "avatar_data": card.get("avatar_data", ""),
        "visibility": card.get("visibility", "public"),
        "market_description": card.get("market_description", ""),
        "market_tags": card.get("market_tags", ""),
        "created_at": str(card.get("created_at", "")),
    }
    headers = create_auth_header(payload)

    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{peer_url}/api/inter-node/card/receive",
                json=payload,
                headers=headers,
            )
        return resp.status_code == 200
    except Exception:
        return False


async def _cross_border_resync_loop() -> None:
    """Periodically retry unsynced cross-border DMs and public cards.

    Runs every 60 seconds.  Silently no-ops when PEER_NODE_URL is unset
    (single-node deployment).
    """
    while True:
        await asyncio.sleep(60)

        peer_url = os.getenv("PEER_NODE_URL", "").rstrip("/")
        if not peer_url:
            continue

        from deps import get_storage

        storage = get_storage()

        # ── DM resync ──
        try:
            msgs = await storage.get_unsynced_cross_border_messages(limit=100)
        except Exception as exc:
            print(f"[cross_border_resync] DM query failed: {exc}")
        else:
            for msg in msgs:
                ok = await forward_dm_to_peer(msg, storage)
                if ok:
                    try:
                        await storage.mark_message_synced(msg["id"])
                    except Exception as exc:
                        print(
                            f"[cross_border_resync] Mark DM synced failed for {msg['id']}: {exc}"
                        )

        # ── Card resync ──
        try:
            cards = await storage.get_unsynced_cross_border_cards(limit=100)
        except Exception as exc:
            print(f"[cross_border_resync] Card query failed: {exc}")
        else:
            for card in cards:
                ok = await forward_card_to_peer(card, storage)
                if ok:
                    try:
                        await storage.mark_card_synced(card["id"])
                    except Exception as exc:
                        print(
                            f"[cross_border_resync] Mark card synced failed for {card['id']}: {exc}"
                        )
