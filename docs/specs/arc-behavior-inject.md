# spec：② 「情境→做法」注入

基线 `proto/arc-phase-unlocated` HEAD `40e9c75b`（= ①补完 B 最终版；main 仍是 `c97116b0`）· 参考实现 `docs/specs/artifacts/arc-behavior-inject-proto.patch`（sha256 `d98d721e599de31faa3c9c87eb2808c8d94af7d03b3bc37a4773d49169ad844f`，在 `40e9c75b` 上 `git apply --check` 通过）
决策来源：总计划「② 情境→做法注入」一节（调研 v1.3 + 10-06 两项补充决定）；重注入间隔 Shiyu 2026-10-07 定「每 4 轮」。

**一个 PR。等 ①补完 B 的 4 个 PR 全部合进 main 后再开工**（本补丁依赖 B 的 `UnlocatedItems` 与 `_project_custom` 现状）。纯后端，不改前端、不改蒸馏、不改存卡结构。

---

## S0（开工前，分钟级）

1. `Test-Path` + `Get-FileHash` 核 worktree 里的本 spec 与补丁，sha256 必须等于上面那串；不符就停。
2. 在**当时的 main**（B 已全部合入）上 `git apply --check docs/specs/artifacts/arc-behavior-inject-proto.patch`。失败就停下报告，不手改补丁。
3. 逐条复核 §2 的坐标（`git show HEAD:<file> | Select-String`），行号可漂，**内容对不上就停**。
4. 审对账表（§5）：逐行确认变异在改后代码上可观测、每个行为都有测试；有问题先报告，再动手。

---

## §1 目标检查（先跑这个）

**目标：开聊时选了阶段 k，模型每轮都看得到「阶段 k + 全程」的遇事做法；别的阶段、未定位区的做法看不到；长对话里每 4 轮再提醒一次，提醒不进对话记录。**

| 检查 | 用例 |
|---|---|
| 选阶段 1 的 system prompt 有阶段 1 与全程的做法、没有阶段 2 与未定位的 | `test_c1`、`test_c4`、`test_c5` |
| 第 5、9、13… 次回复前，当前用户消息末尾带做法表；其余轮不带 | `test_r1`–`test_r3`、`test_r7` |
| 提醒不写进 `history`，下一轮的历史里也没有 | `test_r7`、`test_r8`（流式、非流式各一） |

---

## §2 已查实的约束（`40e9c75b`，每条附读过的行）

- `core/arc_view.py:167-180` `_project_custom`：`:174` 阶段表重建为 `ArcPhase(label=p.label, state=p.state)`，**阶段下的做法被丢掉**；投影卡的 `situation_behaviors` 只剩顶层（全程）做法。`:179` 投影清空未定位区。
- `core/arc_view.py` `project_card` docstring：**B3 顺序契约**——状态类列表「阶段 k 特有在前 + 全程在后」。做法是状态类（B spec §3.2）。
- `core/card_layers.py:77` `"situation_behaviors": FieldSpec("custom", "list", "情境→行为")`：custom 层，通用投影不碰，由 `_project_custom` 处理；锁 S2（`tests/test_arc_phase_fields_locks.py`）的 custom 白名单已含它。
- `core/context_engine.py:341` `_build_card_core`：`:358` 「## 行为模式」、`:368` 「## 语言风格」。**全仓没有任何 prompt 读 `situation_behaviors`**（`grep situation_behaviors core/` 只命中 schema、card_layers、card_draft 等存卡侧）。
- 一对一、agent、群聊、主动消息都经 `ContextEngine.build_ex`：`chat_engine._compose_context`（`:322`）；群聊 `group_session.py:156/323/333`；重逢主动消息 `chat_engine.py:1477` 前用 `self._ctx_engine.build`。开场白、苏醒台词、市场 @ 回复是独立短 prompt，不经 card_core（总计划 10-06 在 `c97116b0` 核过）。
- 时间感知块：`chat_engine.py:420-426`（`chat`）与 `:468-474`（`chat_stream`）**同一段代码写了两遍**，附在当前用户消息末尾、不进 system prompt；`:428` / `:476` 之后才把原始用户消息写进 `self.history`。`scripts/run_agent_eval.py` 又抄了第三份。
- `self.history` 恢复：`web/routers/chat.py:216` 从库**全量**重建（`_rebuild_history_from_db` 只留 user / char）；`history.py:321`、`distill.py:1549` 会先放一条开场白（assistant）。→ 按「用户回合数」计数在恢复后仍准，按 `len(history)//2` 不准。
- 环境：测试库 PG 16 `charsim / ci_test_password / charsim_test` @55432（`docker-compose.test.yml`）。沙箱无 docker，用 apt 装的 PG 16 起同配置。

