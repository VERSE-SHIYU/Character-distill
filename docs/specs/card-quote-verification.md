# 卡片引文逐字核对（2026-09-28，Shiyu 已拍板「只做能确定解决的」）

> 放到 worktree 的 `docs/specs/card-quote-verification.md`，以后的补充写进本文件。
> **源文件**：`E:\Study\ANU\computing\26S1\temp-files\card-quote-verification.md`（入库副本即本文件）。
> **执行方**：C 窗口，从 origin/main（`fb893b9`）新开分支 `fix/card-quote-verification`（`--unset-upstream`）。**执行方 skill** 见文末。

## 目标
- 卡片上凡是用引号括起来的「原文」，都能在原文里逐字找到。找不到的，由代码去掉引号，不再冒充原文。
- 口癖一律逐字出自原文，找不到的删掉。
- 验收脚本和产品用同一套判定，不再漏查英文双引号。

推理判断类错误（关系里写了没往来的人、人物状态判断错、证据用错地方）**不在本 spec 范围**：目前没有可靠的自动方法，演示卡由 Shiyu 用现有的「编辑卡片」功能手工改正（`PATCH /api/distill/card/{card_id}`，`web/routers/distill.py:1230`；前端 `EditCardModal.jsx`）。

## 问题（实测证据）
刘姥姥卡（`4ce655c30e0e`，`ae36f7c` 产出）在本地原文（`text_id 068692e5b6f7`）上核对：
- **3 条引文查不到**：
  - `personality_traits[2]`「你就说醉话，我是要进城瞧瞧去的」：前半句原文没有，是拼接；
  - `key_memories[2]`「唬的不敢作声」：措辞不逐字；
  - `emotional_patterns[1]`「已念了几千声佛」：措辞不逐字。
- **验收报告「引文查不到 0」是假的**：卡里的引文全用英文双引号 `"`，而验收脚本的 `_QUOTE_PAIRS`（`tests/perf/longbook_acceptance.py:589`）不含它，一条都没检查。审计方已实测：编造一条英文双引号引文，`quote_misses` 返回空。
- **产品侧完全没有引文核对**：卡片生成后原样入库。

## 出处对照表
| 做法 | 出处 | 年份 | 事实依据 | 成熟度 |
|---|---|---|---|---|
| 引文由程序从原文确认，不信任模型写的引号 | Anthropic Citations（官方发布文、平台文档） | 2025-01 发布，文档在线更新 | 被引文字由系统直接从文档中提取，不是模型生成的，因此保证指向真实原文 | 厂商正式功能，已商用 |
| 找不到支撑原文的结论要撤回 | Anthropic「Reduce hallucinations」官方指南 | 在线文档 | 生成后逐条找支撑原文，找不到就撤回这条结论 | 厂商推荐做法，无公开量化数据 |
| 引文逐字复制、不经模型改写 | Deterministic Quoting（Simon Willison 2025-01-24 转述） | 2024–2025 | 引文保证原样复制，不被模型有损改写 | 业界工程模式 |
| 产品与验收共用同一判定函数 | 本仓 WP17（`core/quotes.py::verbatim_in`，PR #46） | 2026-09 | 刘姥姥对话示例 3/3 逐字命中 | 本仓已验收 |

DeepSeek 没有 Citations 开关，所以本 spec 用代码实现同一件事，复用 WP17 已有的逐字核对。

## 全量扫描原文（刘姥姥真卡，按字段统计英文双引号引文）
| 字段 | 引文数 | 本 spec 的处理 |
|---|---|---|
| `personality_traits` | 9 | 核对 |
| `values` | 7 | 核对 |
| `key_memories` | 8 | 核对 |
| `inner_tensions` | 6 | 核对 |
| `emotional_patterns` | 7 | 核对 |
| `decision_style` | 2 | 核对 |
| `relationships[].attitude` | 1 | 核对 |
| `psyche.soft_spots` | 1 | 核对 |
| `cognitive.speech_style` | 3 | 核对 |
| `speaking_style.sentence_pattern` | 1 | 核对 |
| `speaking_style.taboo_words` | 4 | **不核对**：引号里是「这个角色不会说的词」（如「卑职」），本来就不该在原文里 |
| 合计 | 49 | 核对 45 条；其中归一化后不足 4 字的（如「巧哥儿」「你老」）按现有口径跳过 |

