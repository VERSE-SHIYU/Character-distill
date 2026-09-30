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

import base64
import datetime
import hashlib
import hmac
import json
import os
import time
import uuid
from types import SimpleNamespace

from http_message_signatures import (
    HTTPMessageSigner,
    HTTPMessageVerifier,
    HTTPSignatureKeyResolver,
    algorithms,
)
from http_message_signatures.exceptions import HTTPMessageSignaturesException, InvalidSignature
from http_message_signatures.structures import CaseInsensitiveDict

from core.fingerprint import key_fingerprint


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


# ═══════════════════════════════════════════════════════════════════════════
# v2：RFC 9421 HTTP Message Signatures（库 `http-message-signatures`，HMAC-SHA256）
#
# 与 v1 的差别（选型与出处见 docs/specs/inter-node-v2.md）：
#   - 签名覆盖方法、目标节点（@authority）、路径、查询串与请求体摘要（Content-Digest，
#     RFC 9530）—— 请求体不能挪到别的接口、也不能挪去另一台节点；
#   - 每个请求带随机 nonce，接收端落库登记，同一请求第二次到达即拒（防重放）；
#   - keyid = 密钥指纹，接收端同时认「当前密钥 + 上一把密钥」，换密钥不用两台同时停机。
#
# 不覆盖 @scheme / @target-uri：请求经 nginx 转到 app 时 scheme 已变成 http、端口也变了。
# @authority 由接收端用**本节点配置的对外域名**（`INTER_NODE_SELF_HOST`）来算，**不信请求自带
# 的 Host 头**：nginx 是 `server_name _` + `proxy_set_header Host $host`，Host 由发请求的一方
# 自己写，信它就等于没绑节点 —— 发给 A 的请求原样送到 B、Host 仍写 A，签名照样过。
# ═══════════════════════════════════════════════════════════════════════════

_PREV_SECRET_ENV = "INTER_NODE_SECRET_PREV"
_SIGN_VERSION_ENV = "INTER_NODE_SIGN_VERSION"   # 发送端用哪一版："1"（缺省）或 "2"
_ACCEPT_V1_ENV = "INTER_NODE_ACCEPT_V1"         # 接收端还认不认 v1："1"（缺省）或 "0"
_SELF_HOST_ENV = "INTER_NODE_SELF_HOST"         # 本节点对外域名（= 对端 PEER_NODE_URL 里的那个）

#: 必须被签名覆盖的组件。库本身**不检查**覆盖了哪些 —— 它按请求里声明的清单验，
#: 所以这张表由我们在验签通过后再核一遍（Mastodon 对 RFC 9421 的要求同形）。
REQUIRED_COMPONENTS = ("@method", "@authority", "@path", "@query", "content-digest")

#: 签名有效期。库缺省是 1 天，必须显式传。
MAX_AGE = datetime.timedelta(seconds=30)
#: 容忍的时钟偏差。库缺省 5 秒；2026-09-30 两台实测读数相差 8 秒（含 ssh 发起时差），
#: 5 秒会把领先节点发出的正常请求判成「来自未来」。
CLOCK_SKEW = datetime.timedelta(seconds=30)
#: nonce 保留时长：必须 ≥ 有效期 + 时钟偏差，否则窗口内的重放会因为登记已被清掉而放过。
NONCE_KEEP_SECONDS = 120


def sign_version() -> int:
    return 2 if os.getenv(_SIGN_VERSION_ENV, "1").strip() == "2" else 1


def accepts_v1() -> bool:
    return os.getenv(_ACCEPT_V1_ENV, "1").strip() != "0"


def _keys() -> dict[str, bytes]:
    """keyid → 密钥。当前密钥必有；上一把可选（换密钥的过渡期）。"""
    current = get_inter_node_secret()
    keys = {key_fingerprint(current): current.encode()}
    prev = os.getenv(_PREV_SECRET_ENV, "")
    if prev:
        keys[key_fingerprint(prev)] = prev.encode()
    return keys


