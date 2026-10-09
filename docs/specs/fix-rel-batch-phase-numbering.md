# 关系分批的阶段序号口径（spec 外缺陷修复）

> 前置：spec `arc-phase-fields.md`（§4.5 关系分批）、`AGENTS.md` 第 6 条（本段改动面内直接修）。
> 参考实现：分支 `fix/rel-batch-phase-numbering`（本文件随该分支交付）。

## 已查实的约束（@ 4311b761）

- `attitudes[].phase` 是 **int**（`core/card_draft.py:73` `DraftAttitude.phase`），转卡时当 **1-based 下标**用：`count = len(phases)`（`core/card_draft.py:338`）、`by_phase[p - 1]`（`core/card_draft.py:248`）。阶段号必须落在 `phases` 里的**位置**上。
- 提示词给的阶段清单在 `_batch_prompt` 里拼成 `阶段依次为：…`（`core/relationship_batch.py:69` @ 4311b761），**只给阶段名、不给序号**；规则第 4 条只说「没有阶段时 phase 填 0」（`core/relationship_batch.py:35` @ 4311b761），从没讲有阶段时填什么。
- 阶段名来自 `_phase_labels`（`core/distiller.py:448-451`）：**1-based 枚举、永不空串**（空 label 退成 `阶段 i`），故序号 = 位置。

## 缺陷

模型被要求按阶段给 `attitudes[].phase`，但提示词没给阶段序号，只能拿阶段名去填 → `phase` 落了中文（如「强撑体面」）→ pydantic 校验失败 → 整步「蒸馏失败：数据校验错误」。

真跑复现：孔乙己 / 祥林嫂 / 魯四老爺 / 趙太爺 全在 95% 处失败（阿Q 侥幸填对整数而通过），DB 里最新一张卡停在 2026-10-03。

## 改动

只动 `core/relationship_batch.py` 两处，**不改 `card_draft` 的类型**（`DraftAttitude.phase` 仍是 int）——**本句已被下方「补充（审计后）」取代**：提示词之外，还要给 `card_draft` 的阶段号类型加一层兜底。

1. 规则第 4 条：`没有阶段时 phase 填 0。` → `phase 填阶段序号（整数 1..n）；没有阶段时填 0。`
2. `_batch_prompt` 的阶段清单按序号列：`1. 名称；2. 名称`；**序号取 `phases` 里的位置**（空阶段让位后编号不重排），无阶段仍写 `（无阶段）`。

## 对账表

变异脚本：`docs/specs/artifacts/fix_rel_batch_phase_numbering_mutations.py`（`run_oneoff`）。

| 变异 | 应红 |
|---|---|
| MG1 去掉阶段编号（退回「名称、名称」） | `tests/test_arc_phase_fields_unit.py::test_rel_batch_prompt_numbers_phases_by_position` |
| MG2 阶段编号从 0 起（错位一格） | 同上 |
| MG3 口径删掉「没有阶段时填 0」 | `…::test_rel_batch_prompt_without_phases_marks_none_and_rule_says_zero` |

## 测试

`tests/test_arc_phase_fields_unit.py` 两条：提示词里有编号且序号落在 `phases` 的位置上；无阶段时写「（无阶段）」且口径写明填 0。本地只跑该文件 + 变异脚本，不跑全量（SPEC-STANDARD 第 3 条）。

---

## 补充（审计后）

@ `e58bf6cd` 审计发现：上面的提示词改法**只防了一半**。提示词防住了模型照规则填序号，但只要模型把
非数字写进 `phase`（实测 `DraftAttitude(phase="强撑体面")`），pydantic 在 `DraftOccurrence.phase`
/ `DraftAttitude.phase`（均 `phase: int`）就先抛 `ValidationError` → 整张卡在 95% 处失败。规则 0
`_valid_number_rows`（撤回 + warning，不让整卡失败）根本走不到。非数字字符串、`None`、布尔、小数
都属这一类；做法（`DraftOccurrence`）、记忆、状态类字段全受影响。

### 改动（`core/card_draft.py`）

1. **阶段号类型只在一处定义**：`PhaseNumber = Annotated[int, BeforeValidator(_phase_number)]`，
   `DraftOccurrence.phase` 与 `DraftAttitude.phase` 都改用它。`_phase_number` 做输入规范化：
   - 能转成整数的照转（`"2" → 2`、`2.0 → 2`），行为与改动前一致；
   - 转不成（非数字串、`None`）或小数非整（`2.5`）、布尔（`True`）→ 打**一条** warning 写明原值，
     按 `0` 处理。`0` 在有阶段的卡里被规则 0 当不合法撤回 —— 坏值只连累那一条，不炸整卡。
   - **调用点不写兜底**：`_convert_relationships` 等处不得各写一份转换。
2. `draft_schema()` 不改：`Annotated[int, BeforeValidator]` 的 JSON schema 仍是
   `{"title": "Phase", "type": "integer"}`，发给模型的结构里 `phase` 仍是 integer。

### 对账表（追加）

| 变异 | 应红 |
|---|---|
| MG4 规则第 4 条写成「整数 0..n-1」 | `…::test_rel_batch_rule4_pins_one_based_range` |
| MC1 `_phase_number` 去掉兜底（非数字重抛） | `…::test_bad_phase_number_on_occurrence_retracts_row_not_whole_card` |
| MC2 兜底返回 1 而不是 0（坏值被误挂阶段 1） | 同上 |
| MC3 只 `DraftAttitude` 用共用类型、`DraftOccurrence` 没用 | 同上 |
| MC4 `draft_schema` 里 `phase` 变 string | `…::test_draft_schema_phase_stays_integer` |

### 测试（`tests/test_arc_phase_fields_unit.py` 追加）

`phase="强撑体面"` 时整卡不抛错、该条撤回、有 warning、同卡其他条目不受影响（做法 occurrence 与
关系 attitude 两个入口各一条）；`phase="2"` 与 `phase=2` 转出同一张卡；共用类型与 schema 各一条守
结构。**放本文件而非 `test_card_draft.py`** —— 后者是 `card_draft_mutations.py` 的覆盖域，加判别器会让
`test_lock_coverage` 变红。