**明确不核对的字段**（逐个声明，不靠「其余」概括）：
- `name`、`tags`；
- `first_message`、`awakening_message`：角色口吻的创作，不是对原文的陈述；
- `dialogue_examples`：WP17 已由构造保证逐字；
- `speaking_style.taboo_words`：理由见上表；
- `speaking_style.tone`、各处的 `vocabulary_level`、`cognitive.education_level`：描述用词，引号里是形容词，不是引文；
- `relationships[].target/relation/note`：`note` 是角色第一人称的注入口径，属于创作；
- `psyche` 的数值与枚举字段。

## 规模表
| 量 | 数值 | 出处 |
|---|---|---|
| 原文长度 | 866,149 字 | S0（WP17）实测 |
| 每张卡要核对的引文 | 约 45 条 | 上表 |
| 原文归一化一次 | 约 0.13 秒 | 审计方用 90 万字公开版实测 |
| 现有 `verbatim_in` 连调 40 次 | **约 4.1 秒** | 同上：每次都重新归一化整本书 |
| 改为归一化一次、再查 45 条 | 约 0.13 秒加上 45 次子串查找 | 本 spec 第 1 步 |

所以核对前**必须先归一化一次原文**，不能逐条调用 `verbatim_in`。

## 已查实的约束（基线 main `fb893b9`；执行方 S0 逐条复核，不成立即停）
1. `core/quotes.py`：`normalize`（`:27`）、`verbatim_in`（`:35`，每次调用都归一化整段 `source`）。`_QUOTE_PAIRS`（`:53`）是从原文里抽**对话**用的，用途不同，本 spec 不动它。
2. 验收脚本：`_strings`（`tests/perf/longbook_acceptance.py:576`）、`_QUOTE_PAIRS`（`:589`，不含英文双引号）、`QUOTE_MIN_CHARS = 4`（`:590`）、`quote_misses`（`:593`）、`catchphrase_misses`（`:622`）；调用处在 `:537-539`。
3. 三条产卡通道的后置挑选调用点：bg `web/routers/distill.py:514`（同步）；SSE `:1134-1136`（已在 `await asyncio.to_thread` 里）；TextManager `core/text_manager.py:471-474`（已在 `to_thread` 里）。三处都调 `Distiller.attach_dialogue_examples`（`core/distiller.py:1744`）。
4. 卡片字段定义在 `core/schema.py`，`CharacterCard` 从 `:69` 起（S0 更正：原写 `:52`；`:52` 实为 `PsycheProfile`）。
5. 手工编辑卡片：`PATCH /api/distill/card/{card_id}`（`web/routers/distill.py:1230`），前端组件 `EditCardModal.jsx`。

## 方案与拍板
Shiyu 2026-09-28 拍板：**只做能确定解决的**，推理错误靠人工编辑。

找不到原文的引文怎么处理，Claude 按技术取舍直接定：**去掉引号，保留文字**。
- 不整条删除：刘姥姥卡里 3 条查不到的都在列表项里，整条删会连带删掉「醉卧怡红院」这种事件本身正确的关键记忆；
- 去掉引号后，这段文字不再冒充原文，「引号里的必是原文」这条保证依然成立；
- 保留下来的文字若有错，由人工编辑改正。

**口癖例外：整条删除。** 口癖本身就声明「是原话」，对不上就是错的，没有可保留的部分。

