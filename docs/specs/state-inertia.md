# 状态类字段「沿用最近一次」投影（③ 验收后续）

> 基线：`origin/main` @ `4311b76`。决定：Shiyu 2026-10-09 选「沿用最近一次」。
> 交付物：本文件 + `tests/test_state_inertia_goal.py`（目标检查）。目标检查在 main 上 5 红 3 绿，3 条绿的是回归守卫（I0、I3、I6）。

## 1. 问题（已查实，不是推测）

演示卡重蒸验收的 B 调查覆盖 5 张卡 × 5 个字段（这是执行方的调查表，Claude 没有直接读那 5 张卡的数据）。按这张表，所有在聊天里显示为空的格子，**全部**是同一个原因：值写在较早阶段的 overlay 里，而聊天默认用最后阶段，最后阶段没有值。没有一格是「模型没写」「阶段号无效被丢」或「进了未定位区」。三档关系做法要三句全非空才会生效（`core/chat_engine.py:881`），所以这些卡上三档都没生效。

根因在投影：状态类字段到了阶段 k，只取「阶段 k 自己那一格 + 全程」（`core/arc_view.py:206` `kth = get_path(phases[k - 1].overlay, path)`）。而值挂在哪个阶段，取决于原文摘录落在哪里（`phase_anchoring`），所以一个人一贯的做法，只要后面阶段没有新的摘录，到后面阶段就没了。关系态度早就是「≤k 里取最新」（`core/arc_view.py:155–162`），状态类没有这样做。

## 2. 规则（只此一条）

为什么选这条（对照返工经验第 28 条）：
- 证据一：B 调查表，空的格子全部是「值在早期阶段」。
- 证据二：仓库里已有同样的先例，关系态度的投影就是「≤k 取最新」（`core/arc_view.py:155–162`）。
- 证据三：状态「一直成立，直到被改变」是状态推理的通用原则，即事件演算里的常识惯性律。这是通用原则，**没有找到**针对角色卡分阶段投影的文献，属于自研应用。

规则：

状态类（`REGISTRY` 里 `layer == "state"` 的路径）投影到阶段 k 时：

1. **沿用最近一次**：阶段 k 的「阶段专属那一格」= 1..k 里最近一个有值的阶段的那一格。阶段 k 自己有值就用自己的。**整格替换**，列表也不与早期合并。**永远不看 k 之后**。
2. **列表**：结果 = 沿用来的阶段条目在前，全程（顶层）条目在后。B3 顺序契约不变。
3. **单值**：阶段 k 上有证据的值优先，即阶段 k 自己那格，其次全程顶层。只有这两样都没有时，才沿用早期阶段的值。理由：全程值的意思是模型在每个阶段（包括 k）都标了，它有证据；沿用来的值只是推定。
4. 单值字段为什么「k 上有证据的优先」：这是技术判断，由 Claude 定。依据是全程值有阶段 k 的证据，而沿用值没有（规则 3）。
5. 「沿用最近一次」只写一个函数 `inherited(values, k)`，放在 `core/arc_view.py`。状态类投影和关系口径（`_project_relationships` 里 `r2.note` 那句）**都调它**，不留第二份写法。

**不在本段改的**：
- 情境做法 `situation_behaviors` 是 custom 投影（`core/arc_view.py:177`）。它的「阶段下的做法只在那个阶段成立」是 ② 定下的语义（`core/schema.py:144`），本段不动。
- 经历类、卡片存储、蒸馏、`card_outline`（展示与导出按阶段原样列出）都不动。

## 3. 连带效果（有意的）

- 所有状态类路径一起生效：性格特征、价值观、动机、雷点、软肋、亲近条件、对话示例、口癖、语气、句式、三档等。阶段 k 没有自己的值时，会看到早期阶段的值。
- `tests/test_personality_goal.py::test_g12_phase_limited_mode_and_condition_apply_only_in_that_phase` 的最后一条断言，钉的是旧语义：只在阶段 1 成立的亲近条件，不能出现在阶段 2。改成新语义：阶段 2 没有自己的亲近条件，就沿用阶段 1 的。三档 close 那几条断言不变：阶段 2 有全程 close，按规则 3 全程优先。改写时在测试 docstring 里写明「语义 2026-10-09 改为沿用最近一次，见 docs/specs/state-inertia.md」。`docs/specs/personality-inject.md` 的 G12 行加一句「已被 state-inertia.md 取代」。
- 影响面（Claude 用原型实跑，只作为影响面的事实，不作为正确性的保证）：投影相关的 45 个测试文件，815 passed，只有 G12 红（另 deselect 了已知稳定红的 `test_relationship_batch_splits_and_merges`）。如果实现后红的不止 G12，就停下报告，不要顺手改测试。

## 4. 参考实现（Claude 沙箱原型，可照抄，也可等价改写）

```python
def inherited(values, k: int):
    """沿用最近一次：阶段 1..k 里最近一个有值的那一格；都没有 → None。永远不看 k 之后。"""
    for v in reversed(values[:max(k, 0)]):
        if v:
            return v
    return None

# project_card 的状态类分支
if spec.layer == "state":
    per_phase = [get_path(p.overlay, path) for p in phases]
    own = per_phase[k - 1] if k >= 1 else None
    if spec.kind == "list":
        base = list(get_path(proj, path) or [])
        set_path(proj, path, list(inherited(per_phase, k) or []) + base)
    elif own or not get_path(proj, path):
        kth = inherited(per_phase, k)
        if kth:
            set_path(proj, path, kth)

# _project_relationships
r2.note = inherited([pa.note for pa in upto], len(upto)) or ""
```

