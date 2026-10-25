# 阶段起点改成章号（phase-start-chapter）

- 日期：2026-10-25。作者：Claude（蒸馏会话）。方案 Shiyu 10-25 定（「那就做到目前性价比最高的做吧」；10:45 最终方案表；10:57 PR 1 细节；11:12 探针第七版后定「没有可用章节的书保留锚点」）。
- 基线：`main` @ `ab4082f`。下文所有坐标都是这个提交上的，都是本轮实际读过的行。
- 分支：`fix/phase-start-chapter`。一个 PR，分 3 个提交段（§5），每段过目标检查再开下一段。
- 目标检查：`tests/test_phase_start_chapter_goal.py`（随本 spec 交付，sha256 前 16 位 `3373601ea462becf`），第 1 个提交原样入库。
- 取代：`docs/specs/phase-locate.md`（首行标「已取代（2026-10-25）：见 phase-start-chapter.md」）。
- 不在本 PR：PR 2（卡片编辑器可改阶段起点）、别名泛称、P5、P6、SZ 部署。复核发现的目标外问题记进跟踪文档，不并进来（第 37 条）。

## 0. 要解决什么、不解决什么

**解决（定位）**：有章节的书，阶段起点落进日札 / 番外 / 章内回忆，整卡跳过位置核对；按原文重新定位三轮小重放都不达标（跟踪文档 10-18～10-22）。
根因：G6 读整本书、自己抄一句「起始原句」当锚点，回忆区里的句子和正文里的同样可抄；重新定位是在这之后补救，检索、判断各自出错（返工经验附⑥）。

**修法**：代码给内容编号，模型只回编号，代码把编号换成位置。G6 改读「代码按章编号的人物摘录」（回忆区的章不进摘录），每个阶段只回 `start_chapter`；第 N 章 → 该章正文起点，由 `locate_starts` 一处换算。重新定位整条删掉。

**不解决（挑选）**：模型挑哪一章当转折，本 PR 不改（探针第五～七版都没改动它，第 49 条：停止改提示词）。挑错的一两个阶段交给 PR 2 让用户在卡上改。

**只对**同时满足两条的书：① 一次读完（`_takes_one_pass(text)` 为真，即 token 数 < 900k，`core/distiller.py:1903-1908` → `core/length_budget.py:49-51`）；② 原文有可用章节标题（`chapter_starts` 不为 None）。任一条不满足就照旧用锚点（J9、J10）。

「超过 900k token」和「走分组路径」是同一个条件：路由只看 `_takes_one_pass`（`:2425`、`:2720`），为假就走分组。这不是新加的豁免，原方案（10-25 10:57 PR 1 细节）本来只改一次读完的入口，这里把边界写明，改动面没有变大。实际能走到分组路径的只有聊天记录（上限按字符，200 万字，`length_budget.py:28`）和这条上传检查之前传的旧书 —— 小说超过 900k token 上传时就被拒（`length_budget.py:56-66`，`core/text_manager.py:259`）。

## 1. 目标检查（第 2 条）

`tests/test_phase_start_chapter_goal.py`，10 条，走真实代码（`Distiller.distill_incremental_stream` 一次读完路径、`phase_anchoring.build_anchors`）。原文、预言都在测试里手写：起点预言由 `chapter_starts` + `normalized_starts` 现算，不调被测的换算。

| 编号 | 检查什么 | `main` 上 | 段 1 后 | 段 2 后 | 段 3 后 |
|---|---|---|---|---|---|
| S1 | G6 读摘录：有【第1/3/5章】；回忆区第 4 章不在（回忆区未标时在）；没有整章无他的第 2 章、没有名字前后 6 段以外的段；只用别名出场的段在（别名传到了）；其余组仍读整本书 | 红 | 红 | 绿 | 绿 |
| S2 | G6 提示词与结构有 `start_chapter`，没有 `anchor`、`key_moments` | 红 | 红 | 绿 | 绿 |
| S3a | 章号 → 该章正文起点（阶段 1 恒为 0） | 红 | 绿 | 绿 | 绿 |
| S3b | 不存在的章 → 查不到；回忆区的章 → 回忆区；章号不递增 → 跳过；没有章节的书给了章号 → 查不到 | 红 | 绿 | 绿 | 绿 |
| S4 | 依赖组的阶段列表写起始章，不写「起始原句」 | 红 | 红 | 绿 | 绿 |
| S5 | G6 与 G1 同批先发（`Barrier(2)`，不同批则破障），其余组在两组都返回后才发；G1 以整书前缀开头 | 红 | 红 | 绿 | 绿 |
| S6 | 没有 `Distiller.locate_phase_starts`、没有 `core/phase_locate.py`，路由里没有「重定位」 | 红 | 红 | 红 | 绿 |
| R1 | 没有章节的书：G6 仍以整书前缀开头、仍要 `anchor`、没有 `start_chapter` | 绿 | 绿 | 绿 | 绿 |
| R2 | 旧草稿（只有锚点）在有章节的书上仍按锚点核对 | 绿 | 绿 | 绿 | 绿 |
| R3 | 分组路径（`_longctx_threshold = 0` 强制）的书即使有章节：G6 仍要 `anchor`、没有 `start_chapter`、没有【第N章】摘录 | 绿 | 绿 | 绿 | 绿 |

