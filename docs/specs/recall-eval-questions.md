# recall-eval-questions：5 本书约 200 条召回题（召回升级 0d）

- 基线：main `a9b830ed`（2026-10-25 读，#207 合并后）。行号均指此提交。
- 共享文档「召回做法调研 · 2026」→「评测集重新设计」「修复总方案」0d 行、「评测选书」「评测改在本地跑」「CoverSynth PDF 全文已读」。
- 本 PR 只做一件事：**题库工具**（出题锚点、四道关、多本书评测入口）。检索本身（聊天链路①–⑥）一行不改；产品代码只动 §4 列出的三处。
- 分支：从最新 main 新建 `feat/recall-questions`。原文、锚点、题目、核查记录、**三份照抄的提示词**只留在本地 `e2e/scratch/recall/`（`.gitignore:283` 的 `/e2e/` 已忽略）与 SG 容器的临时目录，不入库。
- **在哪跑（⑫）**：书和祈灼的卡在 **SG**（Shiyu 的账号 `3996bd7f23a34311`）。读库、生成锚点、摸底、核查、评测都在 **SG 容器**里跑；写代码、起草题目在本地。SG 上跑的是**本分支代码的完整副本**，放在容器临时目录，**不写进 `/app`**（§7「SG 上怎么跑」）。
- 交付物：本 spec、目标检查 `test_recall_synth_goal.py`（23 个用例）、本地提示词文件 4 个（`prompts/`，只放本地）。**不附实现**（返工经验 52）。
- 文献：CoverSynth（arXiv 2610.15409v1，Scale AI），PDF 由 Shiyu 提供，全文已读；下文引原文均注页码。

**Shiyu 已定**

