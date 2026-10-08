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

只动 `core/relationship_batch.py` 两处，**不改 `card_draft` 的类型**（`DraftAttitude.phase` 仍是 int）：

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