## 调用点矩阵（通道 × 执行上下文）
| 通道 | 执行上下文 | 改动 | 守它的测试 |
|---|---|---|---|
| bg `_run_distill_task` | 后台线程（同步） | `:514` 的 `attach_dialogue_examples` 改为 `finalize_card`，参数不变 | T4-bg |
| SSE `_event_gen` | async 生成器 | `:1135` 同上，仍在原来的 `to_thread` 里 | T4-sse |
| TextManager `get_or_distill` | async 协程 | `:472` 同上，仍在原来的 `to_thread` 里 | T4-tm |

三处都只换一个方法名，不新增调用，也不改变执行上下文。

## 路径机制清单
| 机制 | 前提 | 本改动是否改变前提 | 处理 |
|---|---|---|---|
| 后置步骤失败口径（对话示例挑不出即任务失败） | 不 fail-open | 否 | 引文核对是纯计算，不抛业务异常；对话挑选的失败口径不变 |
| async 通道不阻塞事件循环 | 后置步骤在 `to_thread` 里 | 否 | `finalize_card` 替换原调用，仍在同一个 `to_thread` 里 |
| 记账 | 每次模型调用一行 | 否 | 本改动不调用模型 |
| 耗时 | 后置步骤约几秒 | 新增约 0.2 秒 | 按规模表，先归一化一次 |

## 步骤（按依赖顺序，每步独立 commit）
1. **[core/quotes] 引文抽取与一次归一化的核对**（纯函数，不 import 项目模块）：
   - `CITATION_PAIRS`：`"…"`、`'…'`、`‘…’`、`“…”`、`「…」`、`『…』`，从验收脚本 `:589` 搬来并加上英文双引号；`CITATION_MIN_CHARS = 4`，从 `:590` 搬来。
   - `quoted_spans(text) -> list[tuple[int, int, str]]`：返回每条引文（含引号）在 `text` 里的起止位置和引号内文字。
   - `verbatim_in` 拆成两层：`verbatim_in_normalized(src_norm, quote)` 负责实际比对；`verbatim_in(source, quote)` 改为归一化后调用它，对外行为不变。
2. **[core] 新建 `core/card_quotes.py`**：卡片层的引文核对，import `core.schema` 与 `core.quotes`。
   - `VERIFIED_FIELDS`：显式列出要核对的字段路径，即全量扫描表里标「核对」的那些，不在清单里的一律不动。
   - `retract_unverified(card, content) -> (CharacterCard, list[dict])`：
     - 先 `normalize(content)` 一次；
     - 清单内字段里，找不到的引文（归一化后 ≥4 字）去掉引号、保留文字；
     - 口癖整条删除（口癖不设字数下限，同验收 `:622` 的口径）；
     - 返回新卡和撤回清单（字段、原引文）。每条撤回记一行 `logger.warning`。
3. **[distiller + 三通道] 合并后置步骤**：新增 `Distiller.finalize_card(card, content, name, aliases, roster)`，先调 `retract_unverified`，再调 `attach_dialogue_examples`，签名与 `attach_dialogue_examples` 相同。三个通道把调用换成它，见调用点矩阵。
4. **[验收脚本]** 删掉自带的 `_QUOTE_PAIRS`、`QUOTE_MIN_CHARS`，改为从 `core.quotes` import；`quote_misses` 只检查 `VERIFIED_FIELDS`，与产品同口径，并改为「先归一化一次」的写法。

## 测试（本地只跑受影响的测试文件，本 spec 不改前端、不跑 `npm test`；库用 docker PG；合并门是分支 CI；合并只做 git 操作，不跑测试、不等 CI）
- **T1 抽取**（`tests/test_quotes.py`）：英文双引号、中文引号、中文里嵌英文引号、不足 4 字的跳过，都要抽对。
- **T2 核对与撤回**（新文件 `tests/test_card_quotes.py`）。用公版《红楼梦》原句做夹具（公有领域），造一张卡：
  - 「你就说醉话，我是要进城瞧瞧去的」→ 引号被去掉、文字保留，撤回清单里有它；
  - 真实引文（如「我掂着这杯体重，断乎不是杨木，这一定是黄松的」）→ 原样不动；
  - 口癖里有一条原文没有的 → 被删除；
  - `taboo_words`、`first_message`、`note` 里放编造的引文 → 原样不动；
  - `relationships[].attitude` 里的编造引文 → 去掉引号，关系本身保留。
