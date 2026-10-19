# 阶段 1 的锚点不参与核对

基线 main `c24a565`。分支 `fix/phase1-anchor-not-checked`。修复包 A（总问题见「祈灼卡修复 · 当前问题」P1）。

## 目标
阶段 1 的锚点不划区间，就不该参与任何核对，更不能因为它判不过而把别的阶段的锚点换掉。

## 根因（已核实，main c24a565）
- 祈灼草稿重转（10-19，task 1989b374baea）：阶段 1 锚点「他曾以为，此生都不会遇见……」取自第 383 章的章内回忆（716034），阶段 2 锚点「因为，我想当你的底牌。」在第 122 章（224224），本来是对的。
- `phase_anchoring.phase_ranges` 把阶段 1 锚点也定位、拿来和阶段 2 比顺序 → 「锚点位置不严格递增：716034 → 224224」（10-18 日志原文）。
- `distiller.locate_phase_starts`（:1090）只看 `build_anchors(...).reason` 非空就调 `phase_locate.locate`，把阶段 2..n 全部重找；重找结果是第 335 章，第 122 章被换掉。
- 同一个锚点在两处的前提相反：#176 提交说明「keeping its anchor for the strictly-increasing order check」，没写理由、目标检查也没测它的用处；#185 的 `phase-locate.md` §2 写「重新定位时原锚点通常已经错了……所以置空」。**一条规则写在两处、前提相反。**

## 改法（规则只在一处）
`core/phase_anchoring.py`：
1. `phase_ranges`：阶段 1 直接记 0，不取锚点、不定位、不查回忆区、不比顺序；顺序核对照旧在 `starts`（第一项为 0）上做，即阶段 2..n 严格递增且大于 0。去掉 `bounds`（`starts` 已以 0 开头）。
2. `build_anchors`：只看阶段 2..n 有没有省略号来决定要不要算段落边界。
3. `log_phase_starts` 整卡跳过分支：阶段 1 那一行写「不参与核对（阶段 1 从全书开头算起）」，不再定位它。

三个调用方（`distiller.py:1090`、`:1110`，`card_draft.py:451`）都走 `build_anchors`，不改。

## 目标检查（第 1 个提交 xfail strict）
`tests/test_phase1_anchor_not_checked_goal.py`（替身书复用 `test_phase_locate_goal.py`）：
- g1 阶段 1 锚点比阶段 2 靠后 → 不跳过，阶段 2 从自己的锚点起；
- g2 同一草稿 → `locate_phase_starts` 一次判断都不调、草稿不动；
- g3 阶段 1 锚点查不到 / 在回忆区 → 都不影响；
- g4 阶段 2..n 之间乱序照旧跳过。
改前 g1–g3 红、g4 绿（沙箱实跑）。

## 随之改的旧断言（都是旧规则本身，不是放宽）
| 文件 | 原断言 | 改后 | 理由 |
|---|---|---|---|
| `test_phase1_range_from_start_goal.py` g4 | 阶段 1 锚点乱序 → 跳过 | 不跳过（改名 `test_g4_phase1_anchor_is_not_order_checked`） | 就是要改掉的行为 |
| `test_anchor_skip_diagnostics_goal.py` g2 | 阶段 1 明细写章号与位置 | 写「不参与核对」 | 明细与规则一致 |
| `test_anchor_skip_diagnostics_goal.py` g6 | 三个阶段都断言章号未知 | 断言阶段 2、3 | 阶段 1 不再定位 |
| `tests/perf/arc_phase_anchoring_mutations.py` M20 / M32 | 定位串指向 `bounds` / 旧 `if i == 0` 位置 | 指向新代码；M32 改为「阶段 1 锚点重新参与核对」 | 变异驱动的定位串必须存在（`test_mutation_anchors`） |

另：`docs/specs/phase-locate.md` §2 的「只参与顺序核对」一句改为引用本文件。

## 验证
本地只跑受影响文件（库用 docker 测试库）：
```
pytest -q tests/test_phase1_anchor_not_checked_goal.py tests/test_phase_anchoring.py tests/test_phase1_range_from_start_goal.py tests/test_phase_anchor_exact_tier_goal.py tests/test_phase_anchoring_elided.py tests/test_phase_locate_goal.py tests/test_anchor_skip_diagnostics_goal.py tests/test_mutation_anchors.py tests/test_card_draft.py tests/test_card_redraft_goal.py
```
审计沙箱实跑：上面 10 个文件 116 passed；另 14 个调用 `build_anchors` / `card_from_draft` 的文件 242 passed。全量交给分支 CI。

## 结构自检
| 问 | 答 |
|---|---|
| 「阶段 1 锚点不用」写了几处 | 1 处（`phase_ranges`）；`build_anchors` 与明细日志跟随它，#185 的置空保留但不再是唯一防线 |
| 调用方要知道吗 | 不用，三个调用方都只读 `reason` / `ranges` |
| 以后改阶段 1 的规则改几处 | 1 处 |

## 不在本 PR
- 阶段 2..n 的锚点落在章内回忆、顺序又恰好对时发现不了（P6）；
- 重新定位本身的判断规则（P2）；
- 阶段被合并（P3）。
