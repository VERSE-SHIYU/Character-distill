# -*- coding: utf-8 -*-
"""D24 ②：自动打标不再给模型来源类标签。

来源类标签（动漫 / 游戏 / 男频 / 女频 / 原创）说的是书的出处；自动打标只把角色名、
身份、性格和 200 字背景给模型看，模型猜不出来。它瞎猜出的「原创」随后被发布兜底
抄进 ``market_tags``，已出版小说就进了「原创」分类。

本文件锁五条行为（前三行是本次要改的，后两行是现在已成立、防止改坏的）：

  1. ``_auto_tag`` 丢掉模型返回的来源类标签；
  2. ``_auto_tag`` 的提示词里不出现来源类候选；
  3. 发布时用户没填 ``market_tags`` → 兜底只从卡的 tags 里取题材类，再取前 5 个；
  4. 用户填了 ``market_tags`` → 原样写入；
  5. ``GET /api/market/tags`` 仍是同样 15 个、同样顺序（市场分类按钮不变）。

期望值全部是**字面量**，不从被测模块 import —— 从源码 import 就自证自明，变异杀不掉
（同 ``test_identify_failure_channels`` 对文案的处理）。

变异靶点：
  - 1/2：``_auto_tag`` 的候选与过滤改回 ``PRESET_TAGS``；
  - 3：发布兜底的题材过滤去掉，改回 ``ai_tags[:5]``；
  - 5：``PRESET_TAGS`` 增删或换序。
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from conftest import PG_ENV, TEST_DATABASE_URL
from core.distiller import Distiller
from core.moderation import publish_review as PR
from core.moderation.decision_engine import Decision, DecisionEngine
from deps import get_storage
from routers import market as M
from routers.auth import get_current_user
from storage.postgres_store import PostgresStore

SOURCE_TAGS = ("动漫", "游戏", "男频", "女频", "原创")


class _FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[tuple[str, list[dict]]] = []

    def chat(self, system, messages, max_tokens=None, **kw):
        self.calls.append((system, messages))
        return self.reply


def _ask_model(reply: str) -> tuple[Distiller, _FakeLLM]:
    llm = _FakeLLM(reply)
    return Distiller(llm), llm


# ── 1. 自动打标丢掉来源类标签 ────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="D24 ② 未实现：自动打标仍保留来源类标签")
def test_auto_tag_drops_source_tags():
    distiller, _ = _ask_model('["原创","古风","男频"]')
    assert distiller._auto_tag({"name": "张三"}) == ["古风"]


# ── 2. 提示词不再列来源类候选 ────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="D24 ② 未实现：提示词仍列来源类候选")
def test_auto_tag_prompt_has_no_source_tags():
    distiller, llm = _ask_model("[]")
    distiller._auto_tag({"name": "张三"})
    prompt = llm.calls[0][1][0]["content"]
    for tag in SOURCE_TAGS:
        assert tag not in prompt, f"提示词里仍有来源类候选「{tag}」"


# ── 发布兜底（真 PG，与 test_market_publish_policy 同口径） ──────────────────

_CARD = {"name": "张三", "personality": "温和"}


@pytest.fixture
async def store():
    s = PostgresStore(TEST_DATABASE_URL)
    await s._ensure_initialized()
    yield s
    await s.close()


@pytest.fixture
async def owner(store):
    uid = f"u_{uuid.uuid4().hex[:12]}"
    await store.create_user(uid, f"n_{uuid.uuid4().hex[:12]}", "x")
    return uid


@pytest.fixture
def _review_pass(monkeypatch):
    """缺省：关键词 allow、审核通过、全局 LLM 非 None。"""
    monkeypatch.setattr(M, "get_llm", lambda: object())

    def _allow(self, *_a):
        return Decision(decision="allow", confidence=0.5, tier="t", queue_priority=None)

    async def _review(card_json, llm, storage=None):
        return {
            "content": {"pass": True, "reason": ""},
            "injection": {"pass": True, "reason": "", "error": False},
        }

    monkeypatch.setattr(DecisionEngine, "decide", _allow)
    monkeypatch.setattr(PR, "auto_review_split", _review)


def _client(store, uid) -> AsyncClient:
    app = FastAPI()
    app.include_router(M.router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {"id": uid, "username": "t", "role": "user"}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _publish_and_read_market_tags(store, uid, card_tags, body) -> str:
    tid = f"txt_{uuid.uuid4().hex}"
    await store.save_text(tid, "src.txt", "content", user_id=uid)
    cid = f"card_{uuid.uuid4().hex}"
    card = {**_CARD, "tags": card_tags}
    await store.save_card(cid, tid, "张三", json.dumps(card, ensure_ascii=False), user_id=uid)

    async with _client(store, uid) as c:
        r = await c.post(f"/api/market/{cid}/publish", json=body)
    assert r.status_code == 200, r.text

    published = await store.get_card_owned(r.json()["card_id"], uid)
    return published["market_tags"]


# ── 3. 没填 market_tags：兜底只抄题材类 ──────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="D24 ② 未实现：兜底仍抄全部标签")
@PG_ENV.skipif("市场发布兜底用例")
async def test_publish_fallback_keeps_only_genre_tags(store, owner, _review_pass):
    assert await _publish_and_read_market_tags(store, owner, ["原创", "古风"], {}) == "古风"


# ── 4. 用户填了 market_tags：原样写入 ────────────────────────────────────────


@PG_ENV.skipif("市场发布兜底用例")
async def test_publish_keeps_user_market_tags(store, owner, _review_pass):
    got = await _publish_and_read_market_tags(
        store, owner, ["原创", "古风"], {"market_tags": "原创,古风"},
    )
    assert got == "原创,古风"


# ── 5. 市场分类按钮：15 个、原顺序 ──────────────────────────────────────────


async def test_market_tags_endpoint_unchanged():
    app = FastAPI()
    app.include_router(M.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/market/tags")
    assert r.status_code == 200
    assert r.json()["tags"] == [
        "恋爱", "动漫", "游戏", "治愈", "悬疑", "古风", "校园",
        "奇幻", "科幻", "日常", "虐心", "搞笑", "男频", "女频", "原创",
    ]
