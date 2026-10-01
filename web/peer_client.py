"""向对端节点发一条签名请求：本仓所有节点间出站请求的**唯一**出口。

一处定义三件事：
  1. 地址：`PEER_NODE_URL`，且必须是 https —— 节点间载荷含私信正文、角色卡内容，
     明文跨境传输不可接受（2026-09-30 核查：两台原先都是 http）。配成 http 直接拒发，
     不降级；地址的形状（https / 有主机名 / 无路径 / 无 query / 无 userinfo）在
     启动期由 `validate_peer_config` 机器校验，坏配置起不来；
  2. 签名：`INTER_NODE_SIGN_VERSION` 选 v1（缺省）或 v2（RFC 9421）。两台都部署了
     「能收 v2」的版本之后再切 2，见 docs/specs/inter-node-v2.md 的上线步骤；
  3. 超时：10 秒（与原先 7 处手抄的转发同值）。

失败不在这里吞：调用方各自决定降级、记日志还是报错。
"""

from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

import httpx

from inter_node_auth import (
    create_auth_header,
    get_inter_node_secret,
    self_host,
    sign_request_v2,
    sign_version,
)

TIMEOUT_S = 10


class PeerNotConfigured(RuntimeError):
    """`PEER_NODE_URL` 为空：本节点是单节点部署。"""


class PeerConfigError(RuntimeError):
    """`PEER_NODE_URL` / `INTER_NODE_SELF_HOST` 配置无效：拒发，启动期也拒绝。"""


def peer_url() -> str:
    """规范化的对端 base（`https://host[:port]`）；未配置（strip 后为空）返回空串。"""
    return _parse_peer_url() or ""


def _parse_peer_url() -> str | None:
    """解析并校验 `PEER_NODE_URL`（全文件唯一读取处）。返回规范 base；空 → None（单节点）；无效 → `PeerConfigError`。"""
    raw = os.getenv("PEER_NODE_URL", "").strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    if parts.scheme != "https":
        raise PeerConfigError(f"PEER_NODE_URL 必须是 https://（当前 {raw!r}）")
    if not parts.hostname:
        raise PeerConfigError(f"PEER_NODE_URL 缺少主机名（当前 {raw!r}）")
    if parts.path not in ("", "/"):
        raise PeerConfigError(f"PEER_NODE_URL 不能带路径（当前 {parts.path!r}）")
    if parts.query or parts.fragment:
        raise PeerConfigError("PEER_NODE_URL 不能带 query 或 fragment")
    if parts.username is not None or parts.password is not None:
        raise PeerConfigError("PEER_NODE_URL 不能带 userinfo")
    netloc = parts.hostname
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    return f"https://{netloc}"


def _peer_hostname(base: str) -> str:
    return (urlsplit(base).hostname or "").rstrip(".").lower()


def validate_peer_config() -> None:
    """启动期校验。`PEER_NODE_URL` 空 → 单节点，直接返回；否则配置必须完整且自洽。

    必须早于 `_lifespan` 里第一个碰存储的 `_reconcile_distill_tasks`：失败时库还没迁移，
    回滚到 PREV_SHA 才是安全的（见 docs/specs/peer-config-guard.md 约束 3）。
    """
    base = _parse_peer_url()
    if base is None:
        return
    get_inter_node_secret()  # 未设置 / 短于 32 字符 → RuntimeError
    my_host = self_host().rstrip(".").lower()
    if not my_host:
        raise PeerConfigError(
            "配置了 PEER_NODE_URL 时 INTER_NODE_SELF_HOST 必填（= 对端 PEER_NODE_URL 里的域名）"
        )
    peer_host = _peer_hostname(base)
    if my_host == peer_host:
        raise PeerConfigError(
            f"INTER_NODE_SELF_HOST（{self_host()!r}）不能等于对端主机名（{peer_host!r}）"
        )


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout)


def build_request(path: str, payload: dict) -> httpx.Request:
    """构造并签好一个发往对端的请求（不发送）。"""
    base = _parse_peer_url()
    if base is None:
        raise PeerNotConfigured("PEER_NODE_URL 未配置")
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
    """POST `payload` 到对端 `path`。配置问题抛 `PeerNotConfigured` / `PeerConfigError`；
    网络错误原样上抛（`httpx.HTTPError`）。"""
    request = build_request(path, payload)
    async with _client(TIMEOUT_S) as client:
        return await client.send(request)