| # | 决定 | 时间 |
|---|---|---|
| ① | `_scene_index_job` 改名公开 `build_scene_index`，函数体不动 | 10-26 11:09 |
| ② | 「词」按字二元组，一处定义（`char_bigrams`）；只当诊断，不当关卡（见 ⑥） | 10-26 11:09 / 11:26 |
| ③ | S0 缺任何一样就停，不自动补；祈灼用阶段已按新卡（3 个阶段）重分配的 `qizhuo.jsonl` | 10-26 11:09 |
| ④ | 报告按书列出软关结果；丢的题不许补成别的题 | 10-26 11:15 |
| ⑤ | ~~软关修 2 次不过也丢；阈值用祈灼题标定~~ **作废**（11:26，见 ⑥） | — |
| ⑥ | 按原文：词重合 0.5 没有定义 → 不当关卡；软关不进录取条件（式 2）→ 修 2 次不过的**录取并打标记**；防照抄改用附录 A.5 的模型评审 | 10-26 11:26 |
| ⑦ | A.6 通过 = §3.4 列的核查项；`necessary` 只报不卡 | 10-26 11:35 |
| ⑧ | A.6 的 `{intent_criterion}` / `{intent_field}` 照抄表 4 的 Factual lookup 行，不留空 | 10-26 11:35 |
| ⑨ | ~~A.4 的 `{persona}`~~ 随 ⑮ 作废 | 10-26 11:35 |
| ⑩ | 先用祈灼 40 条人工题摸底 A.4 / A.5 的误判率（A.4 第 1 条「refers deictically to an unseen passage」可能误判聊天里的「那天」「他」） | 10-26 11:35 |
| ⑪ | 提示词不进仓库，spec 记哈希和页码（PDF 无许可证声明，按无授权处理） | 10-26 11:35 |
| ⑫ | 全在 SG：祈灼的 3 阶段卡只在 SG（d541a44f3679）；**推翻 10-25 20:00 的「评测在本地跑、线上零影响」**。书不跨节点同步（`web/routers/inter_node.py` 只有 dm / card / invite-code / user 等路由），只影响 SG | 10-26 13:33 |
| ⑭ | 评测单位 =「书 × 角色」。识别结果里「主要」标签一本十几个，不用它选人：每本对名单里每个人物，用名字 + 别名统计「全书均分 60 段中出现在几段」（并列看总次数），取前 2。恶女训狗无数例外：祈灼（43 条人工题，有卡，唯一算越界率）+ 云绮（排名第 1，生成题，只算召回）——祈灼在该书排第 12，只取他会漏掉最主要的人物（Shiyu 10-26 14:14 同意）。**S1 前**列出入选 9 人的全部别名、各自原文出现次数、是否与名单里别人的名字 / 别名重合，停下由审计方据数据定锚点用哪些称呼（别名里混有「母亲」「太太」这类泛称）。理由：锚点按这 60 段抽，段数决定能不能出够题（规则是我们定的，无文献）。每人约 20 题、30 个锚点，总数约 220。S0 贴完整排名表，Shiyu 确认后才往下 | 10-26 14:02 |
| ⑮ | **去掉 A.4**，录取 = g_ground ∧ g_verify（A.6）；A.5 仍为软关。依据 S6a 摸底（SG，10-26）：A.4 的前提是「给看不见原文的文件搜索助手提问」，聊天问句天然带上文指代，前提不成立；43 条（含 3 条寒暄）共 86 次判定 39 次不过，**全部** `self_contained=false`（理由均为指向看不见的上文：当初、那晚、你心里），其中 19 次同时 `realistic=false`；两次一致率 0.698。剔除寒暄（`should` 为空）后的数字由执行方重算，结论不变。**偏离论文式 2，由摸底数据推翻**。代价：没有任何关再检查「像不像真实用户」，靠起草规则保证，列为局限 | 10-26 15:01 |
| ⑯ | ~~别名表审查~~ 作废（⑱）：锚点只用角色本名找位置，不用别名，泛称问题不存在 | 10-26 15:22 |
| ⑰ | ~~A.6 摸底~~ 作废（⑱）：A.6 误判的后果只是丢题、不会混进坏题；整批跑时按角色报各关丢弃数 | 10-26 15:22 |
| ⑱ | **精简流程**（Shiyu：不要一层层停顿）：起草改由 DeepSeek 脚本批量完成（CoverSynth §5.2 本就是同一模型起草与核查，第 8 页）；一条命令 `generate` 在 SG 上跑完「锚点 → 起草 → g_ground → A.5 → A.6 → 修 2 轮」，中途不停，只在出错时停。代价：起草与核查同一模型，少了交叉把关。起草提示词 `DRAFT_PROMPT` 是**我们自己写的**（论文附录没给起草提示词），内容即 §5 第 2 步的规则 | 10-26 15:22 |
| ⑲ | 锚点称呼（按 10-26 别名表数据定）：别名质量不可靠（如云绮的别名里混有「小姐」1396 次、「公主」612 次，以及别人的名字「云汐玥」「云肆野」），**一律只用本名**；唯一例外林黛玉——本名「林黛玉」只出现 284 次，她在原文里主要叫「黛玉」（1375 次，含「林黛玉」），改用「黛玉」。即：云绮、宝玉、黛玉、顾昀、长庚、魏无羡、蓝忘机、简隋英、李玉（祈灼用人工题，不取锚点）。共 **9 个生成题角色**。锚点不足 30 的角色如实报 | 10-26 15:40 |
| ⑬ | 书单：恶女训狗无数（女频古言，43 条人工题，唯一算越界率）、魔道祖师、杀破狼、**红楼梦**（演示账号 offerPass 的内容；半文白文风）、你却爱着一个傻逼（现代背景，约 46 万字）。替换 10-25 名单里的《溺爱成瘾》：它是纯爱、不是记录里写的「现代言情」，且 4 本古风缺现代背景 | 10-26 13:43–13:52 |
| ⑳ | **评测无卡行只建 eval 场景集合**：`eval_recall --suite` 的无卡行调 `build_eval_scene_index`，只建 `scenes_eval_{text_id}_c{n}`，**不建 `text_` 集合**（不整本嵌入）；会话检索按 `scenes_{card_id}` → `text_{text_id}` 的顺序装载，场景集合在即命中（`core/indexing_service.py` `_session_candidates` / `_load_session_rag`）。编号 `n` **只数无卡行**，有卡行（祈灼）不计数。出自 PR #208 独立复核 P1 与「无卡也去建原文集合」两条 | 10-26 19:30 |
| ㉑ | **起草问法补三条规则**（`core/recall_synth.py` 的 `OCCASION_RULE` / `YOU_RULE` 与 `DRAFT_PROMPT` 的片段自足一条）：① 点明场合——带上在场的人、地点或刚发生的事，使问题只指窗口里这一次，不指书里多次发生的同类事；多轮提示词要求 turn1 同样交代场合；② 「你」恒指问答角色，问句里的代词与亲属称谓按角色本人视角写，原文是别人说的话时不把其「我」「我哥」改成「你」「你哥」；③ 片段自足（只加在 `DRAFT_PROMPT`，多轮沿用原片段）——单凭 `should` 片段就能答：问谁说的，片段里须看得出说话人；问某类东西（罪名、原因、名字），片段须恰是那类东西。①② 在 `DRAFT_PROMPT` 与 `MULTITURN_PROMPT` 都加；A.5 / A.6 照抄提示词及其哈希不动。依据：10-27 人工抽查 run3 模型题 20 道——场合不明 5（如「你那天骑上马后跟着谁一起走的？」）、人称搞反 1（问简隋英「你哥……」，原句是简隋林说的）、片段答不了 2（「多谢，还请诸位尽力而为」看不出说话人；「背信弃义有瘾」不是罪名）。生效范围：之后的 `generate` / `multiturn`；**run3 题库不重出**，保证 0e / r1 / r2 可比 | 10-27 |

## 0. 环境硬规则（执行方必读）

- 禁止启动、重启、停止 Docker Desktop / WSL / 任何容器；禁止用 cmd.exe / PowerShell 启动程序。测试库由 Shiyu 起好，不在就停下报告。
- 任何命令退出码非 0/1（如 127）立即停，不重试、不排查系统。
- 不跑全量；本地只跑 §8 列出的 5 个文件。标 ☁ 的测试（会碰 LLM 替身）不在本地跑，由复核方在沙箱带内存上限跑。
- 真实 DeepSeek 调用只在 S6；S6a 摸底后**停下等 Shiyu**，他放行才做 S6b。
- SG 只做：只读查库、在容器临时目录跑本分支脚本。**禁止**往 `/app` 写文件、重启容器、改库（0d 不建索引；建索引是 0e 的事）。