S1–S6 第 1 个提交带 `xfail(strict=True)`，哪段转绿就在那段的提交里摘掉对应标记。
沙箱实跑（`main` @ `ab4082f`，`ulimit -v 4000000`，`--runxfail` 看每条红的原因）：`3 passed, 7 xfailed in 3.70s`；7 条红的原因依次是「`distill_incremental_stream` 不收 `retro`」「G6 没有 start_chapter」「阶段 2 锚点为空」「第 9 章判成 EMPTY」「阶段列表仍写起始原句」「破障 → 服务暂时不可用」「`locate_starts` 还在」—— 都是被测的那件事，不是替身或环境。原型未做：本 spec 发出前没在原型上跑绿（第 2 条要求），见 §9。

## 2. 已查实的事实（第 7、7a 条；坐标 @ `ab4082f`）

| # | 事实 | 读过的行 |
|---|---|---|
| F1 | G6 维度 L 要锚点和 key_moments | `core/distiller.py:297` `'   - anchor：标志这一阶段开始的一句原文（10-40字，逐字照抄）…'`；`:298-299` `key_moments … '第一件写' + phase_locate.KEY_MOMENT_START` |
| F2 | G6 模板与「重要」规则也点名这两个字段 | `:372-373` `"anchor": "标志这一阶段开始的一句原文", "key_moments": [...]`；`:421` `含 label/state/anchor/key_moments/boundary_examples` |
| F3 | 一次读完两条入口都以整书为前缀、不收别名 | `:1912` `def _distill_longcontext(self, text, character_name)`；`:1925` `system_prefix=book_prefix(text) + "\n\n"`；`:1932` `_distill_longcontext_stream(self, text, character_name)`；`:1940` 同 |
| F4 | 调用方手里有别名，没往下传 | `:2425-2431` `if self._takes_one_pass(text): … self._distill_longcontext(text, character_name)`；`:2720-2725` `yield from self._distill_longcontext_stream(text, character_name)`；`distill_incremental_stream` 签名有 `aliases`（`:2695`，读过 `:2694-2696`） |
| F5 | 所有组共用一个 `system_prefix`；G6 单独先发，返回后其余组并行；依赖组带阶段列表 | `:2586-2593` `system_prompt = (system_prefix + DISTILL_PROMPT_BEFORE_NAME + character_name + format_prompt_after(group) + (phase_list_note…) + sub_schema)`；`:2610-2611` `C.ctx_thread(_format_one_group, args=(_PHASE_SOURCE_GROUP,) …).start()`；`:2620-2629` 其余组循环 |
| F6 | 「G6 先单发」同时承担缓存预热 | `:2577-2578` 文档串「弧线先单发也保住了缓存：一次读完的每次调用都带整本书的前缀，首批若并行而缓存未预热，会按未命中计费多次（spec §8.2）」 |
| F7 | 组划分与依赖由登记表推导；G1、G5 不依赖阶段 | `core/schema.py:334-350` `FORMAT_GROUPS`；`core/distiller.py:444-448` `_PHASE_SOURCE_GROUP`、`PHASE_DEPENDENT_GROUPS = … if g != _PHASE_SOURCE_GROUP and _has_phase_leaves(fields)` |
| F8 | 阶段列表唯一渲染处，写「起始原句」 | `:513-523` `_phase_list_note`：`f" · 起始原句：{checked.get(i) or '（从开头起）'}"` |
| F9 | 「起点算不算数」只在 `locate_starts` 判；阶段 1 恒 FROM_START、位置 0 | `core/phase_anchoring.py:139-165`：`if k not in checked: … StartStatus.FROM_START, 0` / `if not anchor: … EMPTY` / `loc = locate_anchor(…)` / `region = _region_of(retro, loc[0])` |
| F10 | 跳过原因与明细只读 `locate_starts` 的结果；递增在 `_skip_reason` | `:168-178` `_skip_reason`；`:286-310` `log_phase_starts`，头部 `锚点「{st.anchor}」→ ` |
| F11 | 回忆区是整章区间、规范化坐标 | `core/retro_regions.py:98-110` `spans`：「区间从首章标题起、到末章的下一章标题止」 |
| F12 | 章节标题只认行首阿拉伯数字「第N章」，重复或不递增 → None | `core/chapters.py:15` `_HEADING = re.compile(r"(?m)^[ \t　]*第(\d+)章")`；`:19-26` `chapter_starts` |
| F13 | 章号 → 正文起点（原文坐标）已有实现，在要删的模块里 | `core/phase_locate.py:54-66` `chapter_bodies`：`nl = source_text.find("\n", a, b); start = b if nl == -1 else nl + 1` |
| F14 | 原文坐标 → 规范化坐标只此一处 | `core/quotes.py:59-65` `normalized_starts(text, raw_starts)` |
| F15 | 草稿阶段的字段；key_moments 只为重定位 | `core/card_draft.py:153-161` `DraftPhase`：`anchor: str = ""`、`key_moments: list[str] = []`，文档串「key_moments 只供重定位用，不进卡」；`:145-150` `_key_moments_stays_required` |
| F16 | 发给模型的子结构由 `CardDraft` 派生 | `core/card_draft.py:218-234` `draft_schema(group)` |
| F17 | 转卡先 `CardDraft.model_validate`，再 `build_anchors` / `log_phase_starts` | `core/card_draft.py:479-485` |
| F18 | 重新定位的三个调用点 | `web/routers/distill.py:566-567` `with task_log.step("重定位"): distiller.locate_phase_starts(…)`；`:1218-1219` 重转路径 `await asyncio.to_thread(distiller.locate_phase_starts, …)`；`scripts/replay_distill.py:491-493` |
| F19 | 回忆区识别只在一次读完时做，分组路径「未标」 | `core/distiller.py:1111-1112` `if not self._takes_one_pass(text): return RetroRegions.unavailable(…)` |
| F20 | 分组路径 G6 读的是分析档案，不是原文 | `:2943-2951` `_format_groups_stream(… user_message=f"以下是关于「{character_name}」的完整分析档案…{format_input}", …)`（无 `system_prefix`） |
| F21 | G6 输出上限 | `:754` `CARD_MAX_TOKENS = 8192`（`:2597` 传给每组） |
| F22 | 窗口与阈值 | `core/length_budget.py:22` `CONTEXT_WINDOW_TOKENS = 1_048_576`；`:24` `LONGCTX_THRESHOLD_TOKENS = 900_000` |
| F23 | 整书前缀的写法 | `core/distiller.py:499-505` `return "以下是完整的文本内容：\n\n" + text` |
| F24 | 小重放按起点所在章判三项（起点齐全递增 / 不在回忆区 / 对真值） | `scripts/replay_distill.py:355-391` `_checklist` |
| F25 | 回忆区覆盖的章号已有现成口径，与 `spans` 共用同一个「对得上原文」判定 | `core/retro_regions.py:116-122` `chapters(source_text) -> set[int]`；`:124-134` `_matched_chapters`（未标或章号对不上原文 → None → 空集）；今天唯一的调用方是要删的 `phase_locate.py:201` |
| F26 | 两条路由在蒸馏前就拿到了回忆区 | `web/routers/distill.py:551-552` `retro = submit_to_main_loop(resolve_retro_regions(…))`，之后 `:380` 才 `distill_incremental_stream(content, name, aliases=…)`；重转流 `:1171` `retro = await resolve_retro_regions(…)`，`:1175` 才开流；`scripts/replay_distill.py:465`/`:480` 同 |