`project_card` 和 `_project_custom` 的 docstring 里写「阶段 k 特有」的地方，改成「阶段 k（或沿用最近一次）」。

## 5. 测试

- 目标检查 `tests/test_state_inertia_goal.py`：I0–I6，共 8 条，全部要绿。
- 本地只跑受影响的文件：目标检查、`tests/test_arc_view.py`、`tests/test_personality_goal.py`、`tests/test_personality_motives_goal.py`、`tests/test_personality_distill_goal.py`、`tests/test_arc_behavior_inject_goal.py`、`tests/test_arc_phase_select.py`、`tests/test_arc_phase_unlocated_goal.py`、`tests/test_context_engine*.py`（存在的才跑）。库用 docker PG。合并门是分支 CI。

## 6. 对账表（复核方必须验的清单）

按返工经验第 14 条：这张表不是保证，是审计时**必须逐条验**的清单。审计方（Claude）实跑，并且必须自补变异，放宽、过严两个方向都要有。执行方 S2 也跑一遍，贴原始输出。

| 行为 | 守它的测试 | 让它变红的变异 | 改后能触发的状态 |
|---|---|---|---|
| 最后阶段沿用早期的值 | I1、I5 | N1 退回只看阶段 k；N7 单值不沿用 | 默认阶段聊天里出现这张卡自己的三档 |
| 阶段自己有值就整格替换 | I2 | N2 列表改成 ≤k 全部并集；N6 忽略自己那格 | 阶段 3 只有阶段 3 的动机 |
| 不看 k 之后 | I3 | N3 沿用时扫全部阶段 | 阶段 1 看不到阶段 2、3 的值 |
| 阶段条目在前、全程在后 | I4、`test_u18_dialogues_phase_k_first_then_top` | N5 全程放前面 | 动机顺序 = [阶段, 全程] |
| 单值：k 上有证据的优先 | I6 | N4 沿用值盖过全程 | 阶段 3 的 close 是全程值 |
| 只有一个 `inherited` | 审计 grep：`core/arc_view.py` 里 `reversed(` 只在 `inherited` 内出现 | 关系口径另写一份 `next(...)` | — |

## 7. 执行步骤

- **S0**：先进 plan mode，把计划报给 Shiyu。审本节对账表：每个变异在改后的代码上是否可观测、每个决定是否都有测试。有问题先停下报告。
- **S0-1**：开分支 `feat/state-inertia`，基于 `origin/main`。放入本文件（`docs/specs/state-inertia.md`）和目标检查，用 `test -f` 确认两个文件都在。跑目标检查，应为 **5 failed、3 passed**，失败的是 I1×2、I2、I4、I5。不符就停下。
- **S0-2**：第一个提交只放这两个文件，`test(state-inertia): add spec and goal check`。推送，CI 应为红。
- **S1**：实现第 4 节，改写 G12，更新 personality-inject.md 的 G12 行。提交：`feat(arc-view): project state fields with carry-forward from the latest phase ≤k`。
- **S2**：跑第 5 节的测试，再在本地跑第 6 节的 N1–N7（`run_oneoff`），贴原始输出。推送，等分支 CI 变绿。**不开 PR、不合并**，报告给 Shiyu，由 Claude 审计。

## Skill

| 步骤 | skill | 用途 |
|---|---|---|
| S0 | `@search-first` | 确认仓库里没有已经写好的「沿用最近一次」函数，有就复用 |
| S1 | `@test-driven-development` | 先让目标检查红，再实现到绿 |
| S2 | `@verification-before-completion` | 测试和变异现跑现贴 |

## 补充（执行前：文档范围裁定）

本文件正文第 3、4 节点名改写 `G12` 与 `personality-inject.md`，未点名两处陈旧文档。Shiyu 2026-10-09 裁定：

1. **`core/card_layers.py` 按新语义改写**：第 7–8 行（`state` 行的描述）与第 67 行注释（`# ── 状态（单值：阶段 k 有值用之，否则顶层）…`）里「阶段 k 特有」的旧口径，改成「阶段 k（或沿用最近一次）」。理由是这两处是投影语义的权威说明，若不改会与 `arc_view.py` 的实现矛盾。
2. **`docs/specs/arc-phase-fields.md` 只在被取代的行末追加标注，不改原文**：
   - DA1（第 21 行）
   - DA15（第 33 行）
   - 状态投影表的列表行 / 单值行（第 126 / 127 行）

   每处**行末**追加「（2026-10-09 已被 state-inertia.md 取代）」，正文一字不动。理由是原台账是历史证据，改写会抹掉「当时怎么想的」，只加取代标注即可追溯。
3. **本段追加第三个提交**：`docs/specs/artifacts/state_inertia_mutations.py`（N1–N7 变异脚本）超出 spec §7 步骤表的字面，为满足「证据产物必须入库」（台账里引用的变异必须有其产数脚本入库，留 gitignored scratch 不可追溯）。

以上三条相对 spec §7 字面的偏离，均按 Shiyu 裁定执行，在此记明。