## 1. 目标与目标检查（第一个提交，`xfail(strict=True)`）

**目标**：在 Shiyu 本地 5 本书上产出约 200 条召回题（祈灼 43 条人工题 + 其余 4 本各约 40 条），每条过 g_ground、A.6 两道硬关（A.4 由摸底推翻，⑮）；评测脚本一次跑完 5 本，按书分组报 R@3，另给总体。

**目标检查** `tests/test_recall_synth_goal.py`（随 spec 交付，纯函数、无 IO；夹具带省略号、中文引号、换行、重复句——返工经验 54）：

| # | 检查 | 用例 |
|---|---|---|
| 1 | 锚点：每层至多 1 个、均匀书至少 55/60 层有；只前半本提到主角 → 后半本无锚点；中段没提到 → 中段空着不借；规范化起止与原文一致 | `test_anchors_*`、`test_gap_in_middle_leaves_strata_empty`、`test_anchor_norm_bounds_match_source` |
| 2 | 关 g_ground（硬）：窗口外 / 全书多处 / 含省略号 / 短于 10 字 → `ground_fail`；规则与人工标注自检同一份 | `test_ground_gate`、`test_ground_rules_are_the_annotation_rules` |
| 3 | 不用 A.4：`judge` 不收 `realism`、登记表里没有 A.4（⑮） | `test_no_realism_gate` |
| 4 | 措辞评审（软，A.5）：`searchable=true` 在第 1、2 次触发修改并带回 `suggestion`；第 3 次仍是 → **录取并打 `own_words_flag`**，但仍要过 A.6；评审结构不对 → `critic_unparsed`；关 1 不过时不跑评审 | `test_own_words_critic_triggers_repair_not_rejection`、`test_flagged_draft_still_needs_hard_gates`、`test_critic_unparsed_is_neither_pass_nor_discard`、`test_critic_skipped_when_ground_fails` |
| 5 | 关 g_verify（硬，A.6）：`answerable`、`sufficient`、`per_doc` 全真、`leak` 假、`intent_match` 真才过；`necessary` 为假不影响；`per_doc` 的键不等于证据 id 集合、或缺字段 → `verify_unparsed` | `test_verify_gate_follows_section_3_4_list`、`test_verify_unparsed` |
| 6 | 修复上限只作用于硬关；结果按「录取 / 录取带标记 / 丢弃（按丢在哪道硬关）」分开数 | `test_repair_cap_on_hard_gates`、`test_outcome_counts_split_hard_discards_and_soft_flags` |
| 7 | 不许补题：草稿 id 不是锚点 id、或是已丢弃的 id → 拒收 | `test_no_backfill` |
| 8 | 提示词：内容与登记哈希不符 → 拒用；占位只替换登记过的名字、多给少给都报错、模板里的 JSON 花括号原样保留；意图槽带表 4 原文 | `test_prompt_must_match_registered_hash`、`test_render_keeps_json_braces_and_requires_exact_placeholders`、`test_intent_slots_carry_table_4_row_verbatim` |
| 9 | 诊断重合：问句 vs **答案片段**（A.5 / A.6 看到的同一份 `{quotes}`），分母 = 问句；0.1 一档分布；**重合再高也不影响状态** | `test_overlap_is_against_gold_quotes_and_denominator_is_question`、`test_overlap_histogram_bins`、`test_overlap_never_decides_status` |
| 10 | 多本书：`overall` 按全部行算，不是各书比例的平均 | `test_suite_overall_counts_rows_not_books` |

目标检查只守规则，不守「题出得好」（返工经验 ⑥-5）。题目质量靠 A.5、A.6 两道模型关、S6a 摸底与 S6b 报告的数字；其他 4 本没有人工抽查（Shiyu 没读过），如实写进 PR。

## 2. 已查实的事实（含「对目标的影响」）

