# ③ 性格注入 · 段 3：动机进聊天 prompt（「## 想要什么」）（spec）

基线：main `c1d0c1ac`（段 1 PR #122、段 2 PR #123 已合并）。新分支 `feat/personality-motives`。
上游：设计稿 v6（分支 `docs/personality-3a-design`）§4a、§7 T23、§8 Q11。本文件只把这几节落到代码上，不改设计。
流程（返工经验第 30 条）：
1. Claude 写本文件和目标检查；
2. 执行方实现；
3. Claude 读 diff、跑变异审计（Shiyu 10-08 同意由 Claude 审）；
4. Shiyu 合并。

## 0. 目标与范围

洗白主要发生在动机上（设计稿 §4a，依据 2601.04716）。段 2 已经让蒸馏产出按阶段挂的 `motives`，但聊天 prompt 还没有读它。段 3 做一件事：卡片核心层在「## 遇事的做法」之后加一节「## 想要什么」，每条动机一行，读的是投影卡。

同一改动面里有一处要一并收拢（返工经验第 6 条）：「一条一行 `- 内容`」这种列表写法，main 上已经手写了四遍（§4 第 12 条）；如果段 3 照抄，就成了第五遍。所以先抽出 `bullet_lines` 作为唯一写法，五处都改用它。输出逐字不变。

覆盖面：一对一（含流式、agent）、群聊、主动消息。这些都经过 `ContextEngine._build_card_core`，见 §4 第 3、4 条。

不覆盖：开场白、苏醒台词、市场 @ 回复，这些是独立的短 prompt，同 ② 的处理。

不做：
- 不把动机加进每 4 轮一次的临时重注入。那里只放做法表，设计稿 §4a 已定；动机会不会随轮数变淡，演示卡验收时看。
- 不改评估 prompt。
- 不做前端（第四步）。
- 不做 S1。

## 1. 目标检查（先跑它）

`tests/test_personality_motives_goal.py`（随本 spec 交付，**第一个提交只放它和本文件**）：

```
python -m pytest tests/test_personality_motives_goal.py -q
```

在 main `c1d0c1ac` 上实跑（Claude 沙箱 PG，2026-10-08）：

```
FAILED tests/test_personality_motives_goal.py::test_m1_motives_of_the_chosen_phase_reach_the_chat_prompt[1-…]
FAILED tests/test_personality_motives_goal.py::test_m1_motives_of_the_chosen_phase_reach_the_chat_prompt[2-…]
FAILED tests/test_personality_motives_goal.py::test_m2_section_sits_after_behaviors_before_speaking_style
FAILED tests/test_personality_motives_goal.py::test_m3_group_and_proactive_entries_carry_it
4 failed, 3 passed in 2.50s
```

四条失败的原因都是「prompt 里没有『## 想要什么』」。

main 上绿的 3 条：
- M0 是夹具自检；
- M4（没有动机就不出这一节）、M5（只进了未定位区的动机不进 prompt）在 main 上本来就绿，因为 main 根本没有这一节。**这两条是回归守卫，不是证据**。

本分支完成后应为 `7 passed`。目标检查有错就先停下报告，不要直接改它。

可行性：Claude 在沙箱里加了 §2 那一行做原型，跑了目标检查和 ② 的两份测试（`test_arc_behavior_inject.py`、`test_arc_behavior_inject_goal.py`），结果 `54 passed`，跑完已撤回，没有提交。这一步只证明目标检查能被满足，**不是变异审计**。

| # | 检查什么 |
|---|---|
| M1 | 选阶段 k：「## 想要什么」下每条一行 `- 动机`；阶段 k 特有的在前、全程的在后；别的阶段才有的不出现 |
| M2 | 位置：`## 行为模式` < `## 遇事的做法` < `## 想要什么` < `## 语言风格`；卡上没有做法时，仍在「## 行为模式」与「## 语言风格」之间 |
| M3 | 群聊的入口 `_compose_context`（`group_session.py` 调用）、主动消息的入口 `ContextEngine.build`（`chat_engine.py:1475` 直接调用）同样带这一节 |
| M4 | 没有动机 → 整块不出现（回归守卫） |
| M5 | 只进了未定位区的动机不进 prompt（回归守卫） |

## 2. 改动

**① 抽出列表的唯一写法**（`core/context_engine.py`，放在 `behavior_lines` 前面）：

```python
def bullet_lines(items) -> str:
    """一条一行「- 内容」—— 人设与上下文里列表的**唯一**写法。"""
    return "\n".join(f"- {x}" for x in items)


def behavior_lines(behaviors) -> str:
    """「- 情境 → 做法」一行一条 —— 卡片核心层与每轮重注入共用的**唯一**写法。"""
    return bullet_lines(f"{b.situation} → {b.behavior}" for b in behaviors)
```

