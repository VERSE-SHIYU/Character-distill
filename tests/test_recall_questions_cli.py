"""☁ 出题命令行：generate 的起草/关卡/修复/跳过/解析失败，probe 的摸底。

本地**不跑**（spec §0：☁ 测试由复核方在沙箱带内存上限跑）。替身只配 ``chat``、
固定返回、无流式；提示词用假模板，不写论文原文。替身按提示词开头路由，不按调用
顺序 —— generate 在 8 个线程里并发，顺序不可依赖。
"""
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import recall_questions as rq  # noqa: E402
from eval_recall import Book  # noqa: E402

NAME = "甲"
SEG = "乙" * 120
FRAG_A = "丙丁戊己庚辛壬癸子丑"
FRAG_B = "子丑寅卯辰巳午未申酉"
# 名字只出现一次；紧跟其后的 10 字片段全书唯一，且落在 ±120 的锚点窗口内。
SOURCE = SEG + NAME + FRAG_A + SEG
SOURCE_TWO = SEG + NAME + FRAG_A + SEG + SEG + NAME + FRAG_B + SEG
BAD_FRAGMENT = "乙乙乙乙乙乙乙乙乙乙"

PROMPTS = {
    "draft": "DRAFT|{character}|{title}|{window}|{feedback}",
    "own_words_A5": "A5|{question}|{quotes}",
    "verifier_A6": "A6|{question}|{quotes}|{intent_criterion}|{intent_field}",
    "intent_factual_A3": "Factual lookup: row",
}

LIFT = {"searchable": True, "reason": "lift", "suggestion": "换个说法问"}
OWN = {"searchable": False, "reason": "", "suggestion": ""}
GOOD = {"answerable": True, "sufficient": True, "per_doc": {"e1": True}, "necessary": [True],
        "leak": False, "intent_match": True, "notes": ""}
DRAFT = {"query": "你当时在场吗？", "should": [FRAG_A]}


class FakeLLM:
    """按提示词开头路由的替身：DRAFT / A5 / A6，线程安全，返回固定或不重复的回复。"""

    def __init__(self, drafts, *, a5=None, a6=None):
        self.drafts = drafts if callable(drafts) else list(drafts)
        self.a5 = list(a5 or [OWN])
        self.a6 = list(a6 or [GOOD])
        self.lock = threading.Lock()
        self.calls = []
        self.last_usage = {"prompt_tokens": 3, "completion_tokens": 5}

    @staticmethod
    def _next(queue):
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def chat(self, system_prompt, messages, max_tokens=None):
        with self.lock:
            self.calls.append(system_prompt)
            if system_prompt.startswith("DRAFT|"):
                if callable(self.drafts):
                    reply = self.drafts(system_prompt)
                else:
                    reply = self._next(self.drafts)
            elif system_prompt.startswith("A5|"):
                reply = self._next(self.a5)
            else:
                reply = self._next(self.a6)
            self.last_usage = {"prompt_tokens": 3, "completion_tokens": 5}
        return reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)


def _book(*, source=SOURCE, cases=None):
    return Book(book="b", title="书", user_id="u", text_id="t1", character=NAME,
                source=source, aliases=[], cases=cases, card_id=None, card=None,
                embedding_key="k", embedding_region="cn")


def _cases(out_dir, stem="t1_甲"):
    path = Path(out_dir) / f"{stem}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_generate_passes_and_writes_eval_cases(tmp_path):
    llm = FakeLLM([DRAFT])

    summary = rq.run_generate([_book()], llm, PROMPTS, tmp_path)

    assert _cases(tmp_path) == [{"id": "a01", "phase": None, "query": "你当时在场吗？",
                                 "should": [FRAG_A], "should_not": [],
                                 "own_words_flag": False}]
    assert summary["books"]["t1_甲"]["outcomes"] == {"passed": 1}
    assert summary["books"]["t1_甲"]["anchors"] == 1
    assert summary["usage"]["calls"] == 3  # 起草 → A.5 → A.6
    assert (tmp_path / "t1_甲.anchors.json").exists()
    assert (tmp_path / "verdicts.jsonl").exists()
    assert (tmp_path / "summary.json").exists()


def test_skip_leaves_anchor_empty_without_gate_calls(tmp_path):
    llm = FakeLLM([{"skip": True}])

    summary = rq.run_generate([_book()], llm, PROMPTS, tmp_path)

    assert summary["books"]["t1_甲"]["skip"] == 1
    assert summary["books"]["t1_甲"]["outcomes"] == {}
    assert _cases(tmp_path) == []
    assert len(llm.calls) == 1


def test_ground_failure_rewrites_with_feedback(tmp_path):
    llm = FakeLLM([{"query": "你当时在场吗？", "should": [BAD_FRAGMENT]}, DRAFT])

    summary = rq.run_generate([_book()], llm, PROMPTS, tmp_path)

    assert _cases(tmp_path)[0]["should"] == [FRAG_A]
    assert summary["books"]["t1_甲"]["repair_passes"]["1"]["ground"] == 1
    assert summary["books"]["t1_甲"]["outcomes"] == {"passed": 1}
    assert "Previous attempt was rejected" in llm.calls[1]  # 第二次起草带反馈
    assert len(llm.calls) == 4  # 失败那轮不过关 1，重写后再过 3


