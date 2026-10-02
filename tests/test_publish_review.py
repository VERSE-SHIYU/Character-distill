# -*- coding: utf-8 -*-
"""`core.moderation.publish_review`：发布审核策略，纯函数逐格测（不连库、不调模型）。

策略（2026-10-01）：明确违规才拒；拿不准的放行，但 log_reason 必须写明没审清的原因。
路由接线（状态码、review_log 落库、是否真发布）见 `test_market_publish_policy.py`。
"""
from __future__ import annotations

import pytest

from core.moderation import publish_review as PR
from core.moderation.publish_review import KeywordSignal, Outcome, decide


def _review(*, inj_pass=True, inj_error=False, content_pass=True, reason="r"):
    return {
        "content": {"pass": content_pass, "reason": reason},
        "injection": {"pass": inj_pass, "reason": reason, "error": inj_error},
    }


ALLOW = KeywordSignal("allow", ())
FLAG = KeywordSignal("flag", ("词A", "词B"))
BLOCK = KeywordSignal("block", ("禁词",))


# ── decide：策略矩阵 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("keyword, review, outcome, log_has, msg_has", [
    # 明确违规 → 拒
    (BLOCK, None, Outcome.REJECT, "[keyword-pregate] 禁词", "违禁关键词：禁词"),
    (ALLOW, _review(inj_pass=False, reason="越权"), Outcome.REJECT, "[publish-injection] 越权", "越权"),
    (ALLOW, _review(content_pass=False, reason="色情"), Outcome.REJECT, "色情", "色情"),
    # 注入判定优先于内容判定
    (ALLOW, _review(inj_pass=False, content_pass=False, reason="x"), Outcome.REJECT, "[publish-injection]", "指令性"),
    # 关键词疑似压不住 LLM 的明确判定
    (FLAG, _review(inj_pass=False, reason="越权"), Outcome.REJECT, "[publish-injection] 越权", "越权"),
    # 拿不准 → 放行留痕
    (FLAG, _review(), Outcome.PASS, "[keyword-pregate] 疑似，已放行待后台复核：词A,词B", ""),
    (ALLOW, _review(inj_pass=False, inj_error=True, reason="boom"), Outcome.PASS,
     "[publish-injection] 审核失败已放行：boom", ""),
    # 干净 → 放行，无痕
    (ALLOW, _review(), Outcome.PASS, "", ""),
])
def test_policy_matrix(keyword, review, outcome, log_has, msg_has):
    v = decide(keyword, review)
    assert v.outcome is outcome
    assert v.allowed is (outcome is Outcome.PASS)
    assert log_has in v.log_reason
    if outcome is Outcome.PASS:
        assert v.user_message == ""
        if not log_has:
            assert v.log_reason == "", "干净放行不能带备注"
    else:
        assert msg_has in v.user_message


def test_both_uncertain_notes_are_kept_per_layer():
    v = decide(FLAG, _review(inj_pass=False, inj_error=True, reason="boom"))
    assert v.allowed
    assert "[keyword-pregate]" in v.log_reason and "[publish-injection]" in v.log_reason


def test_injection_error_beats_its_own_pass_false():
    """出错时注入通道的 pass 也是 False —— 必须按「出错」放行，不能当成判定注入拒掉。"""
    assert decide(ALLOW, _review(inj_pass=False, inj_error=True)).allowed


def test_log_reason_is_clipped():
    v = decide(ALLOW, _review(inj_pass=False, reason="长" * 1000))
    assert len(v.log_reason) == PR.LOG_REASON_MAX


def test_review_required_unless_blocked():
    with pytest.raises(ValueError):
        decide(ALLOW, None)


# ── review_for_publish：编排 ─────────────────────────────────────────────────

async def test_keyword_block_skips_llm(monkeypatch):
    monkeypatch.setattr(PR, "keyword_signal", lambda _c: BLOCK)

    async def _must_not_run(*_a, **_kw):
        raise AssertionError("关键词已拒，不能再花 LLM 调用")
    monkeypatch.setattr(PR, "auto_review_split", _must_not_run)
    v = await PR.review_for_publish({"name": "x"}, llm=object())
    assert v.outcome is Outcome.REJECT


async def test_no_llm_publishes_with_note(monkeypatch):
    """未配 key（llm=None）走真 `auto_review_split`：注入通道出错 → 放行留痕。"""
    monkeypatch.setattr(PR, "keyword_signal", lambda _c: ALLOW)
    v = await PR.review_for_publish({"name": "x"}, llm=None)
    assert v.allowed
    assert "[publish-injection] 审核失败已放行" in v.log_reason


async def test_uncleared_pass_is_logged_as_warning(monkeypatch, caplog):
    monkeypatch.setattr(PR, "keyword_signal", lambda _c: FLAG)

    async def _ok(*_a, **_kw):
        return _review()
    monkeypatch.setattr(PR, "auto_review_split", _ok)
    with caplog.at_level("WARNING", logger=PR.logger.name):
        await PR.review_for_publish({"name": "x"}, llm=object())
    assert any("published without full clearance" in r.getMessage() for r in caplog.records)


def test_keyword_signal_reads_real_filter():
    """真关键词层接线：干净文本 → allow（不 mock，守 `keyword_signal` 的组装）。"""
    assert PR.keyword_signal({"name": "张三", "personality": "温和"}).decision == "allow"
