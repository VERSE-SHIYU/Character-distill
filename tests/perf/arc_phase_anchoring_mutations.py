# -*- coding: utf-8 -*-
"""arc-phase-anchoring 变异矩阵驱动 —— 位置校正的每条变异各有专属红源。

spec：`docs/specs/arc-phase-anchoring.md` §5（M1–M19b）+ §4.1 + §9.5（M31–M36，审计补）。覆盖域是新锁
`tests/test_phase_anchoring.py`（纯计算层 `core.phase_anchoring` / `core.quotes`
与五个调用点 E1–E5 的判别器全在这一个文件）。跑法交 `mutation_framework.run_matrix`
—— 与另外几个驱动共用一份执行原语；本文件只留变异表与基线门。

**预跑实测（2026-10-05，本机一次跑完）：每条在 arc-phase-anchoring 上全红；期望一律
`RED`、不设 marker（每条的红源是整条判据，不靠红源里某个标记串鉴别）。覆盖域里 32 条
判别器**逐条有变异撞**（无豁免名单段）。

**M17 的实现形态。** spec 的 M17 写作「兜底退回含越界编号的原标注」，字面照做会让越界
编号（如 9）流进分发、`by_phase[p-1]` 越界抛 IndexError —— 那是一次**崩溃**而不是一条
判别器变红，元锁会把这条变异记成空转。改成同一缺陷方向的等价形态：越界编号被**夹到**
末阶段（`min(p, count)`），照样红 U9（越界编号该撤回、不该挂到任何阶段）。

用法：python tests/perf/arc_phase_anchoring_mutations.py   （需测试 PG：docker-compose.test.yml）
"""
from __future__ import annotations

import pathlib
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

PERF_DIR = pathlib.Path(__file__).resolve().parent
ROOT = PERF_DIR.parents[1]
sys.path.insert(0, str(PERF_DIR))
sys.path.insert(0, str(ROOT / "tests"))

import lock_coverage  # noqa: E402  —— 判档与产物写入的唯一一份实现
import mutation_framework as framework  # noqa: E402  —— 执行原语的唯一一份实现

ARTIFACT = PERF_DIR / "arc_phase_anchoring_red_lines.json"

TARGET = "tests/test_phase_anchoring.py"
ANCH, DRAFT, QUOTES = (ROOT / "core" / "phase_anchoring.py", ROOT / "core" / "card_draft.py",
                       ROOT / "core" / "quotes.py")
DIST, ROUTE = ROOT / "core" / "distiller.py", ROOT / "web" / "routers" / "distill.py"
TARGETS = (ANCH, DRAFT, QUOTES, DIST, ROUTE)

_SAVE_CALL = (
    "            result = await text_manager.save_distilled_card(\n"
    "                req.text_id, card, user_id,\n"
    "                embedding_key=emb.key, embedding_region=emb.region,\n"
    "            )\n"
)
_NOT_SEGS = ("    if not segs or not all(s in source_norm for s in segs):\n"
             "        return None\n")

# 规则 3、4 合并后（spec arc-phase-unlocated §3.1：摘录落在哪就挂哪）的锚点
_IN_TAGGED = "                if p in landed:                  # 落进所标阶段：只认所标阶段"
_REHANG = "                    hit.update(landed)"
_PHASE_AT = "        if lo <= pos < hi:"

