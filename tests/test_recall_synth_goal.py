"""0d 目标检查：出题锚点覆盖、关卡判定（CoverSynth 式 2：只有硬关决定录取）、
不许补题、诊断重合分布、多本书汇总（纯函数）。

夹具带真实书的格式：省略号、中文引号、换行、重复句（返工经验第 54 条）。
"""
import pytest

from core import recall_eval
from core.quotes import normalize

try:
    from core.recall_synth import (
        check_prompt, intent_slots, judge, ledger_problems, lexical_overlap, outcome_counts,
        overlap_histogram, quotes_block, render, sample_anchors,
    )
except ModuleNotFoundError as exc:  # S3 前模块未实现：调用时记红，不让整份文件收集失败
    if exc.name != "core.recall_synth":
        raise

    def _not_implemented(*args, **kwargs):
        raise NotImplementedError("core.recall_synth 尚未实现")

    check_prompt = intent_slots = judge = ledger_problems = lexical_overlap = (
        outcome_counts
    ) = overlap_histogram = quotes_block = render = sample_anchors = _not_implemented

pytestmark = pytest.mark.xfail(strict=True, reason="0d 题库工具尚未实现")

FILLER = "风过回廊，檐下的灯一盏一盏亮起来……\n远处有人喊：“收衣裳了。”\n"
NAME = "阿灼"

GOOD = {"answerable": True, "sufficient": True, "per_doc": {"e1": True}, "necessary": [True],
        "leak": False, "intent_match": True, "notes": ""}
REAL = {"self_contained": True, "realistic": True, "reason": "", "suggestion": ""}
OWN = {"searchable": False, "reason": "", "suggestion": ""}
LIFT = {"searchable": True, "reason": "lift", "suggestion": "换个说法问"}


def _book(n: int, *, front_only: bool = False) -> str:
    parts = []
    for i in range(n):
        parts.append(FILLER * 6)
        if not front_only or i < n // 2:
            parts.append(f"{NAME}第{i}回说：“这件事我记了{i}年。”")
    return "".join(parts)


def _setup():
    src = _book(20)
    a = sample_anchors(src, [NAME], k=10, half_width=60)[3]
    i = int(src[a["pos"]:a["pos"] + 8].split("第")[1].split("回")[0])
    return normalize(src), a, f"{NAME}第{i}回说：“这件事我记了{i}年"


def _draft(q, should, i="d1"):
    return {"id": i, "query": q, "should": [should]}


def _j(d, a, norm, *, realism=REAL, critic=OWN, verdict=GOOD, attempt=1):
    return judge(d, a, norm, realism=realism, critic=critic, verdict=verdict, attempt=attempt)


# ── 1 锚点 ─────────────────────────────────────────────────────────────