**设计判断**（每条附依据，第 28 条）：

| # | 判断 | 依据 |
|---|---|---|
| J1 | 章号换位置放进 `locate_starts`，不另开函数判 | F9、F10：跳过原因、明细、阶段区间都只读它的结果；放别处就是「起点算不算数」写两处（第 6 条） |
| J2 | 按草稿数据分支：阶段带 `start_chapter`（非 None）→ 按章；否则按锚点 | R2：旧草稿（续跑、重转）只有锚点，按书分支会把它们一律判空、整卡跳过；按数据分支它们照旧核对 |
| J3 | 问模型要哪种起点，只在「摘录建不建得出」一处定 | 摘录建得出 ⇔ 一次读完且有章节标题；分组路径不调它（J10） |
| J4 | 章号的正文起点复用 `chapter_bodies` 的算法，挪出 `phase_locate`（该模块删除） | F13；第 8 条 |
| J5 | 摘录规则逐字照探针第六版的输入（已在原书上复现，见下）：按 `chapter_starts` 分章，去掉标题行；每章正文按换行切段、去首尾空白、丢空段；名字或任一别名所在段前后各 6 段（只在本章内数）；**回忆区的章整章不进**；每章前一行【第N章】，章内不相邻处单独一行「……」，章与章之间空一行；没有一段命中的章不出现 | 10-25 Tracy 附了原书。按这条规则在原书（按上传路径读：`utf-8-sig`、通用换行，`core/text_manager.py:321-324`）上重建，祈灼、谢凛羽两份和探针第六版的输入**逐字一致**（祈灼 135,189 字 sha16 `e77b62056d2783f3`、谢凛羽 194,456 字 `fa0c353490114dfa`），重建脚本 `excerpt_rebuild_check.py` 随 spec 交付。原型阶段 Claude 再用生产的 `build_excerpt` 在原书上建一次，两份都要逐字一致，结果附进原型交付。探针第七版因「……」与原文省略号相混改过标记，那是无章节的书；有章节的书沿用验过的格式 |
| J6 | 删 key_moments | F15：它只供重定位用；重定位删掉后没有读者。风险：探针第六版的提示词里还有 key_moments，删掉后 G6 的挑选可能变化 —— 由 §6 小重放量（第 51 条：风险 → 仍跑的理由：不进卡的字段留着就是死字段，挑选本就交给 PR 2） |
| J7 | 缓存：G6 与 G1 同批先发，其余组等两组都返回 | F6：今天靠 G6 预热整书前缀；G6 换成摘录前缀后若不补，其余 6 组首批并行都按未命中计费。G1 不依赖阶段（F7）、输出最短。预热组按登记表取「FORMAT_GROUPS 顺序里第一个不依赖阶段、也不是 G6 的组」，不写死 |
| J8 | 规则对所有路径一样（分组路径也 G6+G1 同批） | 一条规则一处；分组路径没有前缀缓存，同批先发只差一点时延 |
| J9 | 没有章节的书保留锚点 | 探针第七版（段号）2/4，阿Q 败在挑选不在定位；Shiyu 11:12 定 |
| J10 | 分组路径不换摘录 | F19、F20：分组路径没有回忆区识别、G6 读分析档案，不是原文；能走到这里的只有超长聊天记录和旧书（§0）。现状照旧，由 R3 与现有分组路径测试（`test_distiller_routing.py:892`、`test_longctx_focused_calls_goal.py` C2）守住；本 PR 不改它的起点写法 |
| J11 | 回忆区的章不进摘录；起始章仍落在回忆区（如回忆区未标、或模型写了摘录里没有的章）照旧整卡跳过 | 重建时查实：探针第六版「0 个起点在回忆区」是因为摘录里根本没有第 491–504、508–509 章（不去掉它们，重建出的摘录在第 491 章处与探针输入分叉）。所以去掉回忆章是验过的输入的一部分，不是新加的。回忆区来源用现成的 `RetroRegions.chapters`（F25），路由已经先拿到（F26），只需往下传。回忆区未标（标不了、分组路径）时不去任何章，起点照旧由 `locate_starts` 的 IN_RETRO 兜底 |