- **T3 只归一化一次**：给一张含 45 条引文的卡计数 `normalize` 被整本原文调用的次数，断言为 1。
- **T4 三通道都接上**（沿用 WP17 的通道测试文件）：三条通道产出的卡里，编造引文的引号都已去掉。
- **T5 验收脚本同口径**（`tests/test_longbook_acceptance_card.py`）：一张含英文双引号编造引文、未经核对的卡，`quote_misses` 能报出它。

## 对账表（S0 先审：逐条确认变异在改后代码上可观测、每个决定都有测试；有问题先停下报告）
| 行为变化（含连带效果） | 守它的测试 | 让它变红的变异 | 改后能触发的具体状态 |
|---|---|---|---|
| 英文双引号引文会被抽出 | T1 | 从 `CITATION_PAIRS` 删掉英文双引号 | 刘姥姥卡里的 49 条引文都能被检查 |
| 查不到的引文去掉引号、保留文字 | T2 | 改为原样保留，或改为整条删除 | 「你就说醉话……」不再带引号；关键记忆不丢 |
| 真实引文原样保留 | T2 | 把所有引文都去掉引号 | 真引文照常显示为引文 |
| 口癖对不上就删除 | T2 | 口癖改为去引号或不处理 | 卡里的口癖全都能在原文找到 |
| 清单外字段不动 | T2 | 改为检查所有字段 | 忌讳词、开场白、`note` 不被误改 |
| 原文只归一化一次 | T3 | 改回逐条调用 `verbatim_in` | 核对耗时约 0.2 秒而不是约 4 秒以上 |
| 三条通道都做核对 | T4-bg、T4-sse、T4-tm | 任一通道改回直接调用 `attach_dialogue_examples` | 三种入口产出的卡同样干净 |
| 验收检查英文双引号 | T5 | 验收脚本改回自带、不含英文双引号的配对表 | 验收报告的「引文查不到」不再是假的 0 |

**发出前预跑**（审计方已在 `fb893b9` 上做过）：
- 表中最后一行的「改前状态」已复现：编造一条英文双引号引文，现有 `quote_misses` 返回空；
- 「归一化一次」的耗时对比已实测：连调 40 次约 4.1 秒，单次归一化约 0.13 秒。

## skill
| 步骤 | skill | 用途 |
|---|---|---|
| S0 | `@search-first` | 复核坐标与扫描结论 |
| 1–4 | `@tdd` | 先写 T1–T5 让它们变红，再改代码 |

## 范围规矩
执行中新发现的问题，属于本段改动面的直接修；只有会撞车或需要 Shiyu 拍板时才停下报告，不许自行记账。交付报告不写本地全量测试数字。

## S0（执行方先做，只读，报完停下等审计）
1. 在 `fb893b9` 上复核约束 1–5 的坐标，有一条不成立即停。
2. 用本地库里刘姥姥这张卡（`4ce655c30e0e`）和 `text_id 068692e5b6f7` 的原文，报出：按全量扫描表各字段的引文数；按新口径会被撤回的引文清单。预期正好是问题一节列的那 3 条，不一致就停下报告。
3. 审对账表。

## 验收（之后一次真跑，约 2 元：刘姥姥约 0.7 元，宝玉一次读完约 0.7 元加挑选约 0.4 元）
1. 重跑刘姥姥和宝玉。门槛：
   - 验收脚本（已纳入英文双引号）报「引文查不到」为 0，「口癖查不到」为 0；
   - WP17 的对话示例 3/3 维持；
   - 其余门槛维持。
2. 报出每张卡的撤回清单：字段、原引文。
3. Claude 对两张卡逐条核对推理类错误，列出具体错处和原文依据；Shiyu 用编辑卡片功能改正后，再上演示。
