"""审计补想的变异（spec `arc-phase-unlocated.md` 末「补充」12–14 的复现脚本）。

**来历**：独立审计第 8 步「自己补想漏测的变异」—— 放宽（把锁改弱）、过严（把正常路径改拒）
两个方向各补若干条，专挑 §13 乐观锁那一面。**期望红的红了 = 这条性质已被现有测试钉住；
没红的 = 一条发现**（写进 spec「补充」）。首跑 13 条里 A2、A4、B2 三条存活（补充 12–14）；
补上用例后三条都红，并加了 A2b（A2 的另一个调用点）与 B5（A2 的另一侧），现为 15 条。

**这份不是常设守卫**：与 `arc_phase_unlocated_mutations.py` 同处置，不登记进覆盖闭合元锁
（`tests/perf/*_mutations.py` 才登记）。执行框架、还原、判档全走
`tests/perf/mutation_framework.py` 与 `tests/lock_coverage.py`。

用法（仓库根）：
    .venv/Scripts/python.exe docs/specs/artifacts/arc_phase_unlocated_audit_mutations.py

退出码：0 = 全部符合预期（期望红的红、期望绿的绿）；1 = 有落差；2 = 基线红（拒跑）。
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

OUT = ROOT / "core" / "card_out.py"
RDIST = ROOT / "web" / "routers" / "distill.py"
FE = ROOT / "web" / "frontend"
STORE = FE / "src" / "store" / "useAppStore.js"
CHAR = FE / "src" / "components" / "CharCard.jsx"
UNLJ = FE / "src" / "components" / "common" / "UnlocatedList.jsx"
EDIT = FE / "src" / "components" / "EditCardModal.jsx"
TEXTP = FE / "src" / "components" / "TextPanel.jsx"
TARGETS = (OUT, RDIST, STORE, CHAR, UNLJ, EDIT, TEXTP)

JS = ("src/store/applyServerCard.test.js",
      "src/store/moveUnlocated.test.js",
      "src/components/__tests__/UnlocatedList.test.jsx",
      "src/components/__tests__/CharCardUnlocated.test.jsx",
      "src/components/__tests__/EditCardModalArcKeep.test.jsx",
      "src/components/__tests__/TextPanelEditSave.test.jsx")

LOCK = "tests/test_card_optimistic_lock.py"
MV = "tests/test_arc_phase_unlocated_move.py"
READ = "tests/test_arc_phase_fields_readers.py"


_run_js = framework.vitest(*JS)   # 前端靶子：跑法在框架里（补充 20 的根因修法）


# (编号, 靶子, [(动作, 文件, 载荷)], 期望)
MUTANTS = [
    # ── 放宽：把乐观锁 / 写回改弱 ──────────────────────────────────────
    ("A1  放宽 写回只更新 currentCard，不动 cards（卡片列表留着旧版）", _run_js,
     [("repl", STORE, [(
         "            ? { ...c, card_json: typeof c.card_json === 'string' ? JSON.stringify(obj) : obj, revision: row.revision }\n"
         "            : c",
         "            ? c\n            : c")])], "RED"),
    ("A2  放宽 编辑保存吞掉失败、照常关弹窗（409 时丢用户改动）", _run_js,
     [("repl", CHAR, [(
         "    await updateCard(card.id || card.card_id, cardJson, card.revision)\n"
         "    setShowEditModal(false)",
         "    try { await updateCard(card.id || card.card_id, cardJson, card.revision) } catch { /* 吞 */ }\n"
         "    setShowEditModal(false)")])], "RED"),
    ("A2b 放宽 角色管理的编辑保存吞掉失败、照常关弹窗（A2 的另一个调用点，补充 12 处置时加）", _run_js,
     [("repl", TEXTP, [(
         "            await updateCard(editCard.id || editCard.card_id, cardJson, editCard.revision)\n"
         "            setEditCard(null)",
         "            try { await updateCard(editCard.id || editCard.card_id, cardJson, editCard.revision) } catch { /* 吞 */ }\n"
         "            setEditCard(null)")])], "RED"),
    ("A3  放宽 唤醒语回写在比较失败时强制写（盖掉别人的改动）", f"{LOCK}::test_awakening_racing_another_write_writes_nothing_and_warns",
     [("repl", RDIST, [(
         "        logger.warning(\"[distill] awakening not persisted: card %s changed meanwhile\", card_id)\n"
         "        return\n",
         "        logger.warning(\"[distill] awakening not persisted: card %s changed meanwhile\", card_id)\n"
         "        fresh = await storage.get_card_owned(card_id, user_id)\n"
         "        if fresh:\n"
         "            await storage.update_card(card_id, card.model_dump(), expected=fresh[\"card_json\"])\n"
         "        return\n")])], "RED"),
    # 靶子原是 test_out_card_revision_…（两边同一个函数，自指，打不红 = 补充 13）；改指补上的那条。
    ("A4  放宽 revision 用截断的指纹（碰撞面扩大）", f"{LOCK}::test_card_revision_is_the_full_fingerprint_of_the_stored_text",
     [("repl", OUT, [(
         "    return content_fingerprint(raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False))",
         "    return content_fingerprint(raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False))[:8]")])], "RED"),
    ("A5  放宽 编辑保存去掉路由级 revision 预检（只剩存储层比较）", f"{LOCK}::test_patch_with_stale_revision_is_409_and_card_untouched",
     [("repl", RDIST, [(
         "    if card_revision(record[\"card_json\"]) != req.revision:\n"
         "        raise HTTPException(409, CARD_CONFLICT)\n"
         "    result = await storage.update_card(card_id, validated.model_dump(), expected=record[\"card_json\"])",
         "    result = await storage.update_card(card_id, validated.model_dump(), expected=record[\"card_json\"])")])], "RED"),
    ("A6  放宽 updateCard 失败写全局 error（旧行为，弹窗外再报一遍）", _run_js,
     [("repl", STORE, [(
         "  updateCard: async (cardId, cardJson, revision) => {\n"
         "    const res = await fetchWithTimeout(",
         "  updateCard: async (cardId, cardJson, revision) => {\n"
         "    try {\n"
         "    const res = await fetchWithTimeout(")]),
      ("repl", STORE, [(
         "    if (data.ok) get()._applyServerCard(cardId, data.card)\n"
         "    return data\n  },",
         "    if (data.ok) get()._applyServerCard(cardId, data.card)\n"
         "    return data\n"
         "    } catch (err) { set({ error: err.message }); throw err }\n  },")])], "RED"),
    ("A7  放宽 写回不更新 revision（连续两次保存第二次必 409）", _run_js,
     [("repl", STORE, [(
         "            ? { ...c, card_json: typeof c.card_json === 'string' ? JSON.stringify(obj) : obj, revision: row.revision }",
         "            ? { ...c, card_json: typeof c.card_json === 'string' ? JSON.stringify(obj) : obj }")]),
      ("repl", STORE, [(
         "          ? { ...s.currentCard, ...obj, card_json: obj, revision: row.revision }",
         "          ? { ...s.currentCard, ...obj, card_json: obj }")])], "RED"),
    ("A8  放宽 revision 不按存储原文、按重新序列化的对象算（与卡里的比对不上）",
     f"{LOCK}::test_out_card_revision_is_the_raw_stored_text_and_follows_content",
     [("repl", OUT, [(
         "    row = {**row, \"revision\": card_revision(raw)}",
         "    row = {**row, \"revision\": card_revision(json.loads(raw) if isinstance(raw, str) else raw)}")])], "RED"),
    ("A9  放宽 挪动接口去掉路由级 revision 预检（只剩存储层比较）",
     f"{LOCK}::test_move_with_stale_revision_is_409_and_card_untouched",
     [("repl", RDIST, [(
         "    if card_revision(record[\"card_json\"]) != req.revision:\n"
         "        raise HTTPException(409, CARD_CONFLICT)\n"
         "    card = CharacterCard.model_validate_json(record[\"card_json\"])",
         "    card = CharacterCard.model_validate_json(record[\"card_json\"])")])], "RED"),
    # ── 过严：把正常路径改成拒绝 / 判据拓宽 ───────────────────────────
    ("B1  过严 挪动参数错当成版本冲突（400 → 409）", f"{MV}::test_route_stale_index_is_400",
     [("repl", RDIST, [(
         "        raise HTTPException(400, \"这一条已经不在未定位区，请刷新后重试\") from exc",
         "        raise HTTPException(409, CARD_CONFLICT) from exc")])], "RED"),
    ("B2  过严 未定位区只认做法（overlay / 态度不算条目 → 整块少列）", _run_js,
     [("repl", UNLJ, [(
         "  return (u.behaviors?.length || 0) + (u.attitudes?.length || 0)\n"
         "    + overlayLeaves(u.overlay).reduce((n, [, v]) => n + (v?.length || 0), 0) > 0",
         "  return (u.behaviors?.length || 0) > 0")])], "RED"),
    ("B3  过严 出卡把 selectable 恒定为 false（能选的卡不显示选择框）",
     f"{READ}::test_out_card_recomputes_stale_false_to_true",
     [("repl", OUT, [(
         "        card[\"character_arc\"] = {**arc, \"selectable\": ArcPositions.model_validate(arc).has_positions()}",
         "        card[\"character_arc\"] = {**arc, \"selectable\": False}")])], "RED"),
    ("B5  过严 卡片详情编辑保存成功也不关弹窗（A2 的另一侧，补充 12 处置时加）", _run_js,
     [("repl", CHAR, [(
         "    await updateCard(card.id || card.card_id, cardJson, card.revision)\n"
         "    setShowEditModal(false)",
         "    await updateCard(card.id || card.card_id, cardJson, card.revision)")])], "RED"),
    ("B4  过严 编辑保存整体替换 character_arc（丢指纹 / 未定位区）", _run_js,
     [("repl", EDIT, [(
         "      character_arc: {\n        ...data.character_arc,\n        axis: form.arc_axis.trim(),",
         "      character_arc: {\n        axis: form.arc_axis.trim(),")])], "RED"),
]


def main() -> int:
    mutants = MUTANTS
    return framework.run_oneoff(mutants, targets=TARGETS)


if __name__ == "__main__":
    sys.exit(main())
