"""目标检查：场景重排挂在 ``SCENE_RERANK`` 开关后，默认关。

目标（D15b：百炼重排服务本身 P50 约 3.5 s，生产 5 s 工具时限下会整次丢场景）：
默认不调重排，结果与 cedbb233 的向量顺序填块一致；``SCENE_RERANK=1`` 时行为不变。

1. 未设 ``SCENE_RERANK``：重排一次都不调；按向量顺序返回；不算退化、不计时、不打 WARNING。
2. ``SCENE_RERANK=0`` / 空串：同 1。
3. ``SCENE_RERANK=1``：重排调用一次，顺序按重排分数。

第一个提交里 1、2 为 ``xfail(strict=True)``（当时仍调重排），实现提交去掉标记。
"""

from __future__ import annotations

import logging

import pytest

from tests.test_scene_rerank_goal import NO_CAP, _engine, _install_rerank


def _forbid_rerank(monkeypatch):
    from core import reranker

    def boom(*a, **kw):
        raise AssertionError("开关关时不应调用 reranker.rerank")

    monkeypatch.setattr(reranker, "rerank", boom)


def _assert_vector_order_no_rerank(hits, caplog):
    assert [it.meta["vector_rank"] for it in hits] == list(range(len(hits)))
    assert len(hits) > 0
    assert hits.rerank_fallback is False
    assert hits.rerank_ms is None
    assert hits.rerank_tokens is None
    assert all(it.meta["rerank_score"] is None for it in hits)
    warns = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert not any("场景重排失败" in m for m in warns), warns


def test_unset_skips_rerank(monkeypatch, caplog):
    monkeypatch.delenv("SCENE_RERANK", raising=False)
    _forbid_rerank(monkeypatch)
    with caplog.at_level(logging.WARNING):
        hits = _engine().query_scenes("问题", "甲", token_budget=NO_CAP)
    _assert_vector_order_no_rerank(hits, caplog)


@pytest.mark.parametrize("value", ["0", ""])
def test_off_values_skip_rerank(monkeypatch, caplog, value):
    monkeypatch.setenv("SCENE_RERANK", value)
    _forbid_rerank(monkeypatch)
    with caplog.at_level(logging.WARNING):
        hits = _engine().query_scenes("问题", "甲", token_budget=NO_CAP)
    _assert_vector_order_no_rerank(hits, caplog)


def test_on_calls_rerank_once_and_orders_by_score(monkeypatch):
    monkeypatch.setenv("SCENE_RERANK", "1")
    sink: list = []
    _install_rerank(monkeypatch, scores={3: 0.9, 1: 0.8, 0: 0.7}, sink=sink)
    hits = _engine().query_scenes("问题", "甲", token_budget=NO_CAP)
    assert len(sink) == 1
    assert [it.meta["vector_rank"] for it in hits][:3] == [3, 1, 0]
    assert [it.meta["rerank_score"] for it in hits][:3] == [0.9, 0.8, 0.7]
    assert hits.rerank_fallback is False