## 3. 机制表与规模表（第 11 条）

**这条路径上已有的机制**：

| 机制 | 计时 / 计数从哪开始、前提 | 本 PR 改不改它 | 怎么守 |
|---|---|---|---|
| 前缀缓存（只认前缀） | 第一个带整书前缀的请求返回后才有缓存（F6 的前提） | 改：预热组从 G6 换成 G1 | S5 |
| 回忆区识别（`book_prefix`，F19） | 路由「回忆区」步在蒸馏前；按书缓存，第二次蒸同一本书不调 | 不改；不依赖它预热 | — |
| 组心跳（每组返回一次，`total=len(FORMAT_GROUPS)`，`:2621`、`:2640`） | 每组完成计一次 | 首批变两组：两次 `get` 各发一次心跳，总数不变 | `test_longctx_focused_calls_goal.py:362` `test_g5_start_progress_advances_per_group` 照跑 |
| 组失败整步失败（`_FormatGroupError`） | 任一组 error 即抛 | 首批两组任一失败都抛 | `test_longctx_focused_calls_goal.py:343` `test_g4_any_group_failure_fails_the_whole_step` 照跑；首批的出口多一个组，测试按新批次改 |
| JSON 重修（`required_keys=FORMAT_GROUPS[group]`） | 每组各自 | 不改 | — |
| 关系分批（`rel_prefix=book_prefix(text)`，`:1922`） | 所有组返回后 | 不改 | — |

