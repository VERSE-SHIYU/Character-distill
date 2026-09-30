"""向对端节点发一条签名请求：读 `PEER_NODE_URL` → 签名 → POST。

这三步原先在 `web/cross_border_sync.py`（6 处）与 `web/routers/admin.py`（联邦用户列表）
各抄一遍。新调用方一律走这里；`cross_border_sync.py` 那 6 处随「节点间签名协议 v2」
一并迁入（那条线要逐个改这 6 处的签名，一次改完）。

失败不在这里吞：调用方各自决定降级（联邦列表 = 标记对端不可达）还是报错（跨节点禁用 = 502）。
"""

from __future__ import annotations

import os

import httpx

from inter_node_auth import create_auth_header

TIMEOUT_S = 10  # 与既有 6 处转发同值


class PeerNotConfigured(RuntimeError):
    """`PEER_NODE_URL` 为空：本节点是单节点部署。"""


def peer_url() -> str:
    return os.getenv("PEER_NODE_URL", "").rstrip("/")


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout)


async def post_to_peer(path: str, payload: dict) -> httpx.Response:
    """POST `payload` 到对端 `path`。未配置对端抛 `PeerNotConfigured`；网络错误原样上抛。"""
    base = peer_url()
    if not base:
        raise PeerNotConfigured("PEER_NODE_URL 未配置")
    headers = create_auth_header(payload)
    async with _client(TIMEOUT_S) as client:
        return await client.post(f"{base}{path}", json=payload, headers=headers)
