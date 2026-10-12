"""受控探针：超出历史窗口之后，角色靠本场记录还接不接得上（spec `session-record-extract.md` §7.2）。

做法：拿重放得到的本场记录（截到提问之前那一轮），历史只放最后一处种子 / 改口**之后**的对话
（所以它们一定不在发给模型的消息里——脚本逐题核对），同样的题各问两次：带记录、不带记录。
提示词由真引擎拼（`ChatEngine._compose_system_prompt` + `_build_llm_messages`）。和线上相比少了
`_attach_turn_blocks`（`core/chat_engine.py:443`、`:485`：给当前用户消息附时间感知块和做法提醒），
这一步不含本场记录，不影响结论；也不走路由器、不检索长期记忆（``include_dynamic=False``，不调 `search_memory`），也不跑每轮收尾
（不评估、不抽取、不入库）—— 只看「本场记录」这一条路。

用法（本地，需 DEEPSEEK_API_KEY；卡片 JSON 从库里导出）::

    python scripts/probe_session_record.py --transcript <transcript.json> \
        --replay <全量重放的 replay.json> --card-json <card.json> --user-role "<会话的 user_role>" \
        --out e2e/scratch/probe-session-record
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.llm_adapter import LLMAdapter
from core import session_record as rec
from core.chat_engine import ChatEngine
from core.schema import CharacterCard

# 每题答对要出现的词（全部出现才算对）；第 139 题（态度）没有标准答案，由 Claude 读答案判。
EXPECT: dict[str, tuple[str, ...]] = {
    "你还记得我叫什么、是哪里人吗？": ("林小满", "苏州"),
    "我见了什么东西会躲？": ("猫",),
    "我之前在哪受过伤？伤在哪？": ("西湖", "脚"),
    "我最爱吃什么点心？": ("桂花糕",),
    "我说下回给你带什么书？": ("孟子",),
    "我下个月要去哪里？": ("金陵",),
    "你答应过我什么事？": ("棋",),
    "我妹妹叫什么？多大了？": ("小芸", "十二"),
    "我那本书一开始说的是哪本，后来改成了哪本？": ("论语", "孟子"),
}
WITH_RECORD_MIN = 8      # 带记录：9 道有标准答案的题至少答对 8 道（④ 验收判定线「接不上 3 题及以上不合格」）
WITHOUT_RECORD_MAX = 3   # 不带记录：至多蒙对 3 道，否则说明还有别的路在兜底，探针作废


def record_before(log: list[dict], last_turn: int) -> rec.SessionRecord:
    """按重放日志把记录还原到 ``last_turn`` 那一轮结束时（取代的旧条目置为失效）。"""
    entries: dict[int, rec.Entry] = {}
    for row in log:
        if row["turn"] > last_turn:
            break
        for e in row["new"]:
            entry = rec.Entry(**e)
            if entry.replaces is not None and entry.replaces in entries:
                old = entries[entry.replaces]
                entries[old.id] = rec.Entry(**{**old.__dict__, "active": False})
            entries[entry.id] = entry
    return rec.SessionRecord(tuple(entries[k] for k in sorted(entries)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--replay", required=True)
    ap.add_argument("--card-json", required=True, help="cards.card_json 的原文，存成一个文件")
    ap.add_argument("--user-role", default="")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    turns = json.loads(Path(args.transcript).read_text(encoding="utf-8"))["turns"]
    log = json.loads(Path(args.replay).read_text(encoding="utf-8"))["log"]
    card = CharacterCard.model_validate_json(Path(args.card_json).read_text(encoding="utf-8"))
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        print("需要 DEEPSEEK_API_KEY")
        return 2
    llm = LLMAdapter(api_key=key)

    questions = [t for t in turns if t["kind"] == "question"]
    first_q = questions[0]["turn"]
    last_anchor = max(t["turn"] for t in turns if t["kind"] in ("seed", "change"))
    anchors = [t["user"] for t in turns if t["kind"] in ("seed", "change")]
    history: list[dict] = []
    for t in turns:
        if last_anchor < t["turn"] < first_q:
            history += [{"role": "user", "content": t["user"]}, {"role": "assistant", "content": t["reply"]}]
    record = record_before(log, first_q - 1)

    rows, score = [], {"with": 0, "without": 0}
    for arm, arm_record in (("with", record), ("without", rec.SessionRecord())):
        engine = ChatEngine(llm=llm, rag=None, card=card, card_id="probe", user_role=args.user_role,
                            storage=None, session_id="probe", is_new_session=False)
        engine.load_session_record(arm_record)
        for q in questions:
            system = engine._compose_system_prompt(q["user"], include_dynamic=False)
            messages = engine._build_llm_messages(history, q["user"])
            sent = system + "".join(m["content"] for m in messages)
            leaked = [a for a in anchors if a in sent]
            if leaked:   # 种子 / 改口的原话出现在发出去的内容里，这一题就测不了「窗口外」
                print(f"ABORT: anchor text leaked into the prompt: {leaked}")
                return 3
            answer = llm.chat(system, messages)
            expect = EXPECT.get(q["user"])
            ok = None if expect is None else all(w in answer for w in expect)
            score[arm] += bool(ok)
            rows.append({"arm": arm, "turn": q["turn"], "question": q["user"], "answer": answer,
                         "expect": list(expect or ()), "ok": ok})
            print(f"[{arm:<7} {q['turn']}] {'?' if ok is None else ('✓' if ok else '✗')} {answer[:80]}")

    passed = score["with"] >= WITH_RECORD_MIN and score["without"] <= WITHOUT_RECORD_MAX
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "probe.json").write_text(json.dumps({
        "history_turns": len(history) // 2, "record_block": rec.render_block(record),
        "score": score, "pass": passed, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"带记录 {score['with']}/{len(EXPECT)}（要 ≥ {WITH_RECORD_MIN}），"
          f"不带记录 {score['without']}/{len(EXPECT)}（要 ≤ {WITHOUT_RECORD_MAX}）→ {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