**规模**（祈灼、谢凛羽两本是唯一实测；其余按上界算）：

| 量 | 实测 / 上界 | 处理 |
|---|---|---|
| 摘录 token | 祈灼 93,105、谢凛羽 133,255（10-23 摘录探针 `excerpt_summary.json` 的 `excerpt_tokens`）| 一次读完的书 ≤ 900k；摘录是原文子集 + 章标记（每章一行）+ 略去行，上界 ≈ 原文 token + 章数 × 约 8；离窗口 1,048,576 还有 ≥ 148k，不另设上限 |
| G6 输出 | 探针第六版 4 次共 3,771 completion token（每次约 950） | 上限 `CARD_MAX_TOKENS = 8192` 不变；输出比今天短（去掉锚点句与 key_moments） |
| 多出的费用 | 探针第六版 4 次 G6 全未命中缓存共 $0.0728（每次约 $0.018） | 每次蒸馏多一次摘录未命中，约 $0.02；整书前缀仍只未命中一次（G1） |
| 时延 | 首批 = max(G6, G1)；今天是 G6 | G1 输出最短，预计不长于今天（未实测，§6 小重放记录各组时长） |

## 4. 改动

| 文件 | 改什么 |
|---|---|
| `core/phase_excerpt.py`（新） | 「有章节的书的人物摘录」只在这里：`chapter_bodies(source_text)`（从 `phase_locate` 原样挪来）；`build_excerpt(source_text, terms, skip_chapters) -> str | None`（`chapter_starts` 为 None 或没有一段命中时返回 None；规则按 J5；`skip_chapters` 由调用方传 `retro.chapters(source_text)`）；`excerpt_prefix(name, excerpt)`；`chapter_body_start(source_text, chapter) -> int | None`（原文坐标，章不存在为 None）。纯计算，不 import 模型相关模块 |
| `core/card_draft.py` | `DraftPhase` 删 `key_moments` 与 `_key_moments_stays_required`；加 `start_chapter: int | None = None`；文档串改。`draft_schema(group, *, start_by=…)`：起点用章号时从 `$defs.DraftPhase` 去掉 `anchor`、反之去掉 `start_chapter`（只此一处），默认按锚点 |
| `core/distiller.py` | ① 维度 L、模板、`:421` 三处的锚点说法改为从一张「起点写法」表取（锚点 / 章号两行，章号那行的措辞照探针第六版 `NEW_ANCHOR_RULE`、`NEW_TPL`、`NEW_LIST`，去掉 key_moments 部分），`format_prompt_after(group, *, start_by=…)` 默认锚点；② `distill_incremental_stream`、`distill_incremental`、`_distill_longcontext(_stream)` 加 `retro: RetroRegions = NOT_DETECTED`；`_distill_longcontext(_stream)` 再加 `aliases`，调用方（F4）传下来；它们算一次摘录，建得出就让 G6 用摘录前缀 + 章号写法 + 用户句「请基于以上摘录为「…」输出模板中的字段」，其余组照旧；③ `_format_groups` 接一个「弧线组输入」参数（前缀、用户句、起点写法；缺省 = 与其余组相同、按锚点），首批发 G6 + 预热组（J7），两组都返回再发其余；④ `_phase_list_note` 按阶段数据写「起始章：第N章」或「起始原句：…」（判断走 `phase_anchoring` 的同一处）；⑤ 删 `locate_phase_starts`（`:1124`）、`_judge_phase_starts`（`:1170`）、`LOCATE_MAX_TOKENS` 与 `:776` 的注释、`phase_locate` import |
| `core/phase_anchoring.py` | `locate_starts`：阶段带 `start_chapter` → `chapter_body_start` → `normalized_starts` 换成规范化位置，None → NOT_FOUND，在回忆区 → IN_RETRO；否则照旧走锚点。`PhaseStart` 记下模型给的是什么（锚点原句或「第N章」），`_SKIP_REASONS`、`_DETAIL_NO_POSITION`、`log_phase_starts` 的文案按它写（不再一律写「锚点」）。`checked_anchors` 的「阶段 1 不用」规则不变，章号同样适用 |
| `core/phase_locate.py` | 删 |
| `web/routers/distill.py` | 删 `:566-567`、`:1218-1219` 两处重定位；`_distill_to_draft`（`:355`）加 `retro` 参数，`:380` 与 `:1175` 两处开流传 `retro=retro`（F26） |
| `scripts/replay_distill.py` | `:480` 开流传 `retro=retro`；删 `:491-493` 的重定位与 before/after 锚点对比；`_start_rows` 记 `start_chapter`；其余判据（F24）不变 |
| `docs/specs/phase-locate.md` | 首行标「已取代」 |
| 测试 | 见 §7 |

