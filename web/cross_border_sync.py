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

    # origin_region = sender's home_region
    origin_region = ""
    try:
        owner = await storage.get_user_by_id(card.get("user_id", ""))
        if owner:
            origin_region = owner.get("home_region", "")
    except Exception:
        pass

    payload = {
        "id": card["id"],
        "user_id": card.get("user_id", ""),
        "origin_region": origin_region,
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


_ENDPOINT_MAP: dict[str, str] = {
    "card_delete": "/api/inter-node/card/delete",
    "dm_retract": "/api/inter-node/dm/retract",
    "user_purge": "/api/inter-node/user/purge",
}


async def forward_delete_to_peer(op_type: str, target_id: str, payload: str, storage: StorageBase) -> bool:
    """Forward a delete/retract/purge intent to the peer node.

    The op_type determines the endpoint:
      card_delete → /api/inter-node/card/delete
      dm_retract  → /api/inter-node/dm/retract
      user_purge  → /api/inter-node/user/purge

    Returns True if the peer acknowledged (HTTP 200), False otherwise.
    The caller (resync loop) is responsible for marking the outbox row synced.
    """
    peer_url = os.getenv("PEER_NODE_URL", "").rstrip("/")
    if not peer_url:
        return False

    endpoint = _ENDPOINT_MAP.get(op_type)
    if not endpoint:
        print(f"[cross_border_sync] Unknown delete op_type: {op_type}")
        return False

    from inter_node_auth import create_auth_header

    body = {
        "op_type": op_type,
        "target_id": target_id,
        "payload": payload,
    }
    headers = create_auth_header(body)

    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{peer_url}{endpoint}",
                json=body,
                headers=headers,
            )
        return resp.status_code == 200
    except Exception:
        return False


async def forward_invite_code_to_peer(record: dict) -> bool:
    """Forward a newly created invite code to the peer node.

    Builds a string-typed payload, signs with HMAC, POSTs to the peer's
    invite-code receive endpoint.  Best-effort: returns False on failure,
    caller is not expected to retry.
    """
    peer_url = os.getenv("PEER_NODE_URL", "").rstrip("/")
    if not peer_url:
        return False

    from inter_node_auth import create_auth_header

    payload = {
        "code": str(record.get("code", "")),
        "created_by": str(record.get("created_by", "")),
    }
    headers = create_auth_header(payload)

    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{peer_url}/api/inter-node/invite-code/receive",
                json=payload,
                headers=headers,
            )
        return resp.status_code == 200
    except Exception:
        return False


async def forward_invite_code_delete_to_peer(code: str) -> bool:
    """Forward an invite-code delete to the peer node.

    Best-effort: returns False on failure, caller is not expected to retry.
    """
    peer_url = os.getenv("PEER_NODE_URL", "").rstrip("/")
    if not peer_url:
        return False

    from inter_node_auth import create_auth_header

    payload = {"code": code}
    headers = create_auth_header(payload)

    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{peer_url}/api/inter-node/invite-code/delete",
                json=payload,
                headers=headers,
            )
        return resp.status_code == 200
    except Exception:
        return False


async def forward_user_profile_to_peer(user_id: str, username: str, home_region: str, avatar_data: str = "") -> bool:
    """Forward a user profile to the peer node (lightweight stub sync).

    Best-effort: returns False on failure.  Caller is not expected to retry;
    the profile will be synced when the first DM exchange happens.
    """
    peer_url = os.getenv("PEER_NODE_URL", "").rstrip("/")
    if not peer_url:
        return False

    from inter_node_auth import create_auth_header

    payload = {
        "id": user_id,
        "username": username,
        "home_region": home_region,
        "avatar_data": avatar_data,
    }
    headers = create_auth_header(payload)

    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{peer_url}/api/inter-node/user/sync",
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

            # ── Delete propagation resync ──
            try:
                pending = await storage.get_pending_delete_propagations(limit=100)
            except Exception as exc:
                print(f"[cross_border_resync] Delete outbox query failed: {exc}")
            else:
                for row in pending:
                    ok = await forward_delete_to_peer(
                        row["op_type"], row["target_id"], row.get("payload", ""), storage,
                    )
                    if ok:
                        try:
                            await storage.mark_delete_propagated(row["id"])
                        except Exception as exc:
                            print(
                                f"[cross_border_resync] Mark delete propagated failed "
                                f"for {row['id']}: {exc}"
                            )