class _KeyResolver(HTTPSignatureKeyResolver):
    def __init__(self, keys: dict[str, bytes]) -> None:
        self._keys = keys

    def resolve_public_key(self, key_id: str):
        try:
            return self._keys[key_id]
        except KeyError:
            raise InvalidSignature(f"未知 keyid {key_id!r}") from None

    resolve_private_key = resolve_public_key


def content_digest(body: bytes) -> str:
    """RFC 9530 `Content-Digest` 的值（sha-256）。"""
    return "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode() + ":"


def sign_request_v2(request) -> None:
    """就地给一个 `httpx.Request` 加 `Content-Digest` / `Signature-Input` / `Signature`。"""
    current = get_inter_node_secret()
    request.headers["Content-Digest"] = content_digest(request.content)
    HTTPMessageSigner(
        signature_algorithm=algorithms.HMAC_SHA256,
        key_resolver=_KeyResolver({key_fingerprint(current): current.encode()}),
    ).sign(
        request,
        key_id=key_fingerprint(current),
        covered_component_ids=REQUIRED_COMPONENTS,
        nonce=uuid.uuid4().hex,
        include_alg=True,
    )


def self_host() -> str:
    return os.getenv(_SELF_HOST_ENV, "").strip().lower()


def _verify_v2_signature(method: str, host: str, path: str, query: str,
                         headers: dict[str, str], body: bytes) -> tuple[bool, str, str]:
    """只做无状态那一半（摘要 + 签名 + 覆盖清单 + 时间窗）。返回 (ok, 原因, nonce)。

    `host` 是本节点配置的对外域名，不是请求头里的 Host。
    """
    if not host:
        return False, f"{_SELF_HOST_ENV} 未配置，不收 v2 签名", ""
    claimed = headers.get("content-digest", "")
    if not hmac.compare_digest(claimed, content_digest(body)):
        return False, "Content-Digest 与请求体不符", ""
    url = f"https://{host}{path}" + (f"?{query}" if query else "")
    message = SimpleNamespace(method=method, url=url, headers=CaseInsensitiveDict(headers))
    verifier = HTTPMessageVerifier(
        signature_algorithm=algorithms.HMAC_SHA256, key_resolver=_KeyResolver(_keys()),
    )
    verifier.max_clock_skew = CLOCK_SKEW
    try:
        results = verifier.verify(message, max_age=MAX_AGE)
    except (InvalidSignature, HTTPMessageSignaturesException) as exc:
        return False, f"签名验证失败：{exc}", ""
    # 键是结构化字段序列化后的形态（带引号，如 '"@method"'），末尾还有 @signature-params
    covered = {k.strip('"') for k in results[0].covered_components}
    missing = [c for c in REQUIRED_COMPONENTS if c not in covered]
    if missing:
        return False, f"签名未覆盖必需组件 {missing}", ""
    nonce = str(results[0].parameters.get("nonce") or "")
    if not nonce:
        return False, "签名缺少 nonce", ""
    return True, "", nonce


async def verify_inter_node_request(request, body: bytes, payload: dict, storage) -> tuple[bool, str, int]:
    """接收端唯一验签入口（Starlette `Request`）。返回 (ok, 原因, 版本)。

    带 `Signature-Input` 头 → 按 v2 验，并登记 nonce；否则按 v1 验（`INTER_NODE_ACCEPT_V1=0`
    时直接拒）。v1 与 v2 共存是「双收 → 切发 → 删旧」三步上线的第一步。
    """
    headers = {k.lower(): v for k, v in request.headers.items()}
    if "signature-input" in headers:
        ok, reason, nonce = _verify_v2_signature(
            request.method, self_host(), request.url.path,
            request.url.query, headers, body,
        )
        if not ok:
            return False, reason, 2
        if not await storage.claim_inter_node_nonce(nonce, keep_seconds=NONCE_KEEP_SECONDS):
            return False, "nonce 已用过（重放）", 2
        return True, "", 2
    if not accepts_v1():
        return False, "v1 签名已停用", 1
    ok, reason = verify_auth_header(headers.get("authorization"), payload)
    return ok, reason, 1
