"""☁ 出题命令行：台账拒收、关卡顺序、软关打标记、解析失败、锚点与摸底。

本地**不跑**（spec §0：☁ 测试由复核方在沙箱带内存上限跑）。替身只配 ``chat``、
固定返回、无流式；提示词用假模板，不写论文原文。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import recall_questions as rq  # noqa: E402
from core.recall_synth import sample_anchors  # noqa: E402
from eval_recall import Book, EvalAbort  # noqa: E402

NAME = "甲"
# 名字只出现一次；紧跟其后的 10 字片段全书唯一，且落在 ±120 的锚点窗口内。
SOURCE = "乙" * 120 + NAME + "丙丁戊己庚辛壬癸子丑" + "乙" * 120
FRAGMENT = "丙丁戊己庚辛壬癸子丑"

PROMPTS = {
    "own_words_A5": "{question}|{quotes}",
    "verifier_A6": "{question}|{quotes}|{intent_criterion}|{intent_field}",
    "intent_factual_A3": "Factual lookup: row",
}

LIFT = {"searchable": True, "reason": "lift", "suggestion": "换个说法问"}
OWN = {"searchable": False, "reason": "", "suggestion": ""}
GOOD = {"answerable": True, "sufficient": True, "per_doc": {"e1": True}, "necessary": [True],
        "leak": False, "intent_match": True, "notes": ""}


class FakeLLM:
    """只答卡片式 JSON 请求的替身：按顺序吐出固定回复。"""

    def __init__(self, replies):
        self.replies = [json.dumps(r, ensure_ascii=False) for r in replies]
        self.calls = []
        self.last_usage = {"prompt_tokens": 3, "completion_tokens": 5}

    def chat(self, system_prompt, messages, max_tokens=None):
        self.calls.append(system_prompt)
        return self.replies.pop(0)


def _book(*, cases=None):
    return Book(book="b", title="书", user_id="u", text_id="t1", character=NAME,
                source=SOURCE, aliases=[], cases=cases, card_id=None, card=None,
                embedding_key="k", embedding_region="cn")


def _anchors():
    anchors = sample_anchors(SOURCE, [NAME], k=1, half_width=120)
    return [{"id": "a01", **anchors[0]}]


def _work(tmp_path, anchors=None):
    work = tmp_path / "work"
    drafts = tmp_path / "drafts"
    work.mkdir()
    drafts.mkdir()
    (work / "t1_甲.anchors.json").write_text(
        json.dumps({"anchors": anchors or _anchors()}, ensure_ascii=False), encoding="utf-8")
    return work, drafts


def _draft(drafts, rows):
    (drafts / "t1_甲.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def _cases(work):
    text = (work / "t1_甲.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_check_passes_and_writes_eval_cases(tmp_path):
    work, drafts = _work(tmp_path)
    _draft(drafts, [{"id": "a01", "query": "你当时在场吗？", "should": [FRAGMENT]}])
    llm = FakeLLM([OWN, GOOD])

    summary = rq.run_check([_book()], llm, PROMPTS, drafts, work, round_no=1)

    rows = _cases(work)
    assert rows == [{"id": "a01", "phase": None, "query": "你当时在场吗？",
                     "should": [FRAGMENT], "should_not": [], "own_words_flag": False}]
    assert summary["books"]["t1_甲"]["outcomes"] == {"passed": 1}
    assert len(llm.calls) == 2  # A.5 → A.6，各一次


def test_soft_flag_last_round_still_runs_verifier(tmp_path):
    work, drafts = _work(tmp_path)
    _draft(drafts, [{"id": "a01", "query": "你当时在场吗？", "should": [FRAGMENT]}])
    llm = FakeLLM([LIFT, GOOD])

    rq.run_check([_book()], llm, PROMPTS, drafts, work, round_no=3)

    assert _cases(work)[0]["own_words_flag"] is True
    assert len(llm.calls) == 2


def test_ground_failure_costs_no_model_call(tmp_path):
    work, drafts = _work(tmp_path)
    _draft(drafts, [{"id": "a01", "query": "你当时在场吗？", "should": ["乙乙乙乙乙乙乙乙乙乙"]}])
    llm = FakeLLM([])

    summary = rq.run_check([_book()], llm, PROMPTS, drafts, work, round_no=1)

    assert summary["books"]["t1_甲"]["outcomes"] == {"ground_fail": 1}
    assert llm.calls == []


def test_ledger_rejects_before_any_model_call(tmp_path):
    work, drafts = _work(tmp_path)
    _draft(drafts, [{"id": "x99", "query": "问？", "should": [FRAGMENT]}])
    llm = FakeLLM([])
    with pytest.raises(EvalAbort, match="x99"):
        rq.run_check([_book()], llm, PROMPTS, drafts, work)
    assert llm.calls == []


def test_discarded_id_cannot_come_back(tmp_path):
    work, drafts = _work(tmp_path)
    (work / "t1_甲.discarded.json").write_text(json.dumps(["a01"]), encoding="utf-8")
    _draft(drafts, [{"id": "a01", "query": "问？", "should": [FRAGMENT]}])
    llm = FakeLLM([])
    with pytest.raises(EvalAbort, match="a01"):
        rq.run_check([_book()], llm, PROMPTS, drafts, work)


def test_skip_leaves_anchor_empty_without_calls(tmp_path):
    work, drafts = _work(tmp_path)
    _draft(drafts, [{"id": "a01", "skip": True}])
    llm = FakeLLM([])

    summary = rq.run_check([_book()], llm, PROMPTS, drafts, work)

    assert summary["books"]["t1_甲"]["skip"] == 1
    assert _cases(work) == [] and llm.calls == []


def test_unparsed_twice_becomes_parse_discard(tmp_path):
    work, drafts = _work(tmp_path)
    _draft(drafts, [{"id": "a01", "query": "你当时在场吗？", "should": [FRAGMENT]}])
    llm = FakeLLM(["not json", "still not json"])

    summary = rq.run_check([_book()], llm, PROMPTS, drafts, work)

    assert summary["books"]["t1_甲"]["outcomes"] == {"discarded:parse": 1}
    assert len(llm.calls) == 2  # 同一关重调一次，不计修复次数


def test_anchors_written_per_row(tmp_path):
    summary = rq.run_anchors([_book()], tmp_path / "work", k=1)
    assert summary == {"t1_甲": 1}
    payload = json.loads((tmp_path / "work" / "t1_甲.anchors.json").read_text(encoding="utf-8"))
    assert payload["anchors"][0]["id"] == "a01"
    assert payload["anchors"][0]["text"].startswith("乙" * 120 + NAME)


def test_probe_reports_disagreement_and_excludes_chitchat(tmp_path):
    human = tmp_path / "human.jsonl"
    human.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in (
            {"id": "q01", "query": "你当时在场吗？", "should": [FRAGMENT]},
            {"id": "q02", "query": "早呀", "should": []},
        )) + "\n",
        encoding="utf-8")
    llm = FakeLLM([GOOD, {**GOOD, "leak": True}])

    summary = rq.run_probe([_book(cases=str(human))], llm, PROMPTS,
                           tmp_path / "probe.json", gates=("a6",))

    assert summary["excluded_no_should"] == ["q02"] and summary["n"] == 1
    assert summary["a6"]["fail"] == 1 and summary["a6"]["unparsed"] == 0
    assert summary["a6"]["leak_true"] == 1 and summary["a6"]["agree_rate"] == 0.0
    assert (tmp_path / "probe.json").exists()
    assert summary["usage"]["calls"] == 2