| # | 事实 | 坐标（读过的行） | 对目标的影响 |
|---|---|---|---|
| F1 | 评测只认卡：`load_inputs` 用 `get_card_owned(card_id, user_id)`；`run_eval` 里 `views = {case["phase"]: arc_view(card, case["phase"]) ...}` | `scripts/eval_recall.py:60-95`（:90） | 无卡的书要另一种输入：按 `text_id` + 主角名，阶段为空、窗口 None |
| F2 | 场景索引作业是私有 `_scene_index_job`：先 `_build_text_collection` 再 `SceneIndexer().index_scenes(...)`，同步；`schedule_scene_index` 只 `self._run_in_background(...)`，无完成信号。旧名全部引用（`grep -rn _scene_index_job --include=*.py .`，含 tests）：`core/indexing_service.py:268` 定义、`:336` 唯一调用、`core/scene_indexer.py:81` docstring；测试与结构锁零引用 | `core/indexing_service.py:268-287`、`:314-338` | 评测要等索引建完 → 改名公开，三处一起改 |
| F3 | 会话检索按 `scenes_{card_id}` → `text_{text_id}` 装载；无 key 返回 None | `core/indexing_service.py:88-90`、`:221-238` | 无卡的书每个角色用 `card_id = f"eval_{text_id}_c{n}"`（⑭），与聊天同一路 |
| F4 | 场景集合每段 `"characters": characters_tag([character_name])` | `core/scene_indexer.py:137` | 无卡的书传主角名即可（D7 照旧，第 5 轮修） |
| F5 | 场景只存 `docs.append(scene[:800])` | `core/scene_indexer.py:133` | 锚点**必须从原文按位置取**，否则题目集中在每段前 800 字 |
| F6 | `cached_characters(storage, text_id, user_id)` 只读缓存不发 LLM；`aliases_for` 返回 `c.get("aliases") or []` | `core/character_roster.py:90`、`:118-127` | 名单为空停下报告，不调识别（花钱） |
| F7 | `get_user_llm(user_id)` 只认用户自己的 key；DeepSeek 关思考由适配器统一加 | `web/deps.py:100-136`；`adapters/llm_adapter.py:419-423`、`chat` :972-1012 | 两道模型关都走 Shiyu 本地账号自己的 key |
| F8 | LLM 回复转 JSON 的出口 `loads_llm_json` | `core/llm_json.py:90` | 两道模型关的解析都复用它 |
| F9 | main 上没有字二元组函数：原 `_bigrams` 在 `core/phase_locate.py`，#206 的 `e3e626e4` 删了整个文件 | `git show e3e626e4^:core/phase_locate.py` :60-63 | `core/quotes.py` 新增 `char_bigrams`（诊断用；第 4 轮 BM25 用同一个） |
| F10 | 片段规则全在 `validate_cases` 的循环体里，结果只以 `AnnotationError` 的拼接消息抛出 | `core/recall_eval.py:31-63` | 抽出 `fragment_problems`，人工标注自检与关 g_ground 共用；不许解析异常消息 |
| F11 | 证据 id 白名单只有 `recall-baseline-qizhuo` | `tests/perf/evidence_writer.py:94-96` | 本 PR 不出基线；0e 再登记 |
| F12 | 评测用用户原话，agent 模式实际检索路由写的词 | `core/agent/tools.py:86` | 本评测不覆盖改写，第 3 轮加一档（返工经验 59） |
| F13 | 结构锁：`in source_norm`、`re.finditer(`、`_ELLIPSIS.split(` 在全 `core/` 只许在 `core/quotes.py`；`.find(` 不许在 scene_indexer / rag / quotes / arc_view | `tests/test_phase_anchoring.py:598-616`；`tests/test_arc_phase_select_locks.py:146-151` | `recall_synth.py` 找主角名用 `str.find`，不写那三种写法 |
| F15 | 上传只存书：`_run_upload_task` 只把任务标成完成，不识别人物、不建索引；人物名单由 `POST /api/distill/identify` → `resolve_characters` 识别并缓存 | `web/routers/text.py:62-90`；`web/routers/distill.py:739` | S0 名单为空的书 → 停，告诉 Shiyu 在网站上对那本书点「识别角色」 |
| F16 | 祈灼的卡绑定原文 `3335472a438b`（0b 记录）；Shiyu 10-26 又上传了一份《恶女训狗无数》 | 共享文档 0b 记录 | 祈灼一律用**卡绑定的那份原文**；新传的那份不用（43 条标注是对那份原文校验过的） |
| F14 | 三份提示词模板里都有 JSON 花括号（如 `{"searchable": true|false, ...}`） | 本地 `prompts/*.txt` | **不能用 `str.format`**；只替换登记过的占位名 |

## 3. 复用清单（函数体原文 + 实跑）

| 复用 | 决定行为的原文 | 实跑 | 输出 |
|---|---|---|---|
| `core.quotes.normalize` | `folded = _T2S.convert(str(s))`；`"".join(ch for ch in folded if not ch.isspace() and ch not in _DROP_CHARS).translate(_VARIANT_FOLD)`（`core/quotes.py:50-51`） | 片段「魏无羡把笛子横在唇边，吹了一支不成调的曲子，蓝湛站在廊下听着，一言不发。」；重合 = 问句二元组 ∩ 片段二元组 / 问句二元组 | 「你还记得魏无羡在廊下吹笛子那次吗」0.33；照抄原句 1.00 |
| `core.quotes.intervals_in_normalized` | `return [(m.start(), m.start() + len(n)) for m in re.finditer(re.escape(n), source_norm)]`（:103-112） | 0c 已实跑（`docs/specs/recall-eval-ruler.md` §3） | 全部出现位置，宽度 = 片段自身 |
| `core.quotes.normalized_starts` | `return [len(normalize(text[:r])) for r in raw_starts]`（:59-65） | 锚点规范化起止 | `test_anchor_norm_bounds_match_source` 守 |
| `recall_eval.validate_cases` | 见 F10 | S0 第 4 条用祈灼 43 条对本地原文跑一次 | 执行方贴原样输出 |
| 三份提示词 + 占位替换 | 本地 `prompts/*.txt` | 审计方沙箱：三份都过哈希，按 ⑧⑨ 填占位后无残留占位；A.6 末尾为 `"leak": bool, "intent_match": bool, "notes": "<short>"}` | S0 执行方按 §7 复跑 |