**② 现有四处改用它**，输出逐字不变：
- `behavior_lines` 本身（`:37–39`）；
- 扩展层的关键记忆（`:432`）；
- 扩展层的人际关系（`:436–440`，只把每条前面的 `- ` 去掉，交给 `bullet_lines` 加）；
- `core/chat_engine.py:1072`，「你和提及之人的关系」那块。`chat_engine.py:19` 的导入加上 `bullet_lines`。

**③ 段 3 本身**：`_CORE_SECTIONS`（`:45–47`）在「遇事的做法」那一行之后加一行：

```python
    ("想要什么", lambda c: bullet_lines(c.motives)),
```

标题、空块跳过、位置都由现有的 `_build_core_sections`（`:393–400`）处理，不另写。

不收进来的：`_build_phase_block` 的 `lines.append(f"- {phase_header(i, p.label)}：{p.state}")`（`:413`）。那是把逐行追加进一个和标题、说明混在一起的列表，不是「把一个列表渲染成若干行」，形状不同，硬套反而更绕。

可行性：Claude 在沙箱里按 ①–③ 改完，跑目标检查和 §5.2 里与 prompt 有关的 14 个文件，`225 passed`；改完后在 `core/` 下 grep `"\n".join(f"- `，只剩 `bullet_lines` 自己一处。跑完已撤回，没有提交。

动机的顺序（阶段 k 在前、全程在后）由投影保证（`core/arc_view.py:205–211`），渲染这一层不排序。

`core/context_engine.py:42–44` 的注释说「③ 的『## 想要什么』接在『遇事的做法』之后」，这句话落地后已不再是计划，改成现在时，或者删掉括号里那半句。

不需要改：
- `core/arc_view.py`：`motives` 已登记为 state / list，投影是通用的。
- `core/chat_engine.py`：重注入只放做法表（`:529`）。
- 注入守卫：它对卡上所有文本叶子做通用递归，见 §4 第 7 条。

## 3. 规则

| # | 规则 | 出处 |
|---|---|---|
| R1 | 读投影卡的 `motives`，每条一行 `- {动机}`，不加编号、不加阶段标记 | 设计稿 §4a |
| R2 | 没有动机 → 整块不出现 | 设计稿 §4a、T23 |
| R3 | 位置在「## 遇事的做法」之后、「## 语言风格」之前；没有做法时紧跟「## 行为模式」那段 | 设计稿 §4a；`_build_card_core` 的顺序 |
| R4 | 只有阶段 k 和全程的动机；未定位区的不进 | 设计稿 §4a；C17；B 的规则 |
| R5 | 不进每 4 轮的临时重注入 | 设计稿 §4a「不做」 |
| R6 | 「一条一行 `- 内容`」只在 `bullet_lines` 写一次；其余地方调用它 | 返工经验第 6 条 |

## 4. 判断清单（第 7b 条；基线 `c1d0c1ac`）

执行方第 0 步逐条复核，有一条不成立就停下报告。