def test_verifier_notes_reach_next_draft_as_feedback(tmp_path):
    """A.6 不过时，其 ``notes`` 是唯一的说明 —— 必须原话进下一次起草的 {feedback}。"""
    rejected = {"answerable": True, "sufficient": False, "per_doc": {"e1": True},
                "necessary": [True], "leak": False, "intent_match": True,
                "notes": "缺少 e2 的出处"}
    llm = FakeLLM([DRAFT, DRAFT], a6=[rejected, GOOD])

    summary = rq.run_generate([_book()], llm, PROMPTS, tmp_path)

    drafts = [c for c in llm.calls if c.startswith("DRAFT|")]
    assert len(drafts) == 2
    assert "缺少 e2 的出处" in drafts[1]
    assert summary["books"]["t1_甲"]["outcomes"] == {"passed": 1}


def test_repair_cap_discards_after_three_attempts(tmp_path):
    llm = FakeLLM([{"query": "你当时在场吗？", "should": [BAD_FRAGMENT]}] * 3)

    summary = rq.run_generate([_book()], llm, PROMPTS, tmp_path)

    assert summary["books"]["t1_甲"]["outcomes"] == {"discarded:ground": 1}
    assert _cases(tmp_path) == []
    assert len(llm.calls) == 3  # 三次起草，硬关一次模型调用都不花


def test_soft_flag_last_round_still_runs_verifier(tmp_path):
    llm = FakeLLM([DRAFT] * 3, a5=[LIFT, LIFT, LIFT])

    summary = rq.run_generate([_book()], llm, PROMPTS, tmp_path)

    assert _cases(tmp_path)[0]["own_words_flag"] is True
    assert summary["books"]["t1_甲"]["outcomes"] == {"passed_own_words_flag": 1}
    assert summary["books"]["t1_甲"]["repair_passes"]["2"]["critic"] == 1
    assert len(llm.calls) == 7  # 3 起草 + 3 A.5 + 1 A.6


def test_unparsed_draft_twice_becomes_parse_discard(tmp_path):
    llm = FakeLLM(["not json", "still not json"])

    summary = rq.run_generate([_book()], llm, PROMPTS, tmp_path)

    assert summary["books"]["t1_甲"]["outcomes"] == {"discarded:parse": 1}
    assert len(llm.calls) == 2  # 同一轮重调一次，不计修复次数


def test_human_rows_are_not_generated(tmp_path):
    human = tmp_path / "human.jsonl"
    human.write_text(json.dumps({"id": "q01", "query": "你当时在场吗？",
                                 "should": [FRAG_A]}, ensure_ascii=False) + "\n",
                     encoding="utf-8")
    llm = FakeLLM([])

    summary = rq.run_generate([_book(cases=str(human))], llm, PROMPTS, tmp_path)

    assert summary["books"] == {}
    assert summary["b（人工题）"]["n"] == 1
    assert llm.calls == []


def test_two_anchors_share_usage_and_stay_apart(tmp_path):
    def pick(prompt):
        return {"query": "你当时在场吗？",
                "should": [FRAG_A if FRAG_A in prompt else FRAG_B]}

    summary = rq.run_generate([_book(source=SOURCE_TWO)], FakeLLM(pick), PROMPTS, tmp_path)

    rows = _cases(tmp_path)
    assert {row["id"] for row in rows} == {"a01", "a02"}
    assert {row["should"][0] for row in rows} == {FRAG_A, FRAG_B}
    assert summary["books"]["t1_甲"]["anchors"] == 2
    assert summary["usage"]["calls"] == 6 and summary["usage"]["prompt_tokens"] == 18


def test_probe_reports_disagreement_and_excludes_chitchat(tmp_path):
    human = tmp_path / "human.jsonl"
    human.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in (
            {"id": "q01", "query": "你当时在场吗？", "should": [FRAG_A]},
            {"id": "q02", "query": "早呀", "should": []},
        )) + "\n",
        encoding="utf-8")
    llm = FakeLLM([], a6=[GOOD, {**GOOD, "leak": True}])

    summary = rq.run_probe([_book(cases=str(human))], llm, PROMPTS,
                           tmp_path / "probe.json", gates=("a6",))

    assert summary["excluded_no_should"] == ["q02"] and summary["n"] == 1
    assert summary["a6"]["fail"] == 1 and summary["a6"]["unparsed"] == 0
    assert summary["a6"]["leak_true"] == 1 and summary["a6"]["agree_rate"] == 0.0
    assert (tmp_path / "probe.json").exists()
    assert summary["usage"]["calls"] == 2


def test_evidence_ids_are_the_quote_labels():
    """证据编号只有一处出处：A.6 判 ``per_doc`` 的键与提示词里 ``[eN]`` 的标号同源。"""
    from core import recall_synth

    quotes = [FRAG_A, FRAG_B]
    ids = recall_synth.evidence_ids(quotes)

    assert ids == ["e1", "e2"]
    assert recall_synth.quotes_block(quotes).splitlines() == [
        f"[{i}] {q}" for i, q in zip(ids, quotes)]