MUTATIONS = [
    ('M1  位置检查恒通过（摘录落在别处也认所标阶段）', TARGET, [("repl", ANCH, [
        (_IN_TAGGED, "                if True:")])], "RED"),
    ('M2  「落进所标阶段只认所标」失效（命中时也挂全部落点）', TARGET, [("repl", ANCH, [
        (_IN_TAGGED, "                if False:")])], "RED"),
    ('M3  恢复兜底：没有证据时退回模型原标注', TARGET, [("repl", ANCH, [
        ("        final.append(landed_all)\n", "        final.append(landed_all or list(valid))\n")])], "RED"),
    ('M4  不改挂：摘录落在别处时直接去掉', TARGET, [("repl", ANCH, [
        (_REHANG, "                    pass")])], "RED"),
    ('M4b 改挂只取最早落点（状态类应取全部）', TARGET, [("repl", ANCH, [
        (_REHANG, "                    hit.add(landed[0])")])], "RED"),
    ('M5a 锚点查不到时当作全文末尾，照常检查（不跳过）', TARGET, [("repl", ANCH, [
        ('        if loc is None:\n            return [], f"阶段 {i + 1} 锚点核对不上：{anchor}"',
         "        if loc is None:\n"
         "            starts.append(len(source_norm))\n"
         "            continue")])], "RED"),
    ('M5b 去掉锚点「严格递增」检查', TARGET, [("repl", ANCH, [
        ("        if b <= a:", "        if False:")])], "RED"),
    ('M6  锚点正常也整卡跳过', TARGET, [("repl", ANCH, [
        ("    skipped = bool(reason)", "    skipped = True")])], "RED"),
    ('M7  重复摘录要求所有位置都在所标阶段', TARGET, [("repl", ANCH, [
        (_IN_TAGGED, "                if landed == [p]:")])], "RED"),
    ('M8  能定位就算落进所标阶段（不看落点）', TARGET, [("repl", ANCH, [
        (_IN_TAGGED, "                if landed:")])], "RED"),
    ('M9  范围用 <=（锚点位置归前一阶段）', TARGET, [("repl", ANCH, [
        (_PHASE_AT, "        if lo <= pos <= hi:")])], "RED"),
    ('M10 R1（/start 后台任务）调用点传空串作原文', TARGET, [("repl", ROUTE, [
        ("            card = card_from_draft(data, content)",
         '            card = card_from_draft(data, "")')])], "RED"),
    ('M11 阶段 k 的 source_quote 一律取第一段', TARGET, [("repl", ANCH, [
        ("                    own.setdefault(p, quote)", "                    own.setdefault(p, by_phase[p][0])")])], "RED"),
    ('M12 :1128 去掉 to_thread（位置检查回到事件循环线程）', TARGET, [("repl", ROUTE, [
        ("            card = await asyncio.to_thread(card_from_draft, data, content)",
         "            card = card_from_draft(data, content)")])], "RED"),
    ('M13 监测行字段名错（ambiguous → ambig）', TARGET, [("repl", DRAFT, [
        ('"[phase_anchoring] card=%s tags=%d dropped=%d ambiguous=%d "',
         '"[phase_anchoring] card=%s tags=%d dropped=%d ambig=%d "')])], "RED"),
    ('M14 verbatim_in_normalized 不再走 locate_in_normalized（另写一份匹配）', TARGET, [
        ("write", ROOT / "core" / "verbatim_dup.py",
         "import re\n\n"
         "_ELLIPSIS = re.compile(r\"…+\")\n\n\n"
         "def _dup(source_norm, quote):\n"
         "    segs = [s for s in _ELLIPSIS.split(str(quote)) if s]\n"
         "    if not all(s in source_norm for s in segs):\n"
         "        return None\n"
         "    return [m.start() for m in re.finditer(segs[0], source_norm)]\n")], "RED"),
    ('M15 位置取首段而非最长段', TARGET, [("repl", QUOTES, [
        ("    longest = max(segs, key=len)", "    longest = segs[0]")])], "RED"),
    ('M16 定位时要求省略号各段按顺序出现', TARGET, [("repl", QUOTES, [
        (_NOT_SEGS,
         "    parts = [source_norm.find(s) for s in segs]\n"
         "    if not segs or not all(s in source_norm for s in segs) or parts != sorted(parts):\n"
         "        return None\n")])], "RED"),
    ('M17 越界编号被夹到末阶段（连同摘录），而不是撤回（spec 原形的等价形态，见模块说明）', TARGET,
     [("repl", DRAFT, [
         ("        nums = [occ.phase for occ in item.occurrences]\n"
          "        valid = sorted({p for p in nums if 1 <= p <= count})",
          "        for occ in item.occurrences:\n"
          "            occ.phase = min(occ.phase, count)\n"
          "        nums = [occ.phase for occ in item.occurrences]\n"
          "        valid = sorted({p for p in nums if 1 <= p <= count})")])], "RED"),
    ('M18 同阶段多段摘录：任一段核对不上就整阶段不成立', TARGET, [("repl", ANCH, [
        ("                    continue\n                landed = ",
         "                    break\n                landed = ")])], "RED"),
    ('M19a 出现次数判定改 >=（恰好 3 次也不作证据）', TARGET, [("repl", ANCH, [
        ("                if loc is None or len(loc) > MAX_OCCURRENCES:",
         "                if loc is None or len(loc) >= MAX_OCCURRENCES:")])], "RED"),
    ('M19b 删去次数限制', TARGET, [("repl", ANCH, [
        ("                if loc is None or len(loc) > MAX_OCCURRENCES:",
         "                if loc is None:")])], "RED"),
    ('M20 阶段上界整体挪一（含下一阶段首字）', TARGET, [("repl", ANCH, [
        ("    ranges = [(starts[k], starts[k + 1] if k + 1 < n else len(source_norm))",
         "    ranges = [(starts[k], (starts[k + 1] + 1) if k + 1 < n else len(source_norm))")])], "RED"),
    ('M21 阶段上界退一（锚点前一字被排除）', TARGET, [("repl", ANCH, [
        (_PHASE_AT, "        if lo <= pos < hi - 1:")])], "RED"),
    ('M22 位置只取次匹配的第一处（单一命中）', TARGET, [("repl", QUOTES, [
        ("    return [m.start() for m in re.finditer(re.escape(longest), source_norm)]",
         "    return [re.search(re.escape(longest), source_norm).start()]")])], "RED"),
    ('M23 分段查不到时当作位置 0（而不是 None）', TARGET, [("repl", QUOTES, [
        (_NOT_SEGS,
         "    if not segs or not all(s in source_norm for s in segs):\n"
         "        return [0]\n")])], "RED"),
    ('M24 删掉非法编号的 warning', TARGET, [("repl", DRAFT, [
        ("        if count and (len(valid) != len(set(nums)) or not valid):",
         "        if False:")])], "RED"),
    ('M25 核对不上的阶段照挂（没有证据的标注也留下）', TARGET, [("repl", ANCH, [
        ("            targets[p] = sorted(hit)\n",
         "            if not hit:\n"
         "                hit.add(p)\n"
         "                own.setdefault(p, (by_phase.get(p) or [\"\"])[0])\n"
         "            targets[p] = sorted(hit)\n")])], "RED"),
    ('M26 R2（/run_stream）同一张卡落库两次', TARGET, [("repl", ROUTE, [
        (_SAVE_CALL, _SAVE_CALL + _SAVE_CALL)])], "RED"),
    ('M27 /run_stream 的响应码不是 200', TARGET, [("repl", ROUTE, [
        ('media_type="text/event-stream",',
         'media_type="text/event-stream", status_code=202,')])], "RED"),
    ('M28 distiller 多出一处 card_from_draft 调用（调用点计数变 4）', TARGET, [("append", DIST,
        "\n\ndef _mutation_extra_call(data=None, text=None):\n"
        "    return card_from_draft(data, text)\n")], "RED"),
    ('M29 某一调用点只传一个参数（丢掉原文）', TARGET, [("repl", DIST, [
        ("            card = card_from_draft(data, text)",
         "            card = card_from_draft(data)")])], "RED"),
    ('M30 草稿做法多出一个 phases 字段（存卡形态被草稿字段污染）', TARGET, [("repl", DRAFT, [
        ("    \"\"\"情境→行为：此人遇到某类情境时的具体做法；occurrences 是它在各阶段的原文摘录。\"\"\"\n"
         "    occurrences: list[DraftOccurrence] = []\n",
         "    \"\"\"情境→行为：此人遇到某类情境时的具体做法；occurrences 是它在各阶段的原文摘录。\"\"\"\n"
         "    occurrences: list[DraftOccurrence] = []\n    phases: list[int] = []\n")])], "RED"),
    # ── 审计 §9.5 补（A1–A3），两个方向 ──
    ('M31 阶段 1 以外的空锚点退回取全文末尾（不跳过整卡）', TARGET, [("repl", ANCH, [
        ('            return [], f"阶段 {i + 1} 锚点为空"',
         "            starts.append(len(source_norm))\n            continue")])], "RED"),
    ('M32 阶段 1 的空锚点也整卡跳过', TARGET, [("repl", ANCH, [
        ("            if i == 0:\n                starts.append(0)\n                continue\n", "")])], "RED"),
    ('M33 dropped 按摘录计（核对不上的摘录也记一次去掉）', TARGET, [("repl", ANCH, [
        ("                    unverified += 1              # 查不到 / 出现太多 → 不作位置证据\n",
         "                    unverified += 1              # 查不到 / 出现太多 → 不作位置证据\n"
         "                    dropped += 1\n")])], "RED"),
    ('M34 tags 按摘录计', TARGET, [("repl", ANCH, [
        ("            tags += 1\n            hit: set[int] = set()\n", "            hit: set[int] = set()\n"),
        ("            for quote in by_phase.get(p, []):    # 每段摘录各是一份证据\n",
         "            for quote in by_phase.get(p, []):    # 每段摘录各是一份证据\n"
         "                tags += 1\n")])], "RED"),
    ('M35 删去「去掉标注」的 warning', TARGET, [("repl", ANCH, [
        ('                logger.warning("去掉标注：%s %r 在阶段 %d 没有能定位的摘录",\n'
         "                               kind, label(item), p)\n",
         "                pass\n")])], "RED"),
    ('M36 整卡跳过时不计 tags', TARGET, [("repl", ANCH, [
        ("            tags += len(valid)                   # 分母照常计（监测比例要用）\n", "")])], "RED"),
    # ── arc-phase-unlocated §3.1 / §3.7 新增 ──
    ('M37 删去「改挂」的 warning', TARGET, [("repl", ANCH, [
        ("            elif targets[p] != [p]:\n", "            elif False:\n")])], "RED"),
    ('M38 rehung 按标注计（一条条目改挂两个标注记两次）', TARGET, [("repl", ANCH, [
        ("        elif any(ts and ts != [p] for p, ts in targets.items()):\n            rehung += 1\n",
         "        else:\n            rehung += sum(1 for p, ts in targets.items() if ts and ts != [p])\n")])], "RED"),
    ('M39 改挂过来的摘录压过所标阶段自己的摘录', TARGET, [("repl", ANCH, [
        ("        phase_quotes.append({t: own.get(t) or moved[t] for t in landed_all})",
         "        phase_quotes.append({t: moved.get(t) or own[t] for t in landed_all})")])], "RED"),
]

GROUPS = {"M": MUTATIONS}


def _baseline_gate() -> dict[str, str]:
    """先验基线：覆盖域的靶子锁绿才开跑 —— 否则「变异后红」说不清红源。"""
    summary, _, _, _ = framework._run(TARGET)
    print(f"  基线 {TARGET}  {summary}")
    cause = lock_coverage.baseline_verdict(summary)
    return {TARGET: cause} if cause else {}


def main() -> int:
    print("== 先验基线 ==")
    bad = _baseline_gate()
    if bad:
        return lock_coverage.refuse_on_baseline(bad)
    return framework.run_matrix(
        MUTATIONS, domain=lock_coverage.domain_of(GROUPS), targets=TARGETS, artifact=ARTIFACT,
        driver_rel="tests/perf/arc_phase_anchoring_mutations.py")


if __name__ == "__main__":
    sys.exit(main())