| # | 判断 | 读过的行 / 命令 |
|---|---|---|
| 1 | 接入点只有一张表 | `core/context_engine.py:45–47`：`_CORE_SECTIONS = (("遇事的做法", lambda c: behavior_lines(c.situation_behaviors)),)` |
| 2 | 渲染、空块跳过都在 `_build_core_sections`，位置在「## 行为模式」之后、「## 语言风格」之前 | `:393–400`：`for title, body_of in _CORE_SECTIONS: body = body_of(self.card); if body: out += f"\n## {title}\n{body}\n"`；`_build_card_core` 先拼行为模式（`:366–375`）、再 `core += self._build_core_sections()`（`:376`）、再拼语言风格（`:378–388`） |
| 3 | 一对一、流式、agent 都经 `_compose_context` → `build_ex` → `_build_card_core` | `core/chat_engine.py:329` `built = self._ctx_engine.build_ex(`；`:422`、`:464` 调 `_compose_system_prompt`（`:339` 调 `_compose_context`）；`core/context_engine.py:298` `card_core = self._build_card_core()` |
| 4 | 群聊、主动消息同样经过 | `core/group_session.py:156`、`:323`、`:333` `engine._compose_context(...)`；`core/chat_engine.py:1475` `system_prompt = self._ctx_engine.build(`（`build` 调 `build_ex`，`context_engine.py:266`） |
| 5 | `ContextEngine` 拿到的是投影卡 | `core/chat_engine.py:206–208` `ContextEngine(card=self.card, …)`；`context_engine.py` 引入 `require_projected`（`:19`）。另：② 的锁 S3b 规定 `ProjectedCard(...)` 只在 `arc_view` 构造 |
| 6 | 投影把状态类列表排成「阶段 k 在前 + 全程在后」 | `core/arc_view.py:205–211`：`if spec.layer == "state": if spec.kind == "list": base = …; set_path(proj, path, list(kth or []) + base)` |
| 7 | 注入守卫通用覆盖所有文本叶子，不用为动机另加 | `core/moderation/card_guard.py:72–78`：`leaf_texts` 走 `iter_texts(card, prefix)` 递归；`tests/test_arc_phase_fields_readers.py:217` 已测 overlay 也覆盖 |
| 8 | 重注入只放做法表 | `core/chat_engine.py:529`：`return "\n\n【提醒：你遇事的做法】\n" + behavior_lines(self.card.situation_behaviors) + "\n"` |
| 9 | ② 留的守卫测试追加一行后仍能通过 | `tests/test_arc_behavior_inject.py:128–133` 用 monkeypatch 追加一个「想要什么」再断言顺序。真实一行加入后它会出现两个「## 想要什么」，断言找的是带「- 想要X」的那一个，仍成立（沙箱原型实跑通过） |
| 10 | 卡片核心层不受预算裁剪，新增几行动机计入总预算 | `context_engine.py:298–300`：`card_core = self._build_card_core()` 后 `budget -= count_tokens(card_core) + …`；动机每卡 2–4 条、每条约 20–40 字（设计稿 §6 的量级），对 32,000 的总预算可以忽略 |
| 11 | 开场白、苏醒台词、市场 @ 回复不走 `ContextEngine` | 在 `core`、`web` 下 grep `ContextEngine(` 只命中 `chat_engine.py:206`；`.build_ex(` 只在 `chat_engine.py:329` 和 `context_engine.py:266` |
| 12 | 「一条一行」的列表写法在 main 上手写了四遍 | `core/` 下 grep `f"- \{`：`context_engine.py:39`（`behavior_lines`）、`:413`（阶段块，形状不同，见 §2）、`:432` `memories = "\n".join(f"- {m}" for m in c.key_memories)`、`:438` `f"- {r.target}（{r.relation}）" + …`；`chat_engine.py:1072` `+ "\n".join(f"- {ln}" for ln in top)` |
| 13 | 关键记忆、人际关系、提及之人这三处的输出**没有测试守着** | 在 `tests/` 下 grep `【关键记忆】\|【你和提及之人的关系` 0 命中。所以要先补表征测试（§5.1 U4、U5），在 main 上就绿，改完仍绿 |

## 5. 测试

### 5.1 执行方要补的单测

| # | 一侧 | 另一侧 | 放哪 |
|---|---|---|---|
| U1 | 投影卡有动机 → `_build_card_core()` 里有 `\n## 想要什么\n- …\n` | 投影卡没有动机 → 没有这个标题 | `tests/test_arc_behavior_inject.py`（与「遇事的做法」的 C 组同处） |
| U2 | 重注入文本里**没有**动机 | — | 同上（守 R5） |
| U3 | `bullet_lines(["a", "b"]) == "- a\n- b"` | `bullet_lines([]) == ""`（空列表不出多余的行，`_build_core_sections` 靠它跳过空块） | 同上 |
| U4 | **先写、在 main 上就绿**：扩展层 `【关键记忆】` 下是「- 记忆」逐行；`【人际关系】` 下有态度时是 `- 对方（关系）：态度`，没有态度时是 `- 对方（关系）`、不带冒号 | 改完后仍绿 | `tests/test_context_engine_evidence.py` 或新文件，执行方定 |
| U5 | **先写、在 main 上就绿**：提及之人那块是「- 对X：关系，态度」逐行，最多 3 条 | 改完后仍绿 | 与该函数现有测试同处；没有就新建 |

`tests/test_arc_behavior_inject.py:128` 的 `test_c6` 是 ② 给 ③ 留的接入点演示，接入后它的用途已经由 U1 和目标检查 M2 承担。保留或删掉都可以，删的话在报告里说明。

### 5.2 本地只跑受影响的文件（第 15 条）

库用 docker PG（先查 55432 端口和容器名，被占就另起一个，跑完删掉）。

清单来源：在 `tests/` 下 grep `context_engine|ContextEngine|_build_card_core|_CORE_SECTIONS|## 行为模式|## 语言风格|_compose_context|motives` 的全部命中，去掉两份非测试辅助文件 `census_llm_call_contexts.py`、`evidence_fakes.py`；再加上段 1、② 的两份目标检查。全跑，不删：