def test_anchors_cover_each_stratum_at_most_once():
    src = _book(120)
    anchors = sample_anchors(src, [NAME], k=60)
    strata = [a["pos"] * 60 // len(src) for a in anchors]
    assert len(strata) == len(set(strata)) and len(strata) >= 55
    assert all(NAME in a["text"] for a in anchors)


def test_anchors_do_not_borrow_from_other_strata():
    src = _book(120, front_only=True)
    anchors = sample_anchors(src, [NAME], k=60)
    assert max(a["pos"] for a in anchors) == src.rfind(NAME)
    assert len(anchors) == len({a["pos"] * 60 // len(src) for a in anchors})


def test_gap_in_middle_leaves_strata_empty():
    head = "".join(FILLER * 6 + f"{NAME}甲{i}。" for i in range(20))
    mid = FILLER * 6 * 40
    tail = "".join(FILLER * 6 + f"{NAME}乙{i}。" for i in range(20))
    src = head + mid + tail
    pos = [a["pos"] for a in sample_anchors(src, [NAME], k=60)]
    assert len(pos) == len(set(pos)) == len({p * 60 // len(src) for p in pos})
    assert not any(len(head) <= p < len(head) + len(mid) for p in pos)


def test_anchor_norm_bounds_match_source():
    src = _book(20)
    a = sample_anchors(src, [NAME], k=10)[3]
    assert normalize(src)[a["nstart"]:a["nend"]] == normalize(a["text"])


# ── 2 关 1（硬）──────────────────────────────────────────────────────────

def test_ground_gate():
    norm, a, inside = _setup()
    assert _j(_draft("你那时候说过什么", inside), a, norm)["status"] == "passed"
    for bad in (f"{NAME}第19回说：“这件事我记了19年",   # 窗口外
                FILLER[:16],                        # 全书多处
                "这件事……记了3年那时候",              # 省略号
                "这件事我记"):                        # 太短
        assert _j(_draft("你那时候说过什么", bad), a, norm)["status"] == "ground_fail", bad


def test_ground_rules_are_the_annotation_rules():
    """关 1 与人工标注自检同一份规则：同一片段两边结论一致。"""
    norm, a, inside = _setup()
    for frag in (inside, FILLER[:16], "这件事我记"):
        own = recall_eval.fragment_problems("d1", "should", 0, frag, norm, None)
        try:
            recall_eval.validate_cases([{"id": "d1", "phase": None, "should": [frag]}], norm, {None: None})
            ann = []
        except recall_eval.AnnotationError as exc:
            ann = str(exc).splitlines()[1:]
        assert own == ann


# ── 3 自述措辞评审（软，附录 A.5）─────────────────────────────────────────

def test_own_words_critic_triggers_repair_not_rejection():
    norm, a, inside = _setup()
    d = _draft("你那时候说过什么", inside)
    for n in (1, 2):
        r = _j(d, a, norm, critic=LIFT, attempt=n)
        assert (r["status"], r["problems"]) == ("own_words_repair", ["换个说法问"])
    r = _j(d, a, norm, critic=LIFT, attempt=3)
    assert (r["status"], r["own_words_flag"]) == ("passed", True)


def test_flagged_draft_still_needs_hard_gates():
    norm, a, inside = _setup()
    d = _draft("你那时候说过什么", inside)
    assert _j(d, a, norm, critic=LIFT, verdict={**GOOD, "leak": True}, attempt=3)["status"] == "discarded"


def test_critic_unparsed_is_neither_pass_nor_discard():
    norm, a, inside = _setup()
    d = _draft("你那时候说过什么", inside)
    for c in (None, {"searchable": "yes"}, {}):
        assert _j(d, a, norm, critic=c, attempt=3)["status"] == "critic_unparsed"


def test_critic_skipped_when_ground_fails():
    norm, a, _ = _setup()
    r = _j(_draft("你那时候说过什么", "这件事我记"), a, norm, critic=LIFT, attempt=1)
    assert r["status"] == "ground_fail"


# ── 4 核查（硬）──────────────────────────────────────────────────────────

def test_verify_gate_follows_section_3_4_list():
    norm, a, inside = _setup()
    d = _draft("你那时候说过什么", inside)
    for bad in ({"answerable": False}, {"sufficient": False}, {"leak": True},
                {"per_doc": {"e1": False}}, {"intent_match": False}):
        assert _j(d, a, norm, verdict={**GOOD, **bad})["status"] == "verify_fail", bad
    # necessary 不在 §3.4 的核查列表里：只报、不卡
    assert _j(d, a, norm, verdict={**GOOD, "necessary": [False]})["status"] == "passed"


def test_verify_unparsed():
    norm, a, inside = _setup()
    d = _draft("你那时候说过什么", inside)
    for bad in (None, {"answerable": "yes"}, {**GOOD, "per_doc": {}}, {**GOOD, "per_doc": {"e2": True}},
                {k: v for k, v in GOOD.items() if k != "intent_match"}):
        assert _j(d, a, norm, verdict=bad, attempt=3)["status"] == "verify_unparsed", bad


def test_realism_gate_is_hard():
    norm, a, inside = _setup()
    d = _draft("你那时候说过什么", inside)
    for bad in ({"self_contained": False}, {"realistic": False}):
        assert _j(d, a, norm, realism={**REAL, **bad})["status"] == "realism_fail"
        assert _j(d, a, norm, realism={**REAL, **bad}, attempt=3)["status"] == "discarded"
    assert _j(d, a, norm, realism={"realistic": True}, attempt=3)["status"] == "realism_unparsed"


# ── 提示词：照抄文件只认哈希，占位只换登记过的名字 ─────────────────────────────

def test_prompt_must_match_registered_hash():
    with pytest.raises(ValueError):
        check_prompt("realism_A4", "You are {persona}. 改过一个字")


def test_render_keeps_json_braces_and_requires_exact_placeholders():
    t = 'Q: {question}\nP:\n{quotes}\nRespond ONLY JSON: {"searchable": true|false}'
    out = render("own_words_A5", t, question="问", quotes=quotes_block(["甲", "乙"]))
    assert out == 'Q: 问\nP:\n[e1] 甲\n[e2] 乙\nRespond ONLY JSON: {"searchable": true|false}'
    with pytest.raises(ValueError):
        render("own_words_A5", t, question="问")
    with pytest.raises(ValueError):
        render("own_words_A5", t, question="问", quotes="x", persona="多给的")


def test_intent_slots_carry_table_4_row_verbatim():
    row = "Factual lookup: Retrieve a stated fact, definition, value, threshold, name, or reference precisely, as written (the default when no other intent fits)."
    slots = intent_slots(row + "\n")
    assert row in slots["intent_criterion"] and '"intent_match": bool,' in slots["intent_field"]


# ── 5 修复上限只作用于硬关 ────────────────────────────────────────────────

def test_repair_cap_on_hard_gates():
    norm, a, inside = _setup()
    bad = _draft("你那时候说过什么", f"{NAME}第19回说：“这件事我记了19年")
    assert _j(bad, a, norm, attempt=2)["status"] == "ground_fail"
    assert (_j(bad, a, norm, attempt=3)["status"], _j(bad, a, norm, attempt=3)["gate"]) == ("discarded", "ground")
    ok = _draft("你那时候说过什么", inside)
    assert _j(ok, a, norm, verdict={**GOOD, "answerable": False}, attempt=3)["gate"] == "verify"
    assert _j(ok, a, norm, attempt=3)["status"] == "passed"


def test_outcome_counts_split_hard_discards_and_soft_flags():
    norm, a, inside = _setup()
    ok = _draft("你那时候说过什么", inside)
    bad = _draft("你那时候说过什么", f"{NAME}第19回说：“这件事我记了19年")
    rs = [_j(ok, a, norm), _j(ok, a, norm, critic=LIFT, attempt=3),
          _j(bad, a, norm, attempt=3), _j(ok, a, norm, verdict={**GOOD, "leak": True}, attempt=3),
          _j(ok, a, norm, realism={**REAL, "realistic": False}, attempt=3),
          _j(ok, a, norm, critic=LIFT, attempt=1)]
    assert outcome_counts(rs) == {"passed": 1, "passed_own_words_flag": 1, "discarded:ground": 1,
                                  "discarded:verify": 1, "discarded:realism": 1}


def test_no_backfill():
    probs = ledger_problems(["a01", "a02", "x99"], ["a01", "a02", "a03"], ["a02"])
    assert [p.split(":")[0] for p in probs] == ["a02", "x99"]


# ── 诊断：重合分布（不当关卡）──────────────────────────────────────────────

def test_overlap_is_against_gold_quotes_and_denominator_is_question():
    quotes = ["魏无羡把笛子横在唇边，吹了一支不成调的曲子，蓝湛站在廊下听着，一言不发。"]
    assert round(lexical_overlap("你还记得魏无羡在廊下吹笛子那次吗", quotes), 2) == 0.33
    assert round(lexical_overlap("魏无羡把笛子横在唇边吹了一支不成调的曲子", quotes), 2) == 1.0
    assert lexical_overlap("甲乙丙丁戊", ["甲乙丙"]) == 0.5


def test_overlap_histogram_bins():
    h = overlap_histogram([0.0, 0.05, 0.33, 0.5, 0.99, 1.0])
    assert (h["0.0"], h["0.3"], h["0.5"], h["0.9"], sum(h.values())) == (2, 1, 1, 2, 6)


def test_overlap_never_decides_status():
    """重合再高，只要评审与核查都过就录取 —— 重合只是诊断数字。"""
    norm, a, inside = _setup()
    copied = inside.replace("：“", "")
    assert lexical_overlap(copied, [inside]) == 1.0
    assert _j(_draft(copied, inside), a, norm)["status"] == "passed"


# ── 6 多本书汇总 ─────────────────────────────────────────────────────────

def test_suite_overall_counts_rows_not_books():
    rows = {"甲": [{"hit": True, "leak": False}] * 3, "乙": [{"hit": False, "leak": True}]}
    out = recall_eval.aggregate_suite(rows)
    assert (out["by_book"]["甲"]["r_at_3"], out["by_book"]["乙"]["r_at_3"]) == (1.0, 0.0)
    assert (out["overall"]["r_at_3"], out["overall"]["leak_rate"], out["overall"]["n"]) == (0.75, 0.25, 4)