## 4. 设计

| 块 | 文件 | 职责（只此一处） |
|---|---|---|
| 片段规则 | `core/recall_eval.py`：抽出 `fragment_problems(case_id, key, i, piece, source_norm, before) -> list[str]`；`validate_cases` 汇总它的结果再抛 | 「一个标注片段合不合规」只写这一处 |
| 多本书汇总 | `core/recall_eval.py` 新增 `aggregate_suite(rows_by_book)` | 每本 `aggregate` + 全部行一起 `aggregate` |
| 字二元组 | `core/quotes.py` 新增 `char_bigrams(s)` | 先 `normalize` 再取相邻两字 |
| 公开建索引 | `core/indexing_service.py`：改名 `build_scene_index`，函数体不动；`:336` 与 `core/scene_indexer.py:81` 同步 | 后台调度与评测共用 |
| 出题规则（纯函数、零 IO） | 新增 `core/recall_synth.py` | `sample_anchors` · `ground_problems`（调 `fragment_problems` + 在窗口内）· `PROMPT_SHA256` / `PLACEHOLDERS`（只登记 A.5、A.6、表 4；⑮） / `check_prompt` / `render` / `intent_slots` / `quotes_block`（`[e1] …` 格式，A.5 与 A.6 共用；A.6 原文要求「id in brackets」）· `verify_ok` · `judge(draft, anchor, source_norm, *, critic, verdict, attempt)` · `ledger_problems` · `outcome_counts` · `lexical_overlap` / `overlap_histogram`（诊断） |
| 读书（IO，一处） | `scripts/eval_recall.py` 新增 `load_book(storage, entry) -> Book`（原文、主角名、别名、可选卡） | 两个命令行都从它取书 |
| 书目（配置，一处） | `e2e/scratch/recall/suite.json`（本地一份，随脚本拷进 SG 临时目录）；**每行 = 一本书里的一个角色**（⑭）：每本 `{book, title, user_id, text_id, character, cases, card_id?}` | 所有命令只读这一份；`title` 用于 ⑨ 的人设句 |
| 出题命令行 | 新增 `scripts/recall_questions.py`：`probe` / `generate` | `probe`：已用于 S6a。`generate`：读 `suite.json` → 对每个生成题角色 `sample_anchors(原文, [⑲ 的称呼], k=30)` → 每个锚点用 `DRAFT_PROMPT` 让 DeepSeek 起草（JSON：`query`、`should` 或 `skip`）→ `ledger_problems` → g_ground → A.5 → A.6；未过的把 `problems`（A.5 的 `suggestion` / A.6 的 `notes` / 关 1 的原因）填回 `DRAFT_PROMPT` 的反馈位重写，至多 2 次 → 写 `verdicts.jsonl`、`<角色>.jsonl`（评测格式，带 `own_words_flag`）→ 打印每个角色的 `outcome_counts`、skip 数、各关退回后修 1 / 2 次通过数、人工题与模型题重合分布、token、耗时 |
| 评测命令行 | `scripts/eval_recall.py --suite`（读 `suite.json`） | 有卡照现在；无卡：每个角色 `build_scene_index(card_id=f"eval_{text_id}_c{n}")`（n = 该书第几个角色；集合名用编号不用中文名，Chroma 集合名字符限制 0e 前核实） 后取会话检索，窗口 None，`phase` 必须为 null；输出 `aggregate_suite(...)`；旧单卡参数不变 |
| 起草提示词 | `core/recall_synth.py` 新增 `DRAFT_PROMPT`（占位 `{character}`、`{title}`、`{window}`、`{feedback}`，登记进 `PLACEHOLDERS`，走同一个 `render`；我们自写，入库，不核哈希） | §5 第 2 步的规则逐条写进去 |

**关卡顺序**：g_ground（免费、确定）→ A.5（软）→ A.6（硬），同论文 §3.4 的顺序；A.4 不用（⑮）。

## 5. 出题流程

1. **锚点**（程序）：每本 60 个，比目标 40 条多留丢弃余量（推断）。
2. **起草**（DeepSeek，`DRAFT_PROMPT`，⑱）：每个锚点一条，或返回 `skip`（窗口里没有可问的事）。用户**跟这个角色聊天**时会说的话，第二人称；问他经历过、说过、做过的事；不出现「原文 / 第几章 / 小说 / 作者」；`should` 从窗口逐字摘 10–40 字；不把 `should` 的词搬进问句。修改时把上一轮各关的 `problems` 填进 `{feedback}`。
3. **g_ground**（硬，程序）：`fragment_problems` 全过 + 落在锚点窗口内。
4. ~~g_real（A.4）~~：不用（⑮）。
5. **措辞评审**（软，A.5 照抄）：`{question}`、`{quotes}` = `quotes_block(should)`。`searchable=true` → 带 `suggestion` 退回修改；修 2 次仍是 → 录取并打 `own_words_flag`。
6. **g_verify**（硬，A.6 照抄）：`{quotes}` 同上；`{intent_criterion}` / `{intent_field}` = `intent_slots(表 4 Factual lookup 行)`。通过 = `answerable` ∧ `sufficient` ∧ `per_doc` 全真 ∧ ¬`leak` ∧ `intent_match`。
7. **修复**：每一轮判定（含硬关未过的 id 与原因）写进 `verdicts.jsonl`（每行带 `id`、`gate`、`problems`，与 §4 一致；不另设 `repair.jsonl`），只改未过的；最多修 2 次，第 3 次仍不过 → `discarded`。丢了的锚点空着，不补。`*_unparsed` 不计次数，重调一次仍不行记 `discarded:parse` 并报数。
8. **祈灼**：`qizhuo.jsonl`（3 阶段版）43 条直接进套件，不过模型关（人工已核）；越界率只在祈灼上算。