```
python -m pytest tests/test_personality_motives_goal.py tests/test_personality_goal.py tests/test_personality_distill_goal.py tests/test_arc_behavior_inject.py tests/test_arc_behavior_inject_goal.py tests/test_agent_evidence.py tests/test_agent_loop.py tests/test_arc_phase_fields_locks.py tests/test_arc_phase_fields_readers.py tests/test_arc_phase_fields_unit.py tests/test_arc_phase_select.py tests/test_arc_phase_unlocated.py tests/test_card_draft.py tests/test_card_quotes.py tests/test_context_engine_evidence.py tests/test_distiller_routing.py tests/test_live_llm_refresh.py tests/test_live_llm_swap.py tests/test_rag_characters_filter.py tests/test_rag_isolation.py tests/test_rag_unusable.py tests/test_usage_identity_context.py -q
```

已知：`tests/test_arc_phase_fields_unit.py::test_relationship_batch_splits_and_merges` 在 main 上本来就不稳定（Claude 10-08 在 main `443c8ee0` 上连跑 3 次都失败；它断言并发调用的开始顺序）。它失败不算本段的问题，报告里注明即可。合并门是分支 CI。

### 5.3 审计时要验的变异（Claude 审计时跑，执行方不用跑）

| # | 方向 | 变异 | 应被谁打红 |
|---|---|---|---|
| X1 | 过严 | 删掉这一行 | M1、M2、M3 |
| X2 | 放宽 | 这一行放到「遇事的做法」之前 | M2 |
| X3 | 放宽 | 动机用「、」连成一行 | M1（每条一行） |
| X4 | 过严 | 只取第一条 `c.motives[:1]` | M1（缺全程的那条） |
| X5 | 放宽 | 顺序反过来 `reversed(c.motives)` | M1（阶段特有的应在前） |
| X6 | 放宽 | 没有动机时也输出标题（正文写「（无）」） | M4、U1 另一侧 |
| X7 | 放宽 | 重注入里也拼上动机 | U2 |
| X8 | 放宽 | `bullet_lines` 的前缀改成 `* ` | U3、U4、U5、M1 |
| X9 | 过严 | `bullet_lines` 空列表时返回 `"-"` | U3、M4 |
| X10 | 放宽 | 人际关系改回自己拼 `- `、同时 `bullet_lines` 也加 → 出现 `- - ` | U4 |

## 6. 执行方第 0 步（S0）

1. 从 main `c1d0c1ac` 开分支 `feat/personality-motives`。本文件放到 `docs/specs/personality-motives.md`，目标检查放到 `tests/test_personality_motives_goal.py`，用 `Test-Path` 确认两个文件都在。
2. 逐条复核 §4 判断清单，每条写「成立 / 不成立 + 看到的行」。一条不成立就停下报告。
3. 跑目标检查，应为 `4 failed, 3 passed`，失败的名字与 §1 一致。不一致就停下报告。
   （U4、U5 这两条表征测试在实现之前写，在 main 上应当就绿；它们证明 ② 的重构没改输出。）
4. 第一个提交只放这两个文件（commit message 用英文），推送。CI 上这一提交应当是红的。
5. 再写实现和单测，跑 §5.2，推送，等分支 CI 三道门全绿。
6. **不要开 PR，不要合并。** 报告里贴每一步的原始输出、§4 逐条的复核结果、最终提交号、CI 原始输出（`gh run view <id> --json conclusion,jobs`）。

执行中新发现的问题：在本段改动面内的直接修，需要拍板的才停下报告，不自行记账。commit message 一律英文。

## 7. 用哪些 skill

| 步骤 | skill | 用途 |
|---|---|---|
| S0 | `@search-first` | 先搜代码库，复核判断清单 |
| 实现 | `@test-driven-development` | 目标检查先红、补单测、再写实现 |
| 交付前 | `@verification-before-completion` | 报告里贴原始输出 |

## 8. 要 Shiyu 定的

无。设计稿 §8 Q11 已定「③b-2 并进③、作为最后一段」，§4a 已定做法与不做的事。

## 9. 风险

- 动机进 prompt 后，模型会不会把动机直接说出口（例如「我想要被人敬重」），而不是体现在行为里。这一点代码查不出，演示卡验收时专门看。若明显，再议是否在标题下加一句「不要直接说出来」。设计稿没有定这一点，本段不加。
- 动机在长对话里会不会变淡：不在本段处理，见 §0「不做」。
