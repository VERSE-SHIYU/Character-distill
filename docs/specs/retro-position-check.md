# PR-4：位置核对用上回忆区

基线：main `0f03b937`（含 PR-3 #179）。分支：`feat/retro-position-check`。

## 0. 做什么

阶段按「摘录在原文里的位置」归，默认位置 = 故事时间（`core/phase_anchoring.py::phase_at`）。回忆区（PR-3 标出的倒叙章节）不满足这一点。本 PR 让位置核对认得回忆区：

1. 摘录在回忆区里的出现位置**不作位置证据**。回忆区内外都有 → 只用回忆区外的位置；只在回忆区里 → 保留模型标的阶段。
2. 阶段锚点落在回忆区 → 整卡跳过位置核对，原因写明「阶段 k 锚点落在回忆区（第 N–M 章…）」。锚点重选是 PR-5。
3. 回忆区未标（`NOT_DETECTED` / 标不了 / 原文章节对不上）→ 行为与今天完全相同。

不做：锚点重选、提示词改动（PR-5）。

## 1. 目标检查（`tests/test_retro_position_check_goal.py`，第一个提交 3d215e4a 为 xfail strict）

| 检查 | 守什么 |
|---|---|
| g1 | 只在回忆区的摘录保留模型标注（阶段 1）；未标时同今天被改挂到阶段 2 |
| g2 | 回忆区内外都出现的摘录只用回忆区外的位置（→ 阶段 1）；未标时同今天（命中阶段 2） |
| g3 | 锚点落在回忆区 → 整卡跳过，warning 原因含「阶段 2 锚点落在回忆区（第4–4章日记）」 |
| g5 | `card_from_draft` 不传 `retro` → TypeError |
| g5b / g5c | 真 `/start`、真 `/run_stream`：转卡拿到的 `retro` 就是 PR-3 `resolve_retro_regions` 的结果 |

## 2. 改动

| 文件 | 改什么 |
|---|---|
| `core/retro_regions.py` | `NOT_DETECTED`（不识别回忆区的入口显式传它）；`RetroRegions.spans(source_text)` → 规范化原文里的 `[(起, 止, 标签)]`（首章标题起、到末章下一章标题止；用 `quotes.normalized_starts`，真原文 4 个点约 0.9 s；章节对不上 → 空表 + warning） |
| `core/phase_anchoring.py` | `_region_of`；`phase_ranges(…, retro=())` 锚点落回忆区 → 原因；`PhaseAnchors.retro`；`build_anchors(…, *, retro=())`；`verify`：先滤掉回忆区里的出现位置，`not loc` 并进「命中所标阶段」那一支（只此一处） |
| `core/card_draft.py` | `card_from_draft(data, source_text, *, retro: RetroRegions)` 必填；有阶段时 `retro.spans(source_text)` 交 `build_anchors` |
| `core/distiller.py` | 三处库入口（`distill`、`_distill_longcontext`、`distill_incremental`，生产不走）传 `NOT_DETECTED` |
| `web/routers/distill.py` | `/start` 后台任务、`/run_stream` 接住 `resolve_retro_regions` 的返回值，传给 `card_from_draft` |
| `tests/test_phase_anchoring.py` | s1 结构锁改为 AST：五处生产调用、每处 2 个位置参数 + `retro`；`_spy` 签名带 `retro` |
| 13 个测试文件 | `card_from_draft(…)` 补 `retro=NOT_DETECTED`（AST 定位改写，58 处） |
| `tests/perf/*_mutations.py` | 调用点形态变了的变异文本同步（M10/M12/M20–M22/M28/M29/M30 等）；`_IN_TAGGED` 锚点随 `verify` 改；红源产物重生成 |

## 3. 真数据核对（谢凛羽草稿 d0cdf200fa93 + 原文 3335472a438b，回忆区 = 第 491–504、508–509 章）

| 情形 | 未标（今天） | 用回忆区 |
|---|---|---|
| 草稿原锚点 | 整卡跳过：「锚点位置不严格递增：949671 → 916341」 | 整卡跳过：「阶段 1 锚点落在回忆区（第491–504章日记）：我倒要瞧瞧，两年不见，她如今是什么境况。」 |
| 换成三句正文锚点（PR-5 将做的事） | 能分阶段；改挂 16 条 | 能分阶段；改挂 8 条 —— 少掉的 8 条证据都在日札里（如「再想她，我就是狗！」），现在保留模型标注 |

剩下 8 条改挂的摘录都在回忆区外（构造保证：`verify` 只用回忆区外的位置改挂）。

## 4. CI 红一次（f0a5bec3）

gate 红：`test_security_authz.py::test_15` 得 429 而非 404。根因：`limiter` 是进程级单例，`/run_stream` 限 16/hour、全量跑时所有文件共用「testclient」一个桶；本 PR 的 g5c 多调一次就超限。本 PR 照 `test_text_upload_filename.py` 的现成做法在目标检查文件里前后各 `limiter.reset()`。沙箱全量 3727 passed、1 skipped。**根上的问题**（桶跨文件共享，下一个加 `/run_stream` 测试的人会再撞）不在本 PR 范围，报 Tracy 定是否另开 PR 在 `tests/conftest.py` 统一隔离。

## 5. 已知边界

同 PR-3：分组路径、无「第N章」标题的书、章内回忆（第 399 章）都按未标处理。