## 6. 规模表

| 对象 | 上限 | 出处 | 处理 |
|---|---|---|---|
| 书 | 5 | ⑬ | `suite.json` 5 项 |
| 锚点 / 角色 | 30（`sample_anchors(k=30)`） | 每人目标 20 + 余量（推断） | 不足 20 如实报 |
| 摸底调用 | 约 40 条 × 2 关 × 2 次 = 160 | ⑩ + 返工经验 34 | 打印 token |
| 出题调用 | ≤ 270 锚点（9 × 30）× 3 次 × 3 调（起草 + A.5 + A.6）= 2430（上限；g_ground 不过不调后两关，A.5 不过不调 A.6） | 修复上限 2 | 打印 token |
| `normalized_starts` | 每本 ≤ 180 个位置，每个对前缀整段规范化 | `core/quotes.py:65` | 120 万字的书可能要几十秒（推断，S6 记实际耗时） |
| 评测查询 | 约 200 | 0d 目标 | 每条一次查询嵌入 |
| 首次建索引 | 0d 不建；0e 时 4 本无卡书各整本嵌入一次（建前先查 SG 内存） | F2 | 指纹相同不重建 |

## 7. 执行步骤（每步一个提交）

- **S0（只读，几分钟）**
  1. Test-Path + SHA256 比对：spec、`tests/test_recall_synth_goal.py`、本地 `e2e/scratch/recall/prompts/` 下 4 个文件（哈希见 §10），不一致即停。
  2. 逐条复核 §2、§3 坐标（读函数体，不是调用处），任一不符即停。
  3. `grep -rn _scene_index_job --include=*.py .`（含 tests），应与 F2 一致。
  4. SG 只读：SG 当前部署的 commit；Shiyu 账号下 ⑬ 这 5 本的 `text_id`、字数、书名（祈灼用 F16 那份）；每本名单里的主角名与别名（`cached_characters`）；祈灼卡 `d541a44f3679` 的阶段数（**必须 3**）；`qizhuo.jsonl` 行数 = 43、`phase` ⊂ {1,2,3}、用该卡阶段上界对卡绑定原文跑 `validate_cases`。贴原样输出。另对每本名单里每个人物统计「60 段中出现在几段 + 总次数」，贴完整排名表与按 ⑭ 选出的前 2，**停下等 Shiyu 确认人选**；名单为空 → 停，报是哪本（F15）；其他任何一项缺失或不符 → 停，不补救。
  5. 本地测试库 `localhost:55432` 在不在（`docker-compose.test.yml:33`）；不在 → 停，告诉 Shiyu 起。
- **S1**：提交 `tests/test_recall_synth_goal.py`，模块级 `xfail(strict=True)`，23 个全 xfail。
- **S2**：`fragment_problems` 抽取 + `char_bigrams` + 改名 + `aggregate_suite`；`tests/test_recall_eval_goal.py` 原样绿。
- **S3**：`core/recall_synth.py`（`PROMPT_SHA256` 用 §10 的值）；去掉 xfail，23 个全绿。
- **S4**：`load_book` + `recall_questions.py`（`probe` / `generate`）+ `DRAFT_PROMPT`；☁ `tests/test_recall_questions_cli.py`（替身只配 `chat`、固定返回、无流式；替身对空输入、坏 JSON 的行为照生产——返工经验 35；用例里的提示词用假模板，不写进论文原文）。
- **S5**：`eval_recall.py --suite`；☁ `tests/test_recall_suite.py`。
- **SG 上怎么跑（S6 与 0e 共用）**：把本分支的 Python 代码（`core/`、`web/`、`adapters/`、`storage/`、`scripts/` 整目录）和 `e2e/scratch/recall/`（含 `prompts/`、`suite.json`、草稿）拷到 SG 容器的 `/tmp/recall_pkg/`；在容器里 `cd /app && python /tmp/recall_pkg/scripts/<脚本> ...`。脚本开头把自己的上级目录插到 `sys.path` 最前（`scripts/eval_recall.py:30-31` 现成写法），于是用的是本分支代码。配置按**代码文件位置**找（`web/deps.py:17,36` `_REPO_ROOT / "config.yaml"`；`adapters/llm_adapter.py:760-761` 同理），所以要在副本里建软链 `/tmp/recall_pkg/config.yaml → /app/config.example.yaml`（**更正**：SG 容器里没有 `/app/config.yaml`，生产进程走 `web/deps.py:37-38` 的回退读 `config.example.yaml`；S6a 首跑因软链指错 `FileNotFoundError` 退出 1，改指后跑通）；Chroma 目录是相对当前目录的 `./data/chroma_db`（`core/rag.py:232`），`cd /app` 后即用线上那份；**必须整目录拷**——只拷改过的文件时 `core` 包会解析到副本、缺其他模块而导入失败。产物从 `/tmp/recall_pkg/` 拷回本地。跑完删掉 `/tmp/recall_pkg/`。本分支不加新依赖，用 SG 镜像现有的包。
- **S6a（真跑，摸底，SG，已完成 10-26）**：A.4、A.5 各两次。结果见 ⑮；A.5 判 `searchable=true` 7/86 次（q06、q17、q31、q36、q40），两次一致率 0.930，可用。
- ~~S6a′、S6a″~~：作废（⑯⑰）。

