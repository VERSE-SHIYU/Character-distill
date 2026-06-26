"""Cross-border DM forwarding: shared function + background resync loop.

All HMAC-signed peer forwarding goes through forward_dm_to_peer() so the
payload-construction and signing logic has a single source of truth.
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


async def _cross_border_resync_loop() -> None:
    """Periodically retry unsynced cross-border DMs (cross_border_synced=0).

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
        try:
            msgs = await storage.get_unsynced_cross_border_messages(limit=100)
        except Exception as exc:
            print(f"[cross_border_resync] Query failed: {exc}")
            continue

        for msg in msgs:
            ok = await forward_dm_to_peer(msg, storage)
            if ok:
                try:
                    await storage.mark_message_synced(msg["id"])
                except Exception as exc:
                    print(
                        f"[cross_border_resync] Mark synced failed for {msg['id']}: {exc}"
                    )
