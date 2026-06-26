"""Inter-node authentication for cross-region sync (深圳 ↔ 新加坡).

Uses INTER_NODE_SECRET (separate env var, never JWT_SECRET or user tokens)
to HMAC-SHA256 sign cross-region API requests.

Usage — sender:
    from inter_node_auth import create_auth_header
    headers = create_auth_header({"user_id": "..."})
    await httpx.get("https://sg-node/api/sync/user", headers=headers)

Usage — receiver:
    from inter_node_auth import verify_auth_header
    valid, reason = verify_auth_header(headers, {"user_id": "..."})
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time


_INTER_NODE_SECRET_ENV = "INTER_NODE_SECRET"
_MAX_AGE_MS = 30_000  # 30 s clock skew tolerance


def get_inter_node_secret() -> str:
    """Read INTER_NODE_SECRET from env. Raises RuntimeError if unset or < 32 chars."""
    secret = os.getenv(_INTER_NODE_SECRET_ENV)
    if not secret:
        raise RuntimeError(
            f"{_INTER_NODE_SECRET_ENV} 未设置，跨节点同步不可用"
        )
    if len(secret) < 32:
        raise RuntimeError(
            f"{_INTER_NODE_SECRET_ENV} 长度不足 32 字符（当前 {len(secret)}）。"
            "请用 openssl rand -hex 32 生成"
        )
    return secret


def validate_inter_node_secret() -> None:
    """Validate INTER_NODE_SECRET strength if configured. Skip if unset (single-node compat)."""
    secret = os.getenv(_INTER_NODE_SECRET_ENV)
    if not secret:
        return
    get_inter_node_secret()  # raises RuntimeError if < 32 chars


def _sign(payload: dict, timestamp: int) -> str:
    """HMAC-SHA256 hex digest of timestamp + sorted JSON payload."""
    secret = get_inter_node_secret()
    msg = f"{timestamp}:{json.dumps(payload, separators=(',', ':'), sort_keys=True)}"
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def create_auth_header(payload: dict) -> dict[str, str]:
    """Build HMAC-signed auth header dict for cross-node requests.

    Returns {"Authorization": "HMAC-SHA256 ts=<ms>,sig=<hexdigest>"}
    """
    ts = int(time.time() * 1000)
    sig = _sign(payload, ts)
    return {"Authorization": f"HMAC-SHA256 ts={ts},sig={sig}"}


def verify_auth_header(
    authorization: str | None,
    payload: dict,
) -> tuple[bool, str]:
    """Verify an Authorization header from create_auth_header.

    Returns (True, "") on success or (False, reason) on failure.
    """
    if not authorization:
        return False, "缺少 Authorization 头"

    parts = authorization.split()
    if len(parts) != 2 or parts[0] != "HMAC-SHA256":
        return False, "Authorization 格式错误"

    try:
        params = dict(param.split("=", 1) for param in parts[1].split(","))
        ts = int(params["ts"])
        sig = params["sig"]
    except (KeyError, ValueError):
        return False, "Authorization 参数解析失败"

    # Clock skew check
    now_ms = int(time.time() * 1000)
    if abs(now_ms - ts) > _MAX_AGE_MS:
        return False, "请求已过期（时钟偏差过大）"

    expected = _sign(payload, ts)
    if not hmac.compare_digest(expected, sig):
        return False, "签名验证失败"

    return True, ""