- **S6b（真跑，一条命令，SG）**：`generate`，跑完一次性报 `generate` 那一栏列的全部数字，然后开 PR。中途只在出错时停。**不跑评测**（基线是 0e）。

## 8. 调用点矩阵与测试范围

| 调用点 | 改前 | 改后 | 测试 |
|---|---|---|---|
| `validate_cases` | 循环体内联 | 调 `fragment_problems` | `tests/test_recall_eval_goal.py` 原样绿；目标检查 2 |
| `judge` 关 g_ground | — | 调 `fragment_problems` | 目标检查 2 |
| `schedule_scene_index` | 调 `_scene_index_job` | 调 `build_scene_index` | `tests/test_scene_index_dispatch.py` |
| `core/quotes.py`、`core/recall_synth.py` 结构锁 | — | 新增函数 | `tests/test_phase_anchoring.py`、`tests/test_arc_phase_select_locks.py` |
| `eval_recall.py` 单卡 | 现状 | 不变 | `tests/test_recall_eval_goal.py` |
| `eval_recall.py --suite` | — | 新增 | 目标检查 10；☁ `tests/test_recall_suite.py` |
| `recall_questions.py` | — | 新增 | 目标检查 2–9；☁ `tests/test_recall_questions_cli.py` |
| 聊天 `ContextEngine` | 不改 | 不改 | CI |

本地只跑：`tests/test_recall_synth_goal.py`、`tests/test_recall_eval_goal.py`、`tests/test_scene_index_dispatch.py`、`tests/test_phase_anchoring.py`、`tests/test_arc_phase_select_locks.py`。全量看分支 CI。

## 9. 结构自检（返工经验 36）

| 问 | 答 | 依据 |
|---|---|---|
| 同一条规则写了几处？ | 片段合规 1 处（`fragment_problems`）；每道模型关的通过条件 1 处（`verify_ok` / `judge` 里的 `searchable`）；占位替换 1 处（`render`）；证据格式 1 处（`quotes_block`，A.5 与 A.6 共用）；提示词身份 1 处（`PROMPT_SHA256`）；读书 1 处（`load_book`）；书目 1 处（`suite.json`） | §4 |
| 调用方是否被迫知道不该知道的事？ | 命令行不知道关卡规则（只调 `judge` / `ledger_problems` / `render`）；`judge` 不做 IO、不调模型（结果由调用方传入）；关 g_ground 不解析异常消息 | §4、F10 |
| 加一种情况要改几处？ | 换一份论文提示词版本 → 本地文件 + `PROMPT_SHA256` 一行；加一本书 → `suite.json` 一行；以后加意图 → `intent_slots` 一处；第 1 轮改切法 → `build_scene_index` 自动带上，题按原文位置判，不用重做 | — |

## 10. 出处对照（PDF 原文，页码为 PDF 页）

| 原文 | 页 | 本 spec |
|---|---|---|
| 「A quote is admitted only if it occurs in its source」；式 1 `g_ground(s_k, c_k) = 1[∃[i,j] s.t. c_k[i:j] = s_k]` | 5 | g_ground |
| 「Ordered checks impose two hard gates: persona realism (g_real; Appendix A.4) and exact quote localization (g_ground; Equation 1).」 | 5 | 两道硬关 |
| 「Two soft checks prompt repairs for question–passage bag-of-words overlap above 0.5 and distinctive wording or a question that lists its own answer options (the semantic "own-words" critic; Appendix A.5).」——词重合**没有定义**比什么、怎么分词、分母 | 5 | 词重合只当诊断（⑥）；A.5 当软关 |
| 「A final adversarial verifier (g_verify) checks answerability, gold-span sufficiency, that the question neither reveals the answer nor points to its location, independent support per evidence unit, and sequential dependency for bridges」 | 5 | `verify_ok`（⑦）；桥接只适用多跳，我们不出 |
| 「The hard admission criteria are」式 2 `A = g_real ∧ ⋀ g_ground ∧ g_verify`；「A draft still failing a hard gate after at most two repairs is rejected.」 | 5 | 只硬关决定录取；软关修不过录取带标记（⑥） |
| 表 4：「The generator steers a candidate toward one intent and an adversarial gate confirms the drafted question matches it. Factual lookup is the default」；Factual lookup 行 | 16 | `intent_slots`（⑧） |
| A.4 Realism gate 全文 | 16–17 | 本地 `prompts/realism_A4.txt`；**不用**（⑮），文件保留作摸底记录 |
| A.5 Own-words critic 全文 | 17–18 | 本地 `prompts/own_words_A5.txt` |
| A.6 Single and Aggregation Queries 全文 | 18 | 本地 `prompts/verifier_A6.txt` |
| 「GPT-5.6 Sol drafts questions and adjudicates semantics.」（§5.2） | 8 | CoverSynth 起草与核查同一模型；我们 Claude 起草、DeepSeek 核查是自己的选择 |

