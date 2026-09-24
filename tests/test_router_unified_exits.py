# -*- coding: utf-8 -*-
"""spec-71 A-2 首段：33 处 `except Exception → HTTPException(500, …)` 删掉之后，失败走向哪。

**这两条钉的是「统一出口真的接管了」，不是「删掉了几行」。** 删了多少处在 AST 上数得清
（判据：`ExceptHandler.type` 是名字 `Exception`、body 里有 `raise HTTPException(5xx)`），
但那只证明**拦路的那段代码没了**；「异常此后落到哪个出口、落成什么码、原文去了哪里」
是另一回事，只在真 app 上跑出来才算数。

**为什么用生产 app（`server.app`）**：统一出口的注册（`register_domain_error_handlers`）
与那条全局 `Exception` 处理器都长在 `web/server.py` 的装配里。另搭一个最小 app 等于把
「生产这份装配生效了」测成「我照着又注册了一遍」——
`tests/test_failure_alerting.py::test_unhandled_exception_is_logged_and_alerts_...` 同款理由。

**两条各钉一处不可替代的差异**（删包装这个动作对两者的影响方向相反）：
  - `at_reply`（`web/routers/market.py`）：删除前，`LLMCallRefused`（门拒绝，语义是 403）
    被宽 `except` 一律重抛成 500「操作失败，请稍后重试」——判定与故障不可分；删除后落到
    `_llm_error_handler` → **403 + 门给的原话**。
  - `inter_node`（`web/routers/inter_node.py`）：删除前，异常原文被拼进 `detail` 上屏；
    删除后 `detail` 只剩通用文案，原文随**带堆栈的 ERROR** 进日志。

**边界**：不测真 LLM 网络（`get_user_llm` 换成拒答替身）、不测 HMAC 摘要算法
（`verify_auth_header` 换成放行替身 —— 本文件要观测的是失败通道，不是签名）。两条都用
生产 app + `raise_server_exceptions=False`（全局 `Exception` 处理器发完响应还会往上抛，
`ServerErrorMiddleware` 的行为）。

Run: pytest tests/test_router_unified_exits.py -v
"""
from __future__ import annotations

import asyncio
import logging
import uuid

import pytest
from fastapi.testclient import TestClient
from pwdlib import PasswordHash

import deps
import server
from adapters.llm_adapter import LLMCallRefused
from core.schema import CharacterCard
from routers import inter_node as IN
from routers.auth import _create_access_token, get_jwt_secret
from storage.sqlite_store import SQLiteStore

_UNIFIED_500 = "服务器内部错误，请稍后重试"
#: 门给的、已审的上屏口径（`LLMCallRefused.reason` 同时就是 `user_message`）。
_REFUSAL_REASON = "境内不支持境外模型"
#: inter_node 的异常原文：删除包装前会被拼进 `detail`，删除后只准出现在日志里。
_RAW_DETAIL = "上游返回 502：peer-db 连接被拒绝（trace 0xdeadbeef）"

_PW_HASH = PasswordHash.recommended().hash("Pass1234")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / "unified_exits.db"))


@pytest.fixture
def user(store):
    uid = f"usr_{uuid.uuid4().hex[:12]}"
    _run(store.create_user(uid, "U_" + uuid.uuid4().hex[:8], _PW_HASH))
    return _run(store.get_user_by_id(uid))


@pytest.fixture
def client(store, monkeypatch):
    """生产 app（真中间件 + 真统一出口）：只把 storage 换成用例自己那份。

    中间件与依赖读的都是 `deps._storage`，故只 patch 这一处就够（与
    `tests/test_identity_resolution.py` / `tests/test_settings_config_llm_available.py`
    的 client 夹具同源）。
    """
    monkeypatch.setattr(deps, "_storage", store)
    return TestClient(server.app, raise_server_exceptions=False)


def _token(user: dict) -> dict:
    return {"Authorization": f"Bearer {_create_access_token(user['id'], user['username'], get_jwt_secret())}"}