---

## §3 设计

### 3.1 投影（`_project_custom`）

`proj.situation_behaviors = 阶段 k 的做法（深拷贝）+ 全程做法`；k=0（无弧线卡）只有全程。其他阶段、未定位区不进。原卡不改。

### 3.2 卡片核心层（`context_engine.py`）

- 新增 `behavior_lines(behaviors)`：「- 情境 → 做法」一行一条——**核心层与重注入共用的唯一写法**。
- 新增 `_CORE_SECTIONS = (("遇事的做法", …),)` 与 `_build_core_sections()`：在「## 行为模式」之后、「## 语言风格」之前按表渲染「## 标题 + 正文」，正文空则整块不出现。
- **③ 的接入点**：③b-2 只在 `_CORE_SECTIONS` 里「遇事的做法」之后加一行 `("想要什么", lambda c: …)`，渲染、空块跳过、位置都不用再写（`test_c6` 守）。③ 的关系三档、分面在人格块（动态区），不进这里。

### 3.3 重注入（`chat_engine.py`）

- `REINJECT_EVERY = 4`、`reinject_due(prior_user_turns)`：此前用户回合数是 4 的正整数倍时注入，即第 5、9、13… 次回复前（论文：第 4、8、12 次回复后）。判据只此一处。
- `_attach_turn_blocks(llm_messages)`：取代 `chat` / `chat_stream` 里重复的时间感知段（`run_agent_eval.py` 也改调它）。在当前用户消息末尾依次附：时间感知块 → 到期的做法表提醒。不写进 `history`。agent 路径拿同一个 `llm_messages`，自然带上。
- `_build_behavior_reminder()`：内容 = `"【提醒：你遇事的做法】"` + `behavior_lines(投影卡.situation_behaviors)`；**不加**「挑选相关条目」「不要重复」之类的指令（v1.3）；卡没有做法时不注入。
- 只数 `role == "user"`：开场白、主动消息是角色说的，不算回合。
- **不做**：群聊不重注入（`group_session` 自己拼消息，本轮群聊不动；群聊照样经 card_core 拿到做法）；重逢主动消息、开场白不重注入（不是对用户回合的回复）。

### 3.4 出处表

| 设计 | 出处 |
|---|---|
| 整表放进 system prompt 固定前缀，不检索、不加挑选指令 | **文献**：MDRP（Findings of ACL 2026）全部条目进 prompt、提升集中在挑对条目与守边界；反向证据：同文 DeepSeek-Chat「挑对条目」最低、只测单轮（调研 v1.3，Shiyu 10-06 定） |
| 只放阶段 k + 全程 | 与①一致（状态类只取阶段 k）+ CDT（ACL 2026）未验证的不注入 → 未定位区不放 |
| 临时用户消息重注入、放消息末尾、不进对话记录 | **文献**：2609.24532（2026-09 预印本，未经同行评审）——重注入以 user 角色临时消息投递、只对下一次回复有效、不入记录；静态重注入降漂移 35%，按偏离检测再注入不比固定间隔好（p=.769）；作者推荐静态全人设重注入为低复杂度基线。ContextEcho（2026 预印本）：放用户消息优于系统提示 |
| 间隔 4 轮 | **文献**：2609.24532 静态方案在第 2/4/6 个检查点（每检查点 = 4 个回合 = 2 次角色回复）后注入，即每 4 次角色回复一次；更频繁方案（每 2 次）描述上略好但未做检验。Shiyu 10-07 选 4 |
| 时间感知在前、提醒在后 | **工程原则**（提醒贴着模型要回复的位置；论文未比较块内顺序） |
| 只数用户回合 | **代码事实**（§2：历史开头有开场白、可能有主动消息） |