结构自检（第 36 条）：

| 问 | 答（附行） |
|---|---|
| 同一条规则写了几处 | 「起点算不算数」：`locate_starts` 一处（F9，J1）。「问模型要哪种起点」：`build_excerpt` 建不建得出一处（J3）。「哪些章算回忆」：`RetroRegions.chapters` 一处（F25），摘录与位置核对共用。「章号 → 原文位置」：`chapter_body_start` 一处；→ 规范化位置：`normalized_starts` 一处（F14）。「起点写法的措辞」：提示词在 distiller `_START_WORDING`、结构在 `card_draft._START_SCHEMA`，两表分工、各一处 |
| 调用方是否被迫知道不该知道的事 | `_format_groups` 不知道章节，只收「弧线组输入」；路由不知道起点怎么定（重定位删了）；`card_from_draft` 不变（F17） |
| 加一种情况要改几处 | 加一种起点写法（如段号）：起点写法表加一行 + `locate_starts` 加一个分支 + 摘录模块加一种编号。三处各管一件事：问法、换算、编号 |

## 5. 提交段

| 段 | 内容 | 过哪几条 |
|---|---|---|
| 0 | 目标检查原样入库（S1–S6 xfail strict） | R1、R2 绿 |
| 1 | `phase_excerpt.py`（含挪来的 `chapter_bodies`）、`DraftPhase.start_chapter`、`locate_starts` 按章分支与文案 | S3a、S3b |
| 2 | 提示词起点写法表、`draft_schema` 分模式、一次读完传别名、回忆区与摘录（路由与 replay 传 `retro`）、弧线组输入、G6+G1 同批、阶段列表 | S1、S2、S4、S5 |
| 3 | 删重定位（distiller、路由两处、`phase_locate.py`、key_moments）、replay 脚本、旧 spec 标取代、受影响测试 | S6 |

## 6. 真模型小重放（第 34 条）

**问的是什么**：生产链上，有章节的书的起点是不是都落在正文里（定位）。不问挑得对不对（PR 2 的事）。

**怎么跑**：段 3 完成后，Tracy 本地用更新后的 `scripts/replay_distill.py`，祈灼、谢凛羽各 `--runs 2`（同一份书，属主自己的配置）。

**判定线（跑前写死）**：4 次运行，`_checklist` 第 1 项「起点齐全、严格递增」与第 2 项「起点都不在回忆区 / 章内回忆」全部为「过」→ 通过；任一次不过 → 不通过，不重跑挑结果。第 3 项（对真值）只记录。

**为什么预计能过（≥80%，第 47、51 条）**：生产建出的摘录与探针第六版逐字相同（J5）。探针第六版同一摘录、同一模型 4 次：16 个起点全是摘录里有的章、0 个在回忆区、按生产规则（阶段 1 归 0）4 次都严格递增 —— 祈灼 `[0,12,122,335]`、`[0,12,183,337]`，谢凛羽 `[22→0,23,29,106]`、`[22→0,29,102,481]`（`chapters-out/report.txt`）。风险：①删 key_moments（J6）；②生产走 `chat_stream_long`、探针走 `async_chat`；③谢凛羽第二次的 481 离日记区（491 起）只差 10 章。②只换调用方式不换提示词；①③由本次重放量出来。

**费用**：每次蒸馏约等于今天一次蒸馏 + $0.02（§3）；4 次。Tracy 跑前按今天的单次费用报总数。

