"""向对端节点发一条签名请求：本仓所有节点间出站请求的**唯一**出口。

一处定义三件事：
  1. 地址：`PEER_NODE_URL`，且必须是 https —— 节点间载荷含私信正文、角色卡内容，
     明文跨境传输不可接受（2026-09-30 核查：两台原先都是 http）。配成 http 直接拒发，
     不降级；
  2. 签名：`INTER_NODE_SIGN_VERSION` 选 v1（缺省）或 v2（RFC 9421）。两台都部署了
     「能收 v2」的版本之后再切 2，见 docs/specs/inter-node-v2.md 的上线步骤；
  3. 超时：10 秒（与原先 7 处手抄的转发同值）。

失败不在这里吞：调用方各自决定降级、记日志还是报错。
"""

from __future__ import annotations

import json
import os

import httpx

from inter_node_auth import create_auth_header, sign_request_v2, sign_version

TIMEOUT_S = 10


class PeerNotConfigured(RuntimeError):
    """`PEER_NODE_URL` 为空：本节点是单节点部署。"""


class PeerNotSecure(RuntimeError):
    """`PEER_NODE_URL` 不是 https：拒发，不把节点间载荷明文送出去。"""


def peer_url() -> str:
    return os.getenv("PEER_NODE_URL", "").rstrip("/")


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout)


def build_request(path: str, payload: dict) -> httpx.Request:
    """构造并签好一个发往对端的请求（不发送）。"""
    base = peer_url()
    if not base:
        raise PeerNotConfigured("PEER_NODE_URL 未配置")
    if not base.lower().startswith("https://"):
        raise PeerNotSecure("PEER_NODE_URL 必须是 https://")
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    request = httpx.Request(
        "POST", f"{base}{path}", content=body, headers={"Content-Type": "application/json"},
    )
    if sign_version() == 2:
        sign_request_v2(request)
    else:
        request.headers.update(create_auth_header(payload))
    return request


async def post_to_peer(path: str, payload: dict) -> httpx.Response:
    """POST `payload` 到对端 `path`。配置问题抛 `PeerNotConfigured` / `PeerNotSecure`；
    网络错误原样上抛（`httpx.HTTPError`）。"""
    request = build_request(path, payload)
    async with _client(TIMEOUT_S) as client:
        return await client.send(request)