**与论文的偏差（照实写）**：论文重注入的是**完整人设**；我们只重注入做法表（10-06 定：③的「多轮漂移」只做这一处）。35% 的效果是全人设重注入测出来的，做法表单独重注入的效果没有直接证据。论文的人设是多动症学生，不是负面性格；只有 28 回合。

---

## §4 文件

| 文件 | 改动 |
|---|---|
| `core/arc_view.py` | `_project_custom` 合并阶段 k 的做法 |
| `core/context_engine.py` | `behavior_lines`、`_CORE_SECTIONS`、`_build_core_sections`，`_build_card_core` 调一次 |
| `core/chat_engine.py` | `REINJECT_EVERY`、`reinject_due`、`_attach_turn_blocks`、`_build_behavior_reminder`；`chat` / `chat_stream` 去掉重复段 |
| `scripts/run_agent_eval.py` | 改调 `_attach_turn_blocks`（第三份拷贝） |
| `tests/test_arc_behavior_inject.py` | 新增 32 条（P 6、C 6、R 20 含参数化） |
| `docs/specs/artifacts/arc_behavior_inject_mutations.py` | 一次性变异脚本（不进元锁） |
| `docs/specs/artifacts/runlogs/arc-behavior-inject-mutations.txt` | 沙箱预跑原始输出 |

---

## §5 对账表（发出前已在沙箱预跑：`结论：21/21 条全红`，3 个目标文件还原后 sha256 逐字节一致）

| 行为变化（含连带效果） | 守它的测试 | 让它变红的变异 | 改后能触发的具体状态 |
|---|---|---|---|
| 投影卡做法 = 阶段 k 在前 + 全程在后 | P1、P2、P6 | M1、M5、M6 | 选阶段 1 开聊，system prompt 的「## 遇事的做法」第一条是阶段 1 的做法 |
| 其他阶段、未定位区的做法不进投影 | P4、C4 | M2、M3、M4 | 选阶段 1 时 prompt 里没有后期做法；卡上未定位区有条目时 prompt 里没有它 |
| 原卡不被改 | P5 | M7 | 同一张卡开两个存档（阶段 1、阶段 2），各自 prompt 只有自己阶段的做法 |
| 无弧线卡只放全程做法、不崩 | P3 | M19 | 旧卡（无阶段）开聊正常，prompt 有全程做法 |
| 核心层出现「## 遇事的做法」，在行为模式与语言风格之间，格式「- 情境 → 做法」 | C1、C2 | M8、M10 | 任意有做法的卡开聊，system prompt 里可见这一块 |
| 卡没有做法时整块不出现 | C3 | M9 | 没蒸出做法的卡，prompt 无空标题 |
| 一对一、群聊、agent 的 system prompt 都带上 | C5 | M8 | 群聊里该角色的 prompt 也有做法块 |
| ③ 只加一行即可接入「## 想要什么」 | C6 | M20 | ③b-2 合入后「## 想要什么」出现在做法块之后 |
| 每 4 个用户回合重注入一次（第 5、9、13… 次回复前） | R1、R1b、R2 | M12、M14 | 第 5 句用户消息发出时，发给模型的最后一条消息末尾有「【提醒：你遇事的做法】」 |
| 非到期轮不注入 | R3 | M13 | 第 6 句时没有提醒 |
| 只数用户回合，开场白与主动消息不算 | R5 | M15 | 有开场白 + 主动消息的会话，第 5 句时照样注入 |
| 时间感知在前、提醒在后 | R2 | M16 | 第 5 句的末尾依次是【此刻的现实感知】、【提醒：你遇事的做法】 |
| 核心层与提醒用同一个写法 | R2 | M11 | 两处的行逐字相同 |
| 提醒用所选阶段的做法 | R6 | M1 | 选阶段 2 的存档，提醒里是阶段 2 的做法 |
| 卡没有做法时不注入 | R4 | M21 | 没做法的卡第 5 句无提醒标题 |
| 提醒不进对话记录（非流式 / 流式） | R7、R8 | M17、M18 | 第 5 句后刷新会话，库里与 `history` 里那句用户消息是原文 |