**第 3 项参考**：探针第六版第 3 项 1/4（祈灼 run2 第 3 阶段起在 183，真值 122）。文献里同类任务人与人精确一致不高，第 3 项本就不作门槛（第 48 条）。

## 7. 测试与替身（第 35、40 条）

改调用方式：**不改**（仍是 `_chat_accounted(…, stream=True)` → `chat_stream_long`）。改的是批次与 G6 的前缀，所以按「谁假设了批次、谁录了 G6 提示词」扫。

扫描命令与原始输出（全量，不抽样）：

```
$ git grep -l -E "phase_locate|locate_phase_starts|key_moments|KEY_MOMENT_START" -- tests
tests/fixtures/longctx_focused_calls_f1_baseline.json
tests/test_arc_key_moments_goal.py
tests/test_card_redraft_goal.py
tests/test_distill_diagnostic_logs_goal.py
tests/test_distill_draft_persist.py
tests/test_distill_task_api.py
tests/test_identify_failure_channels.py
tests/test_longctx_focused_calls_goal.py
tests/test_phase1_anchor_not_checked_goal.py
tests/test_phase_anchoring.py
tests/test_phase_locate_goal.py
tests/test_replay_distill_goal.py

$ git grep -n -E "Barrier\(" -- tests
tests/test_arc_phase_select.py:167:    barrier = threading.Barrier(len(PHASE_DEPENDENT_GROUPS), timeout=2)   # 依赖组一起等破障
tests/test_chroma_shared_client_goal.py:52:    barrier = threading.Barrier(2)
tests/test_distill_usage_accounting.py:368:        barrier = threading.Barrier(3)
tests/test_distiller_routing.py:913:        barrier = threading.Barrier(len(rest), timeout=2)

$ git grep -n -E "_distill_longcontext(_stream)?\(" -- core web scripts tests
core/distiller.py:1912:    def _distill_longcontext(self, text: str, character_name: str) -> CharacterCard:
core/distiller.py:1932:    def _distill_longcontext_stream(self, text: str, character_name: str):
core/distiller.py:2431:            card = self._distill_longcontext(text, character_name)
core/distiller.py:2725:            yield from self._distill_longcontext_stream(text, character_name)
tests/test_arc_phase_fields_unit.py:661:    Distiller(llm=_LLM(), config_path=None)._distill_longcontext(src, name)
tests/test_distill_usage_accounting.py:151:        Distiller(_FormatGroupReturnUsageLLM([]), config_path=None)._distill_longcontext(
tests/test_distill_usage_accounting.py:449:        drafts = [t for t in d._distill_longcontext_stream("全文", "角色") if isinstance(t, str)]
tests/test_longctx_focused_calls_goal.py:241:    return Distiller(llm=llm, config_path=None)._distill_longcontext(text, name)
tests/test_longctx_focused_calls_goal.py:245:    return list(Distiller(llm=llm, config_path=None)._distill_longcontext_stream(text, name))
```

| 测试 | 为什么受影响 | 怎么改（契约变了，不是为了变绿） |
|---|---|---|
| `test_distiller_routing.py:892` `TestFormatGroupsRunInParallel` | `Barrier(len(rest))` 假设 G6 之外的 6 组同批；G1 进首批后第二批只有 5 组 | 第二批的组从登记表算（除 G6 与预热组）；「先发」判据改为第二批每组的事件晚于首批两组 |
| `test_longctx_focused_calls_goal.py:249` `test_g1_one_pass_calls_are_grouped_arc_first` | 断言 `groups[0] == "G6"`、其余组都在 `end:G6` 之后 | 改为首批是 {G6, 预热组}、其余在两者之后；文本无章节，整书前缀断言不变 |
| `tests/fixtures/longctx_focused_calls_f1_baseline.json` | 逐字录了 G6 提示词（含 key_moments） | 段 2 按新期望重录，`baseline_commit` 写重录用的提交 |
| `test_arc_phase_select.py:167` | 依赖组同批 | 不用改（依赖组仍同批）；照跑 |
| `test_phase_locate_goal.py` | 测被删的模块 | 删；其中守「有章节 + 锚点」的用例若守的是 `locate_starts` 行为，挪进 `test_phase_anchoring.py` |
| `test_phase1_anchor_not_checked_goal.py` | 从 `test_phase_locate_goal` import 原文与替身；g2 测重定位 | 原文挪进本文件；g2 删（重定位没了），g1/g3/g4/g5 保留 |
| `test_arc_key_moments_goal.py` | 测 key_moments | 删 |
| 其余 7 个（扫描结果里剩下的） | 草稿 / 替身里带 key_moments 或重定位步骤 | 去掉 key_moments 字段与重定位断言；不改别的断言 |
| `test_replay_distill_goal.py` | 脚本改了 | T6 锁的常量不变；去掉 before/after 锚点相关断言 |

