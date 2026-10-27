"""☁ 多轮题（``recall_questions.py multiturn``）：选题、关 1、A.6 上文、修复上限、丢弃不补、产物读回。

替身照 test_recall_questions_cli.py 的写法：只配 ``chat``、按提示词开头路由（并发下顺序不可依赖），
返回字符串，解析走生产的 ``loads_llm_json``（解析不了照生产记 ``discarded:parse``）。
"""
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import recall_questions as rq  # noqa: E402
from eval_recall import Book, load_cases, load_suite  # noqa: E402

from core import recall_synth  # noqa: E402

NAME = "甲"
ROSTER = [
    {"name": "甲", "aliases": ["阿甲"]},
    {"name": "乙", "aliases": ["小乙"]},
    {"name": "丙", "aliases": []},
    # ⑲：别名混有通称与别人的名字 —— 只能拿来查泄漏，不能拿来选题
    {"name": "云绮", "aliases": ["小姐", "云汐玥"]},
    {"name": "林黛玉", "aliases": ["林姑娘"]},
]
OVERRIDES = rq.ANCHOR_NAME_OVERRIDES
FRAG = "丙丁戊己庚辛壬癸子丑"

PROMPTS = {
    "multiturn": "MT|{character}|{title}|{question}|{entity}|{feedback}",
    "verifier_A6": "A6|{question}|{quotes}|{intent_criterion}|{intent_field}",
    "intent_factual_A3": "Factual lookup: row",
}

GOOD = {"answerable": True, "sufficient": True, "per_doc": {"e1": True}, "necessary": [True],
        "leak": False, "intent_match": True, "notes": ""}
BAD = {**GOOD, "answerable": False, "notes": "上文没交代是谁"}
TURNS = {"turn1": "我最近老想起乙。", "turn2": "你跟他后来去了哪儿？"}


class FakeLLM:
    """MT（改写）/ A6（核查）两路；队列剩最后一条后一直重复它。"""

    def __init__(self, rewrites, *, a6=None):
        self.rewrites = list(rewrites)
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
            queue = self.rewrites if system_prompt.startswith("MT|") else self.a6
            reply = self._next(queue)
            self.last_usage = {"prompt_tokens": 3, "completion_tokens": 5}
        return reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)


def _case(cid, query, **extra):
    return {"id": cid, "phase": None, "query": query, "should": [FRAG], "should_not": [], **extra}


def _setup(tmp_path, cases):
    path = tmp_path / "in" / "t1_甲.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases),
                    encoding="utf-8")
    book = Book(book="b", title="书", user_id="u", text_id="t1", character=NAME,
                source="", aliases=["阿甲"], cases=str(path), card_id=None, card=None,
                embedding_key="k", embedding_region="cn")
    row = {"book": "b", "title": "书", "user_id": "u", "text_id": "t1",
           "character": NAME, "cases": str(path), "card_id": None}
    return book, row


def _run(tmp_path, cases, llm):
    book, row = _setup(tmp_path, cases)
    out = tmp_path / "out"
    summary = rq.run_multiturn([book], [ROSTER], llm, PROMPTS, out, suite_rows=[row])
    return summary, out