---

## §6 测试（固定写法）

- 本地只跑受影响的：`tests/test_arc_behavior_inject.py` + 下面选集；PG 用 docker 测试库（§2）。不改前端，不跑 `npm test`。
- 变异：`python docs/specs/artifacts/arc_behavior_inject_mutations.py`，必须 `21/21 条全红`。
- 合并门是分支 CI；合并只做 `gh pr create` → `gh pr merge --merge`，PR 标题与描述用英文。

沙箱预跑（`40e9c75b` + 补丁）：受影响选集 76 个文件（`grep -l` 命中 `chat_engine|context_engine|arc_view|group_session|ChatEngine|ContextEngine|project_card|run_agent_eval|text_manager|routers.(chat|history|distill|group)`）**1448 passed, 1 skipped**；新文件 32 passed；锁 `test_arc_phase_fields_locks.py` 全绿。

---

## §7 每步配的 skill

| 步骤 | skill | 用途 |
|---|---|---|
| S0 核补丁、坐标、对账表 | `@search-first` | 先在仓库里查实坐标与现有实现，再动手 |
| 应用补丁、跑测试与变异、开 PR | `@verification-before-completion` | 贴实际输出再报「通过」 |

---

## §8 规模表

| 数据 | 实测（4 张公版样本卡，按阶段 k） | 处理 |
|---|---|---|
| 每次注入的做法条数 | 1–6 | 全放，不截断 |
| 做法块 token | 55–264（`core.tokens.count_tokens`） | card_core 不裁剪；重注入每 4 轮多这么多，不计入历史预算 |
| 聊天总预算 | 32,000（V4 系列，`_compute_budgets`） | 做法块 <1% |

## §9 调用点矩阵

| 调用点 | 核心层做法块 | 重注入 | 测试 |
|---|---|---|---|
| 一对一 `chat` | 有 | 有 | C5、R7、R8 |
| 一对一 `chat_stream` | 有 | 有 | C5、R7、R8 |
| agent 模式 | 有（`_compose_system_prompt`） | 有（同一 `llm_messages`） | 同上路径 |
| 群聊 `send` / `broadcast` | 有（`_compose_context`） | **无**（本轮群聊不动） | C5 |
| 重逢主动消息 | 有（`build`） | 无 | — |
| 开场白、苏醒台词、市场 @ | 无（独立短 prompt） | 无 | — |
| `run_agent_eval.py` | 有 | 有（改调同一函数） | — |

---

## §10 不做 / 风险

- 不做：群聊重注入；按偏离检测再注入（论文：不比固定间隔好）；重注入完整人设（10-06 定只重注入做法表）。
- 风险 1：做法表单独重注入的效果无直接证据（见 §3.4 偏差）。验收并进演示卡重蒸：同卡同阶段「只有①」与「①+②」对照原文，专看自私、虚伪类角色是否被洗白。
- 风险 2：每 4 轮那一轮的用户消息多 55–264 token，不进历史、不影响缓存前缀（附在最后一条）。

## 自检表

| 项 | 结果 |
|---|---|
| 每条代码判断附了读过的行 | §2 全部带坐标 |
| 与自身规则冲突 | 无：做法写法、注入判据、渲染入口各一处；时间感知三份拷贝收为一处 |
| 对照调研结论 | 整表进前缀、不检索、不加挑选指令、只放阶段 k + 全程、未定位不放、临时用户消息重注入 —— 与 v1.3 + 10-06 决定一致 |
| 每条边界两侧都测 | 放宽 / 过严成对：M1/M5、M2/M3/M4 vs M5、M12/M13、M8/M9、M21 |
| 变异预跑无存活 | 21/21 |
| 目标检查独立于被测代码 | C1、R2 断言字面量，不调 `behavior_lines` |

## 补充（执行与审计发现写这里）
