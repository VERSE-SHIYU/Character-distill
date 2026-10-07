"""变异预跑（spec `arc-phase-unlocated.md` §7 对账表 X1–X37 + 乐观锁 L1–L7 + 前端 F1–F13）。

逐条把改后代码改坏一处（放宽、过严两个方向），对应测试必须红；跑完逐字节还原。

用法：在仓库根目录
    .venv/Scripts/python.exe docs/specs/artifacts/arc_phase_unlocated_mutations.py [段号]

段号 1–4 只跑该段的变异（spec §4 分段，每段 PR 跑自己那份）；不给段号 = 全部（段 4 合并后）。

**执行框架与判档不在本文件里**：改文件 / 跑 pytest / 还原用 `tests/perf/mutation_framework.py`，
判档与基线门用 `tests/lock_coverage.py` —— 与 `arc_phase_select_mutations.py` 同一处置。

它**不**登记进覆盖闭合元锁：这是一份 spec 的一次性对账，不是常设守卫（返工经验 #10）。
位置规则本身的变异在常设驱动 `tests/perf/arc_phase_anchoring_mutations.py` 里。

退出码：0 = 全部红；1 = 有存活；2 = 基线红（拒跑）。
"""
from __future__ import annotations

import pathlib
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests" / "perf"))
sys.path.insert(0, str(ROOT / "tests"))

import mutation_framework as framework  # noqa: E402

LAYERS = ROOT / "core" / "card_layers.py"
SCHEMA = ROOT / "core" / "schema.py"
DRAFT = ROOT / "core" / "card_draft.py"
ANCH = ROOT / "core" / "phase_anchoring.py"
VIEW = ROOT / "core" / "arc_view.py"
QUOTES = ROOT / "core" / "card_quotes.py"
CTX = ROOT / "core" / "context_engine.py"
WALK = ROOT / "core" / "moderation" / "card_text.py"
GUARD = ROOT / "core" / "moderation" / "card_guard.py"
MOVE = ROOT / "core" / "unlocated.py"
RDIST = ROOT / "web" / "routers" / "distill.py"
FE = ROOT / "web" / "frontend"
EDIT = FE / "src" / "components" / "EditCardModal.jsx"
ARCL = FE / "src" / "components" / "common" / "ArcList.jsx"
UNLJ = FE / "src" / "components" / "common" / "UnlocatedList.jsx"
CHAR = FE / "src" / "components" / "CharCard.jsx"
STORE = FE / "src" / "store" / "useAppStore.js"
TXTP = FE / "src" / "components" / "TextPanel.jsx"
OUT = ROOT / "core" / "card_out.py"
PGS = ROOT / "storage" / "postgres_store.py"
TARGETS = (LAYERS, SCHEMA, DRAFT, ANCH, VIEW, QUOTES, CTX, WALK, GUARD, MOVE, RDIST,
           EDIT, ARCL, UNLJ, CHAR, STORE, TXTP, OUT, PGS)
JS = ("src/components/__tests__/ArcListNested.test.jsx",          # 段 1
      "src/components/__tests__/EditCardModalArcKeep.test.jsx",   # 段 2
      "src/store/applyServerCard.test.js",                        # 段 2
      "src/components/__tests__/UnlocatedList.test.jsx",          # 段 4
      "src/components/__tests__/CharCardUnlocated.test.jsx",      # 段 4
      "src/store/moveUnlocated.test.js",                          # 段 4
      "src/components/__tests__/TextPanelEditSave.test.jsx")      # 段 4（§13）

UNL = "tests/test_arc_phase_unlocated.py"
GOAL = "tests/test_arc_phase_unlocated_goal.py"
MV = "tests/test_arc_phase_unlocated_move.py"
LOCK = "tests/test_card_optimistic_lock.py"
PGT = "tests/test_postgres_store.py"


def _u(name: str) -> str:
    return f"{UNL}::{name}"


def _l(name: str) -> str:
    return f"{LOCK}::{name}"


def _pg(name: str) -> str:
    return f"{PGT}::{name}"


def _g(name: str) -> str:
    return f"{GOAL}::{name}"


def _m(name: str) -> str:
    return f"{MV}::{name}"


_run_js = framework.vitest(*JS)   # 前端靶子：跑法在框架里（补充 20 的根因修法）