def _mt_cases(out):
    lines = (out / "t1_甲.multiturn.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# ── 选题 ──────────────────────────────────────────────────────────────

def _pick(question):
    return recall_synth.multiturn_entity(question, ROSTER, NAME, OVERRIDES)


def test_entity_by_real_name_only_aliases_kept_for_leak_check():
    assert _pick("你和乙后来怎样了？") == ("乙", ["乙"], ["乙", "小乙"])
    assert _pick("你和丙吵过架吗？") == ("丙", ["丙"], ["丙"])
    # 别名不参与选题：「小姐」是云绮的别名，问句里没有任何本名 → 不选
    assert _pick("小姐那天穿的什么？") is None
    assert _pick("云汐玥后来怎样了？") is None        # 别名里混进的别人名字也不选
    # 覆盖表（与找锚点同一张 ANCHOR_NAME_OVERRIDES）：「黛玉」→ 林黛玉
    assert _pick("黛玉那天哭了吗？") == (
        "林黛玉", ["林黛玉", "黛玉"], ["林黛玉", "黛玉", "林姑娘"])
    # 本人不选；名单外的地名 / 事件名不选
    assert _pick("甲，你那天在哪？") is None
    assert _pick("你去过长安城吗？") is None


# ── 关 1 ──────────────────────────────────────────────────────────────

def test_gate1_turn1_needs_real_name_turn2_blocks_name_and_every_alias():
    check = recall_synth.multiturn_problems
    call, leak = ["林黛玉", "黛玉"], ["林黛玉", "黛玉", "林姑娘"]
    assert check("我想起黛玉了。", "你们后来去哪了？", call, leak) == []
    assert check("我想起林姑娘了。", "你们后来去哪了？", call, leak)  # turn1 只有别名
    assert check("我想起黛玉了。", "林姑娘后来去哪了？", call, leak)  # turn2 含别名
    assert check("我想起黛玉了。", "你和黛玉后来去哪了？", call, leak)  # turn2 含覆盖称呼


def test_real_multiturn_prompt_renders_with_registered_slots():
    text = recall_synth.render(
        "multiturn", recall_synth.MULTITURN_PROMPT, character="甲", title="书",
        question="你和乙去哪了？", entity="乙", feedback="")
    assert "乙" in text and "{" not in text.replace('{"', "")


# ── 主流程 ────────────────────────────────────────────────────────────

def test_a6_question_carries_context_and_output_fields(tmp_path):
    case = _case("q1", "你和乙后来去哪了？", extra_field="keep")
    llm = FakeLLM([TURNS])
    summary, out = _run(tmp_path, [case, _case("q2", "你那天吃了什么？")], llm)

    a6 = [c for c in llm.calls if c.startswith("A6|")]
    assert a6 and f"上文：{TURNS['turn1']}\n问题：{TURNS['turn2']}" in a6[0]

    [mt] = _mt_cases(out)
    assert mt["id"] == "q1_mt" and mt["source_id"] == "q1" and mt["entity"] == "乙"
    assert mt["query"] == TURNS["turn2"]
    assert mt["history"] == [{"role": "user", "content": TURNS["turn1"]}]
    assert mt["should"] == [FRAG] and mt["extra_field"] == "keep" and mt["phase"] is None
    role = summary["books"]["t1_甲"]
    assert role["candidates"] == 1 and role["passed"] == 1


def test_gate1_repair_cap_then_discard_without_refill(tmp_path):
    leaky = {"turn1": "我想起乙了。", "turn2": "你和乙后来去哪了？"}
    llm = FakeLLM([leaky])
    summary, out = _run(tmp_path, [_case("q1", "你和乙后来去哪了？")], llm)

    rewrites = [c for c in llm.calls if c.startswith("MT|")]
    assert len(rewrites) == recall_synth.MAX_ATTEMPTS
    assert "乙" in rewrites[1].split("|")[-1]          # 原因带回重写
    assert not any(c.startswith("A6|") for c in llm.calls)  # 关 1 不过不花 A.6
    assert _mt_cases(out) == []
    role = summary["books"]["t1_甲"]
    assert role["discarded"] == {"multiturn": 1}
    assert load_suite_rows(out) == []                  # 一题不剩的角色不进套件


def test_verify_notes_fed_back_and_pass_on_second_try(tmp_path):
    llm = FakeLLM([TURNS], a6=[BAD, GOOD])
    summary, out = _run(tmp_path, [_case("q1", "你和乙后来去哪了？")], llm)

    rewrites = [c for c in llm.calls if c.startswith("MT|")]
    assert len(rewrites) == 2 and "上文没交代是谁" in rewrites[1]
    assert len(_mt_cases(out)) == 1
    assert summary["books"]["t1_甲"]["repair_passes"]["1"]["verify"] == 1


def test_verify_fails_to_cap_then_discard(tmp_path):
    llm = FakeLLM([TURNS], a6=[BAD])
    summary, out = _run(tmp_path, [_case("q1", "你和乙后来去哪了？")], llm)
    assert _mt_cases(out) == []
    assert summary["books"]["t1_甲"]["discarded"] == {"verify": 1}


def test_unparsed_rewrite_twice_is_parse_discard(tmp_path):
    llm = FakeLLM(["不是 JSON"])
    summary, _ = _run(tmp_path, [_case("q1", "你和乙后来去哪了？")], llm)
    assert summary["books"]["t1_甲"]["discarded"] == {"parse": 1}


def test_suite_reads_back_with_eval_recall(tmp_path):
    _, out = _run(tmp_path, [_case("q1", "你和乙后来去哪了？")], FakeLLM([TURNS]))
    entries = load_suite(out / "suite_eval.json")
    assert [Path(e["cases"]).name for e in entries] == ["t1_甲.multiturn.jsonl"]
    assert load_cases(entries[0]["cases"])[0]["id"] == "q1_mt"
    assert (out / "verdicts.jsonl").exists() and (out / "summary.json").exists()


def load_suite_rows(out):
    return json.loads((out / "suite_eval.json").read_text(encoding="utf-8"))