**本地提示词文件**（从 PDF 抽文本；只做三处机械清理：去掉页脚页码与「Scale AI Research」行、把排版换行续行符「,→」接回上一行、去掉公共缩进；表 4 一行把两栏用「: 」接起来）：

| 文件 | SHA256 |
|---|---|
| `realism_A4.txt` | `6bcc5264a5feed4125209600c65e363d2ba7385f315506945382678d3561aca2` |
| `own_words_A5.txt` | `3a16aa0982370acbd5109db3db82c6b4ec5993c4838048bc257c197421e38280` |
| `verifier_A6.txt` | `026d60838790eab847c2841b66c11017972abc8a206d65d8b980200fe901999c` |
| `intent_factual_A3.txt` | `ae4684baa1ea63f31734b3c73baccf84bb9857d7d63665c22f1a8938b0a5f8ca` |

**论文没有直接写、按原文旁证推断的三处**（2026 年「生成 + 评审修改」相关工作里没找到写明这两处做法的；CoverSynth 无公开代码）：

| 推断 | 原文旁证 | 判断 |
|---|---|---|
| ~~A.4 两项都为真才算通过~~ | — | 随 ⑮ 作废 |
| 修改时用 A.5 的 `suggestion` | A.5 输出格式有 `suggestion` 字段，写明是改写后的问句；论文别处没有其他改写机制 | 很可能对 |
| A.6 不过也修：把 `notes`（哪一项不过）交给起草方重写，最多 2 次 | A.6 输出只有 `notes`、没有 `suggestion`；第 5 页「A draft still failing a hard gate after at most two repairs is rejected」，A.6 是硬关 | 与原文最一致的读法 |

怎么验：两条都是修改机制，摸底量不到，在 S6b 报「各关退回后修 1 次、修 2 次通过的条数」；修了也过不了的多，回来找 Shiyu 定。

**我们自己写的部分（论文没给，标明）**：⑨ 的人设句；`intent_slots` 的包装句「6. intent_match: does the QUESTION realize this intent? 」与字段名 `intent_match`（论文只留 `{intent_criterion}` / `{intent_field}` 两个占位）；`quotes_block` 的 `[e1]` 编号格式（A.6 原文只说「id in brackets」）；g_ground 先跑。

**局限**：论文没报各关通过率，也没有模型关与人工的一致率；我们的数字只能如实报。A.4 原文场景是「file-search assistant」「day-to-day work」，与和角色聊天不同 → S6a 摸底量误判。

## 11. 补充

### 复核方必须验的变异清单（返工经验 14：作者侧不作证据）

复核方逐条验，并**自补**放宽、过严两个方向各至少 2 条。

| # | 变异 | 方向 |
|---|---|---|
| M1 / M2 | 每层取全部 / 空层向后借 | 放宽 |
| M3 | 规范化起止算成原文坐标 | 错位 |
| M4 / M5 | g_ground 不查窗口 / 不走 `fragment_problems` | 放宽 |
| M6 | `judge` 仍收 `realism` 或仍登记 A.4 | 违反 ⑮ |
| M7 | A.5 第 3 次仍判 `searchable` 时跳过 A.6 直接录取 | 放宽 |
| M8 | A.5 第 3 次仍判 `searchable` 时丢题 | 过严（违反式 2） |
| M9 | A.5 第 3 次打标记后跳过 A.6 | 放宽 |
| M10 | A.6 忽略 `per_doc` / `intent_match` | 放宽 |
| M11 | A.6 把 `necessary` 计入通过 | 过严 |
| M12 | `per_doc` 键集合不核对 | 放宽 |
| M13 | 解析不了当作失败 | 过严 |
| M14 / M15 | 修复上限 3 / 1 | 放宽 / 过严 |
| M16 | 不核哈希 | 放宽 |
| M17 | 用 `str.format` 替换占位 | 崩 / 错位 |
| M18 | 重合参与判定 | 改口径 |
| M19 | 重合对整段窗口而非答案片段 | 改口径 |
| M20 / M21 | 已丢弃可重出 / 可新增题 | 补题 |
| M22 | 结果计数不分关 / 不分标记 | 报告失真 |
| M23 | 总体 = 各书比例平均 | 改口径 |

（审计发现写在这里。）