# 「回到 #116 的扁平存法」：写成扁平键 + 不转换 + 不拒带点键名（三处一起，模拟根因复发）
_FLAT = [
    ("repl", DRAFT, [("            set_path(overlays.setdefault(idx, {}), path,\n"
                      "                     vals if spec.kind == \"list\" else vals[0])",
                      "            overlays.setdefault(idx, {})[path] = (\n"
                      "                     vals if spec.kind == \"list\" else vals[0])")]),
    ("repl", LAYERS, [("    out: dict = {}\n", "    return dict(overlay)\n    out: dict = {}\n"),
                      ("        if \".\" in key:\n            raise", "        if False:\n            raise")]),
]

_BEH_PATHS = "                 for c in (\"character_arc.phases[].behaviors\", f\"{_UNLOCATED}.behaviors\"))"

# (编号, 靶子测试, [(动作, 文件, 载荷)], 期望)
MUTANTS = [
    # ── 段 1：overlay 与卡片同形 ──
    ("X1  放宽 旧扁平键加载时不转换", _u("test_n1_legacy_flat_key_is_nested_on_load"),
     [("repl", LAYERS, [("    out: dict = {}\n", "    return dict(overlay)\n    out: dict = {}\n")])], "RED"),
    ("X2  过严 嵌套值被扁平重复覆盖", _u("test_n2_nested_value_wins_over_flat_duplicate"),
     [("repl", LAYERS, [("        if \".\" in key and pydash.get(out, key) is None:",
                         "        if \".\" in key:")])], "RED"),
    ("X3  放宽 校验不查叶子是否登记", _u("test_n3_overlay_rejects_unregistered_nested_leaf"),
     [("repl", LAYERS, [("        if spec is None or spec.layer not in layers:",
                         "        if False:")])], "RED"),
    ("X4  放宽 校验不查列表形态", _u("test_n4_overlay_rejects_wrong_kind_on_nested_leaf"),
     [("repl", LAYERS, [("        if (lists_only or spec.kind == \"list\") and not isinstance(val, list):",
                         "        if False:")])], "RED"),
    ("X5  放宽 校验不拒带点键名", _u("test_n4b_unlocated_overlay_uses_the_same_validator_and_rejects_dotted_key"),
     [("repl", LAYERS, [("        if \".\" in key:\n            raise", "        if False:\n            raise")])], "RED"),
    ("X6  放宽 未定位区不走同一个校验器", _u("test_n4b_unlocated_overlay_uses_the_same_validator_and_rejects_dotted_key"),
     [("repl", SCHEMA, [("        check_overlay(self.overlay, layers=(\"state\",), lists_only=True, where=\"未定位区 overlay\")",
                         "        pass")])], "RED"),
    ("X7  根因复发（回到扁平存法）→ 根因锁", _u("test_n5_no_dotted_key_anywhere_in_card_or_projection"), _FLAT, "RED"),
    ("X7d 根因复发 → 目标检查 G4", _g("test_g4_guard_reaches_every_text_leaf"), _FLAT, "RED"),
    # G1/G2 测的是读侧（嵌套 overlay 上守卫、核对都走得到），写侧复发由 X7 / X7d 管
    ("X7b 读侧 守卫寻址不按点分层", _u("test_g1_guard_neutralizes_each_dotted_overlay_leaf"),
     [("repl", GUARD, [("_SEG_RE = re.compile(r\"([^.\\[\\]]+)|\\[(\\d+)\\]\")",
                        "_SEG_RE = re.compile(r\"([^\\[\\]]+)|\\[(\\d+)\\]\")")])], "RED"),
    ("X7c 读侧 带点字段不派生 overlay 核对路径", _u("test_g2_quote_check_in_overlay_matches_top_level"),
     [("repl", QUOTES, [("        out.append(f\"{_PHASE_OVERLAY}.{base}\" + (\"[]\" if spec.kind == \"list\" else \"\"))",
                         "        out += [] if \".\" in base else [f\"{_PHASE_OVERLAY}.{base}\" + (\"[]\" if spec.kind == \"list\" else \"\")]")])], "RED"),
    ("X8  放宽 阶段 overlay 不进引文核对", _u("test_g3_fabricated_catchphrase_in_phase_overlay_is_retracted"),
     [("repl", QUOTES, [("        out.append(f\"{_PHASE_OVERLAY}.{base}\" + (\"[]\" if spec.kind == \"list\" else \"\"))",
                         "        pass")])], "RED"),
    # ── 段 3：没有位置证据的条目 ──
    ("X9  放宽 恢复兜底（无证据退回原标注）→ 目标检查 G1", _g("test_g1_every_phase_placement_has_positional_evidence"),
     [("repl", ANCH, [("        final.append(landed_all)\n", "        final.append(landed_all or list(valid))\n")])], "RED"),
    ("X10 过严 未定位做法被丢 → 目标检查 G2", _g("test_g2_nothing_with_a_valid_tag_is_lost"),
     [("repl", DRAFT, [("            for i in b_loose],", "            for i in []],")])], "RED"),
    ("X11 放宽 未定位区进投影 → 目标检查 G3", _g("test_g3_no_projection_carries_unlocated_items"),
     [("repl", VIEW, [("    proj.character_arc.unlocated = UnlocatedItems()", "    pass")])], "RED"),
    ("X11b 同上 → P1", _u("test_p1_projection_never_carries_unlocated"),
     [("repl", VIEW, [("    proj.character_arc.unlocated = UnlocatedItems()", "    pass")])], "RED"),
    ("X12 不改挂 → 目标检查夹具自检 G0", _g("test_g0_fixture_really_exercises_rehang_and_unlocated"),
     [("repl", ANCH, [("                    hit.update(landed)", "                    pass")])], "RED"),
    ("X13 过严 状态类无证据被丢（不进未定位区）", _u("test_u1_state_behavior_without_evidence_goes_to_unlocated"),
     [("repl", DRAFT, [("            for i in b_loose],", "            for i in []],")])], "RED"),
    ("X14 过严 经历类无证据被丢（不挂最后）", _u("test_u4_experience_memory_without_evidence_hangs_on_last_phase"),
     [("repl", DRAFT, [("        if layer == \"experience\":\n            final_phases[i] = experience_fallback(count)",
                        "        if False:\n            final_phases[i] = experience_fallback(count)")])], "RED"),
    ("X15 放宽 经历类无证据挂阶段 1（泄露后续）", _u("test_u4_experience_memory_without_evidence_hangs_on_last_phase"),
     [("repl", DRAFT, [("    return [count]\n", "    return [1]\n")])], "RED"),
    ("X16 放宽 经历类改挂后挂全部落点（应只挂最早）", _u("test_u9_experience_rehung_to_several_landings_hangs_only_the_earliest"),
     [("repl", DRAFT, [("        return [f if (count and len(f) == count) else f[:1] for f in final_phases]",
                        "        return final_phases")])], "RED"),
    ("X17 未定位单值存成单值（应一律列表）", _u("test_u3_state_scalar_dotted_without_evidence_is_kept_as_list_nested"),
     [("repl", DRAFT, [("            set_path(loose_overlay, path, [rows[i].value for i in loose])",
                        "            set_path(loose_overlay, path, rows[loose[0]].value)")])], "RED"),
    ("X18 过严 整卡跳过也进未定位区", _u("test_u7_skipped_card_keeps_model_tags_for_items_without_evidence"),
     [("repl", ANCH, [("            tag_targets.append({p: [p] for p in valid})\n            continue",
                       "            tag_targets.append({p: [p] for p in valid})\n            unlocated.append(i)\n"
                       "            continue")])], "RED"),
    ("X19 监测 分类计数漏关系", _u("test_u8_monitor_counts_every_category"),
     [("repl", DRAFT, [("    kinds[\"关系\"] = [r_rehung, len(loose_attitudes), r_to_last]",
                        "    kinds[\"关系\"] = [0, 0, 0]")])], "RED"),
    # ── 段 3：关系态度 ──
    ("X20 过严 撞车时原标注不优先", _u("test_r2_collision_original_tag_wins_loser_goes_to_unlocated_with_note"),
     [("repl", DRAFT, [("next((j for j in cands if flat[j][1].phase == t), cands[0])", "cands[0]")])], "RED"),
    ("X21 过严 撞车输掉的态度被丢", _u("test_r2_collision_original_tag_wins_loser_goes_to_unlocated_with_note"),
     [("repl", DRAFT, [("            if j not in won:", "            if False:")])], "RED"),
    ("X22 放宽 在别处赢了的也进未定位区", _u("test_r3_attitude_that_wins_elsewhere_is_not_unlocated"),
     [("repl", DRAFT, [("            if j not in won:", "            if True:")])], "RED"),
    ("X23 放宽 无态度的关系挂阶段 1（泄露）", _u("test_r5_empty_attitude_relationship_absent_before_last_and_rendered_without_colon"),
     [("repl", SCHEMA, [("[{\"phase\": count, \"attitude\": \"\", \"note\": \"\"}]",
                         "[{\"phase\": 1, \"attitude\": \"\", \"note\": \"\"}]")])], "RED"),
    ("X24 放宽 无态度的关系沿用模型态度（未核对）", _u("test_r4_relationship_without_any_located_attitude_hangs_last_with_empty_attitude"),
     [("repl", DRAFT, [("            row[\"phase_attitudes\"] = placeholder_phase_attitudes(experience_fallback(count)[0])",
                        "            row[\"phase_attitudes\"] = [{\"phase\": count, \"attitude\": r.attitudes[-1].attitude, \"note\": \"\"}]")])], "RED"),
    ("X34 放宽 同一关系的态度共用位置证据（审计发现 1）", _u("test_r7_same_phase_tags_each_attitude_uses_only_its_own_evidence"),
     [("repl", DRAFT, [("            cands = [j for j in mine if j in top or j in slots[t - 1]]",
                        "            cands = [j for j in mine if j in top or any(k in slots[t - 1] for k in mine)]")])], "RED"),
    ("X34b 同上 → 目标检查 G1（夹具「孩子们」）", _g("test_g1_every_phase_placement_has_positional_evidence"),
     [("repl", DRAFT, [("            cands = [j for j in mine if j in top or j in slots[t - 1]]",
                        "            cands = [j for j in mine if j in top or any(k in slots[t - 1] for k in mine)]")])], "RED"),
    ("X37 过严 无阶段的卡状态类进未定位区（spec §3.2 原那一行，审计发现 3）",
     _u("test_u10_card_without_phases_skips_the_check_and_puts_state_on_top"),
     [("repl", DRAFT, [("        if count == 0 or len(final) == count:\n            top.append(i)",
                        "        if count == 0 and layer == \"state\":\n            loose.append(i)\n"
                        "        elif count == 0 or len(final) == count:\n            top.append(i)")])], "RED"),
    ("X25 渲染 空态度仍带冒号", _u("test_r5_empty_attitude_relationship_absent_before_last_and_rendered_without_colon"),
     [("repl", CTX, [("(f\"：{r.attitude}\" if r.attitude else \"\")", "f\"：{r.attitude}\"")])], "RED"),
    ("X26 无阶段的卡取第一条态度", _u("test_r6_card_without_phases_keeps_latest_attitude"),
     [("repl", DRAFT, [("            row[\"attitude\"] = r.attitudes[-1].attitude",
                        "            row[\"attitude\"] = r.attitudes[0].attitude")])], "RED"),
    # ── 段 3：未定位区照样被核对、被审核 ──
    ("X27 放宽 未定位做法不进引文核对", _u("test_q1_quote_in_unlocated_behavior_is_stripped"),
     [("repl", QUOTES, [(_BEH_PATHS, "                 for c in (\"character_arc.phases[].behaviors\",))")])], "RED"),
    ("X27b 同上 → 摘录逐字核对", _u("test_q2_unverifiable_source_quote_in_unlocated_behavior_is_cleared"),
     [("repl", QUOTES, [(_BEH_PATHS, "                 for c in (\"character_arc.phases[].behaviors\",))")])], "RED"),
    ("X28 放宽 未定位 overlay 不进引文核对", _u("test_q3_fabricated_catchphrase_in_unlocated_overlay_is_removed"),
     [("repl", QUOTES, [("        if spec.layer == \"state\":\n            out.append(",
                         "        if False:\n            out.append(")])], "RED"),
    ("X29 放宽 未定位态度不进引文核对", _u("test_q4_quote_in_unlocated_attitude_is_stripped"),
     [("repl", QUOTES, [("    f\"{_UNLOCATED_ATT}[].attitude\",\n", "")])], "RED"),
    ("X30 放宽 审核遍历跳过未定位区", _u("test_q5_moderation_walker_sees_unlocated_leaves"),
     [("repl", WALK, [("        for k, v in node.items():",
                       "        for k, v in ((k, v) for k, v in node.items() if k != \"unlocated\"):")])], "RED"),
    # ── 段 4：挪动 ──
    ("X31 过严 单值挪入不交换（原值丢）", _m("test_m4_scalar_value_into_occupied_slot_swaps_back"),
     [("repl", MOVE, [("            if current:                              # 交换", "            if False:  # 交换")])], "RED"),
    ("X32 放宽 挪入态度不去占位（投影被空态度盖住）", _m("test_m5_attitude_replaces_empty_placeholder_and_shows_in_projection"),
     [("repl", MOVE, [("        kept = [pa for pa in rel[\"phase_attitudes\"] if not is_placeholder_attitude(pa)]",
                       "        kept = list(rel[\"phase_attitudes\"])")])], "RED"),
    ("X33 接口返回不走 out_card", _m("test_route_returns_the_card_through_out_card"),
     [("repl", RDIST, [("    return {\"ok\": True, \"card\": out_card(result)}   # B4：出卡只经 out_card（现算 selectable）",
                        "    return {\"ok\": True, \"card\": result}")])], "RED"),
    # ── 乐观锁（§13）──
    ("L1  放宽 PATCH 不核对 revision（旧版覆盖）", _l("test_patch_with_stale_revision_is_409_and_card_untouched"),
     [("repl", RDIST, [("    if card_revision(record[\"card_json\"]) != req.revision:\n        raise HTTPException(409, CARD_CONFLICT)\n    result = await storage.update_card(card_id, validated",
                        "    result = await storage.update_card(card_id, validated")])], "RED"),
    ("L2  放宽 挪动不核对 revision（按漂移的序号挪错）", _l("test_move_with_stale_revision_is_409_and_card_untouched"),
     [("repl", RDIST, [("    if card_revision(record[\"card_json\"]) != req.revision:\n        raise HTTPException(409, CARD_CONFLICT)\n    card = CharacterCard",
                        "    card = CharacterCard")])], "RED"),
    ("L3  放宽 存储不比较就写（核对与写入之间的竞争）", _pg("TestPgCardCompareAndSwap::test_stale_expected_writes_nothing"),
     [("repl", PGS, [("WHERE id = $2 AND card_json = $4\",\n                    json.dumps(card_json, ensure_ascii=False), card_id, now, expected,",
                      "WHERE id = $2\",\n                    json.dumps(card_json, ensure_ascii=False), card_id, now,")])], "RED"),
    ("L4  放宽 PATCH 存储比较失败当成功（竞争时吞掉）", _l("test_patch_racing_another_write_is_409_and_keeps_the_other_write"),
     [("repl", RDIST, [("    result = await storage.update_card(card_id, validated.model_dump(), expected=record[\"card_json\"])\n    if result is None:\n        raise HTTPException(409, CARD_CONFLICT)",
                        "    result = await storage.update_card(card_id, validated.model_dump(), expected=record[\"card_json\"]) or record")])], "RED"),
    ("L5  唤醒语拿内存里的旧卡整卡写回（覆盖用户编辑）", _l("test_awakening_lands_on_the_latest_card_and_keeps_the_users_edit"),
     [("repl", RDIST, [("    card = CharacterCard.model_validate_json(rec[\"card_json\"])\n    card.awakening_message = awakening",
                        "    card = CharacterCard.model_validate({\"name\": \"x\"})\n    card.awakening_message = awakening")])], "RED"),
    ("L6  过严 PATCH 一律 409（合法保存也被拒）", _l("test_patch_with_current_revision_saves_and_returns_the_new_revision"),
     [("repl", RDIST, [("    if card_revision(record[\"card_json\"]) != req.revision:\n        raise HTTPException(409, CARD_CONFLICT)\n    result = await storage.update_card(card_id, validated",
                        "    raise HTTPException(409, CARD_CONFLICT)\n    result = await storage.update_card(card_id, validated")])], "RED"),
    ("L7  revision 按补过 selectable 的串算（出卡与核对口径不一）", _l("test_out_card_revision_is_the_raw_stored_text_and_follows_content"),
     [("repl", OUT, [("    row = {**row, \"revision\": card_revision(raw)}\n", "")]),
      ("repl", OUT, [("        return {**row, \"card_json\": json.dumps(card, ensure_ascii=False)}",
                      "        return {**row, \"card_json\": json.dumps(card, ensure_ascii=False), \"revision\": card_revision(json.dumps(card, ensure_ascii=False))}")])], "RED"),
    # ── 前端（vitest）──
    ("F1  编辑保存整体替换 character_arc（丢指纹 / 未定位区）", _run_js,
     [("repl", EDIT, [("        ...data.character_arc,\n", "")])], "RED"),
    ("F2  ArcList 不往下走嵌套（只认扁平键）", _run_js,
     [("repl", ARCL, [("    v && typeof v === 'object' && !Array.isArray(v)", "    false")])], "RED"),
    ("F3  updateCard 写回本地提交的卡（过期 selectable）", _run_js,
     [("repl", STORE, [("    if (data.ok) get()._applyServerCard(cardId, data.card)",
                        "    if (data.ok) get()._applyServerCard(cardId, { card_json: cardJson })")])], "RED"),
    ("F4  moveUnlocated 打错接口", _run_js,
     [("repl", STORE, [("/unlocated/move`", "/move`")])], "RED"),
    ("F5  moveUnlocated 失败也写回", _run_js,
     [("repl", STORE, [("    if (!res.ok || !data.ok) throw new Error(data.detail || '挪动失败，请刷新后重试')\n", "")])], "RED"),
    ("F6  态度默认阶段不用原标注", _run_js,
     [("repl", UNLJ, [("      initial: a.phase >= 1 && a.phase <= last ? a.phase : last,", "      initial: last,")])], "RED"),
    ("F7  挪入传错路径", _run_js,
     [("repl", UNLJ, [("      move: (p) => onMove('overlay', i, p, path), initial: last,",
                       "      move: (p) => onMove('overlay', i, p, ''), initial: last,")])], "RED"),
    ("F8  只读账号也显示未定位区", _run_js,
     [("repl", CHAR, [("        {canWrite && hasUnlocated(data.character_arc) && (",
                       "        {hasUnlocated(data.character_arc) && (")])], "RED"),
    ("F9  挪入没接到 store", _run_js,
     [("repl", CHAR, [("      await moveUnlocated(card.id || card.card_id, { section, index, phase, path, revision: card.revision })",
                       "      await Promise.resolve()")])], "RED"),
    ("F10 编辑保存不带 revision", _run_js,
     [("repl", CHAR, [("    await updateCard(card.id || card.card_id, cardJson, card.revision)",
                       "    await updateCard(card.id || card.card_id, cardJson)")])], "RED"),
    ("F11 写回后 store 留着旧 revision（连续第二次保存必 409）", _run_js,
     [("repl", STORE, [("          ? { ...s.currentCard, ...obj, card_json: obj, revision: row.revision }",
                        "          ? { ...s.currentCard, ...obj, card_json: obj }")])], "RED"),
    ("F12 角色管理编辑改回自己发 PUT（必 405）", _run_js,
     [("repl", TXTP, [("            await updateCard(editCard.id || editCard.card_id, cardJson, editCard.revision)",
                       "            await fetchWithTimeout(`/api/distill/card/${editCard.id}`, { method: 'PUT', body: JSON.stringify({ card_json: JSON.stringify(cardJson) }) })")])], "RED"),
    ("F13 挪动不带 revision", _run_js,
     [("repl", CHAR, [("{ section, index, phase, path, revision: card.revision })",
                       "{ section, index, phase, path })")])], "RED"),
]


# spec §4：每段对账哪些变异（编号前缀）
SEGMENTS = {
    "1": ("X1 ", "X2 ", "X3 ", "X4 ", "X7 ", "X7b", "X7c", "X8 ", "F2 "),
    "2": ("F1 ", "F3 "),
    "3": ("X5 ", "X6 ", "X7d", "X9 ", "X10", "X11", "X12", "X13", "X14", "X15", "X16", "X17",
          "X18", "X19", "X20", "X21", "X22", "X23", "X24", "X25", "X26", "X27", "X28", "X29", "X30",
          "X34", "X37"),
    "4": ("X31", "X32", "X33", "L", "F4 ", "F5 ", "F6 ", "F7 ", "F8 ", "F9 ", "F10", "F11", "F12", "F13"),
}


def main() -> int:
    seg = sys.argv[1] if len(sys.argv) > 1 else None
    mutants = [m for m in MUTANTS if seg is None or m[0].startswith(SEGMENTS[seg])]
    return framework.run_oneoff(mutants, targets=TARGETS, gates=[*(lock for lock in (UNL, GOAL, MV) if (ROOT / lock).exists()), _run_js])


if __name__ == "__main__":
    sys.exit(main())