def _seed_public_pair(store, owner_id: str) -> tuple[str, str]:
    """同一本『书』下两张卡：`src`（被评论的）+ `at`（public、被 @ 的）。返回 (src, at)。"""
    text_id = f"txt_{uuid.uuid4().hex[:12]}"
    src_id, at_id = f"card_{uuid.uuid4().hex[:12]}", f"card_{uuid.uuid4().hex[:12]}"
    card_json = CharacterCard(name="甲", identity="测试用角色").model_dump_json()
    _run(store.save_text(text_id, "src.txt", "正文", user_id=owner_id))
    _run(store.save_card(src_id, text_id, "乙", card_json, owner_id))
    _run(store.save_card(at_id, text_id, "甲", card_json, owner_id))
    assert _run(store.update_card_visibility(at_id, "public")), "种子前提破了：at 卡没能置为 public"
    return src_id, at_id


# ── 1. 门拒绝的 LLM 异常：403 + 门给的原话（删除前是 500「操作失败」） ─────────

class _RefusingLLM:
    """`at_reply` 只用到 `chat`；`preflight` 不经过（替身替换的是整个 `get_user_llm`）。"""

    def chat(self, *_args, **_kwargs):
        raise LLMCallRefused(_REFUSAL_REASON, "https://api.other.com")


def test_at_reply_refused_llm_is_403_with_the_gate_reason(client, store, user, monkeypatch):
    """被门拒绝的调用点 → 403 + `detail` 是门给的原话，不再被宽 except 重抛成 500。

    变异（本条的判据来源）：把 `web/routers/market.py` 里 `at_reply` 的 LLM 调用重新包回
    `try/except Exception → raise HTTPException(500, "操作失败，请稍后重试")` —— 本条立刻红在
    状态码上（500 ≠ 403），`detail` 一并变回通用文案。
    """
    src_id, at_id = _seed_public_pair(store, user["id"])

    async def _refusing_llm(_user_id, _storage=None):
        return _RefusingLLM()

    monkeypatch.setattr(deps, "get_user_llm", _refusing_llm)

    resp = client.post(
        f"/api/market/{src_id}/comments/at-reply",
        json={"at_card_id": at_id, "comment_content": "在吗"},
        headers=_token(user),
    )

    assert resp.status_code == 403, f"门拒绝没落成 403：{resp.status_code} {resp.text}"
    assert resp.json()["detail"] == _REFUSAL_REASON, \
        f"上屏文案不是门给的原话：{resp.json().get('detail')!r}"


# ── 2. inter_node 的内部异常：通用文案上屏、原文只进日志（删除前原文拼进 detail） ──

def test_inter_node_failure_hides_the_raw_text_and_logs_a_traced_error(
    client, store, monkeypatch, caplog,
):
    """未捕获的内部异常 → 500 + 通用文案；原文**不在**响应体里，随带堆栈的 ERROR 进日志。

    变异（本条的判据来源）：把 `receive_dm` 的存储调用重新包回
    `except Exception as exc: raise HTTPException(500, f"...: {exc}")` —— 本条立刻红两处：
    `detail` 变成拼了原文的串，且因 `HTTPException` 不经过全局处理器而**一条 ERROR 都没有**。
    """
    monkeypatch.setattr(IN, "verify_auth_header", lambda *_a, **_k: (True, ""))

    def _boom(_msg_id):
        raise RuntimeError(_RAW_DETAIL)

    monkeypatch.setattr(store, "get_dm_message_unscoped", _boom)
    payload = {"id": "m1", "sender_id": "u1", "receiver_id": "u2", "content": "hi"}

    with caplog.at_level(logging.ERROR):
        resp = client.post("/api/inter-node/dm/receive", json=payload)

    assert (resp.status_code, resp.json()["detail"]) == (500, _UNIFIED_500), \
        f"失败没走统一出口：{resp.status_code} {resp.text}"
    assert _RAW_DETAIL not in resp.text, f"内部原文漏进响应体：{resp.text!r}"

    traced = [r for r in caplog.records
              if r.levelno == logging.ERROR and "/api/inter-node/dm/receive" in r.getMessage()]
    assert [(r.levelno, bool(r.exc_info)) for r in traced] == [(logging.ERROR, True)], \
        f"该恰有一条带堆栈的 ERROR（原文的排障线索靠它），实得 {[(r.levelno, bool(r.exc_info)) for r in traced]}"