替身与生产一致：新增的目标检查替身 `chat_stream_long` 每条路径都 `return _USAGE` 收尾，不改写 system（S5 的 `Barrier(2, timeout=2)` 破障时抛错，生产同样以组失败收场）。沙箱带 `ulimit -v 4000000` 跑过（§1），3.5 秒结束。

本地只跑：`tests/test_phase_start_chapter_goal.py` + 上表文件 + `test_phase_anchoring.py`、`test_card_draft.py`、`test_distill_usage_accounting.py`。全量看分支 CI（第 15 条）。

## 8. S0（第 18 条）

执行方开工前逐条复核 §2 的 F1–F26：打开所写的行，贴出实际读到的那一两行；有一条对不上就停下报告，不自己改设计。

## 9. 发出前的判断清单（第 7b 条）与未做的事

- §2 F1–F26 每条都对应本轮读过的行（读过的范围：`core/distiller.py` 280–300、365–425、444–523、754–777、1100–1180、1903–1946、2420–2440、2565–2650、2690–2735、2930–2955；`core/phase_anchoring.py` 76–310；`core/card_draft.py` 145–234、464–500；`core/chapters.py` 1–40；`core/phase_locate.py` 40–70；`core/schema.py` 154–163、330–350；`core/quotes.py` 55–70；`core/retro_regions.py` 40–140；`core/text_manager.py` 199–252、313–327；`web/routers/distill.py` 355–385、540–575、1160–1226；`scripts/replay_distill.py` 1–40、345–500；`core/length_budget.py` 常量行）。
- §4 里「起点写法表」「弧线组输入」「`chapter_body_start`」「`start_by` 参数」是本 spec 的设计，代码里还没有；名字执行方可按实际代码取，职责不变。
- **已核实（10-25）**：摘录规则。用 Tracy 附的原书重建，两份都和探针第六版的输入逐字一致（J5）；顺带查出探针输入去掉了回忆章（J11），已写进规则。
- **原型未做**：第 2 条要求写代码前在原型上跑通目标检查。本 spec 只在 `main` 上验了目标检查是红的、红得对。原型（沙箱里按 §4 改一遍、10 条全绿、受影响测试全绿）是下一步，做完再交本地。

## 补充（原型实施）

1. 段 2、段 3 对调：先删重新定位（`locate_phase_starts` / `key_moments`），再加章号（`start_chapter`）。
2. 章节正文边界放在 `core/chapters.py`，不放 `phase_excerpt.py`。
3. 变异驱动的 4 处锚点跟着改了：M5a、EMPTY 两处、M17。
4. 草稿阶段多了 `start_chapter` 字段；锚点书（`StartBy.ANCHOR`）下它为 `null`。
5. 审计问题 1–6 的处理：
   - 问题 1（非递增原因硬编码「锚点」）：`_skip_reason` 的文案改从 `_START_NOUN` 取，名词用被破坏那一对里后一个阶段的写法；锚点模式文案逐字不变；S3b 改断「起始章位置不严格递增」。
   - 问题 2（`_arc_importance` 不从写法表取）：`_StartWording` 加 `field` 字段，`_arc_importance` 从表取；`:265` 的注释与代码一致。
   - 问题 3（自检表「模板 / 规则 / schema 都从它取」写错）：该格改为「提示词在 distiller `_START_WORDING`、结构在 `card_draft._START_SCHEMA`，两表分工、各一处」。
   - 问题 4（§4 ④ 例文与实现不同）：例文改为「起始章：第N章」，与 `_START_WORDING[CHAPTER].note` 一致；S4 断依赖组提示词含「起始章：第3章」。
   - 问题 5（夹具 `baseline_commit` 不可审计）：在当前分支重录，`baseline_commit` 写重录提交；`group_calls` / `tail_frame_kinds` / `draft` 与改前逐字相同。
   - 问题 6（重新出卡用例的断言恒真）：用例里给存下的草稿放独有 `background`，断言出卡取草稿值且不等于手改值；共用 `_draft()` 未动。
