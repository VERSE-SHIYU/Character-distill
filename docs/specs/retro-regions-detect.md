# PR-3：识别并存储「回忆区」（只算、不用）

基线：main `a39b1512`。分支：`feat/retro-regions-detect`。代码由 Claude 在沙箱写好、跑过全部检查后直接推送；本地执行方负责审计。

## 0. 这一步做什么、不做什么

- **做**：每本书蒸馏时，把它的回忆区（日记 / 书信 / 番外 / 整章回忆的章节范围）标出来，按书存一份；同一本书再蒸就直接读缓存，不再调模型。
- **不做**：位置核对还不用它（锚点、证据句照旧），那是 PR-4。本 PR 上线后行为唯一的变化是：每本书第一次蒸馏多一次调用，日志多一行 `[retro]`。费用口径：首次蒸馏时它是第一个发整书前缀的请求，按全价输入计（冷缓存）；它在主蒸馏之前同步跑完（`/start` 里 `submit_to_main_loop` 默认 `wait=True`，`/run_stream` 里 `await`），主调用随后才发同一前缀、命中缓存。所以相对今天净多出的是「一次缓存价的整书输入 + 不到 1024 token 输出」；DeepSeek 的缓存是尽力而为，没命中时最坏多一次全价整书输入。冷缓存实测待首次真蒸馏时从 `[retro]` 用量行读出。
- 依据：共享文档「方案 1」（Tracy 已定）；提示词就是 10-17 小重放实测那一版（2 次，日札和番外一章没漏）。

## 1. 目标检查（`tests/test_retro_regions_goal.py`，diff 里已有；main 上因缺模块而全红，原型上 19 passed）

| 检查 | 守什么 |
|---|---|
| g1 / g1b | 只认行首「第N章」；没有标题、章号重复、不递增 → None（不猜） |
| g2 / g2b / g2c | 章号从代码列的目录里选：解析结果与标准答案**逐章相等**；目录逐章列出章号 + 开头 40 字；开头跳过只有省略号/破折号的行 |
| g3 | 章号不在目录里、首尾颠倒、某一个范围坏了、缺章号、回复不是 JSON → 整本「未标」且不缓存 |
| g4 / g4b | 没有章节标题就不调模型；请求以整书前缀开头（与蒸馏主调用同一个 `book_prefix`，后发的那个命中缓存），以目录结尾 |
| g5 | JSON 存取来回一致 |
| g6–g6e | 按书缓存：第二次不调模型；版本不符重算；非属主读不到；偶发失败不缓存；调用抛错不往外抛 |
| g7 / g7b | 真 `/start`、真 `/run_stream`：回忆区被算出并落库，卡照常落库 |
| g7c | 回忆区标不了时，卡照常落库 |

## 2. 改动（11 个文件）

| 文件 | 改什么 |
|---|---|
| `core/chapters.py`（新） | `chapter_starts(source_text) -> [(章号, 原文起点)] \| None`，正则 `(?m)^[ \t　]*第(\d+)章`；`chapter_openings(source_text, chapters, width=40)`（每章开头几十字，跳过只有省略号/破折号的行） |
| `core/retro_regions.py`（新） | `RETRO_VERSION = 1`、`RETRO_INSTRUCTION`、`RetroRegions`（`status` marked/unavailable、`ranges`、`reason`、`final` 能否缓存）、`instruction(source_text, chapters)`（指令 + 代码列的全书目录）、`parse(reply, chapters)`（章号须在目录里；任一范围不合法整本未标、不缓存）、`detect(source_text, *, prefix, call)`。**不认识 Distiller**，模型调用由外部注入（同 `core/relationship_batch.py`） |
| `core/retro_cache.py`（新） | `resolve_retro_regions(storage, distiller, text_id, user_id, content)`：读缓存 → 没有就 `distiller.detect_retro_regions` → `final` 的写回；任何一步出错只打 warning、返回「未标」，不挡蒸馏（同 `core/character_roster.py::resolve_characters` 的形状） |
| `core/distiller.py` | 新增 `RETRO_MAX_TOKENS = 1024`（`IDENTIFY_VERSION` 下方）和方法 `detect_retro_regions(text)`（`fill_relationships` 上方）：分组路径直接「未标」；一次读完的书用 `_collect_stream`（记账出口不变，action `distill_retro`）+ `book_prefix(text)` 调 `retro_regions.detect`；截断抛 `DistillError` |
| `storage/base.py` | 两个抽象方法 `save_retro_regions(text_id, retro_json, *, version)`、`get_retro_regions_owned(text_id, user_id, *, version) -> str` |
| `storage/sqlite_store.py` / `storage/postgres_store.py` | 两个方法的实现（放在 `load_card_draft_owned` 后面）；`get` 版本不符返回 ""；SQLite 迁移清单加 `"108_text_retro_regions.sql"`（sqlite_store.py 迁移元组末尾，107 下面） |
| `storage/migrations_pg/043_text_retro_regions.sql`、`storage/migrations/108_text_retro_regions.sql`（新） | `texts` 加 `retro_json TEXT`、`retro_version INTEGER DEFAULT 0`（PG 用 `IF NOT EXISTS`，SQLite 裸 `ADD COLUMN`） |
| `web/routers/distill.py` | import `resolve_retro_regions`；`_run_distill_task` 里 `aliases = aliases_for(chars, name)` 之后调一次（`submit_to_main_loop`）；`/run_stream` 的 `_event_gen` 里「Incremental distillation」注释之前 `await` 一次。**不动** `card_from_draft` 那两行（变异驱动 M10/M12 的靶子） |
| `tests/test_retro_regions_goal.py`（新） | §1 |

## 3. 已查实的约束（main a39b1512）

1. 名单缓存的现成做法：`texts.characters_json` + `characters_version`，`postgres_store.py:466-505` 的 `save_characters` / `get_characters_owned`（属主过滤在 SQL，版本不符当无缓存）；入口 `core/character_roster.py:98-115`。本 PR 照抄这个形状。
2. 一次读完 vs 分组：`Distiller._takes_one_pass(text)`（distiller.py:1775）；整书前缀 `book_prefix(text)`（distiller.py:465）。
3. 记账：长输出走 `_collect_stream`（distiller.py:956），在本级记账。
4. SQLite 迁移要登记在 `sqlite_store.py` 的迁移元组里（107 那一项在 :174）；PG 迁移按文件自动发现（postgres_store.py:24 `_MIGRATIONS_DIR`）。`tests/test_migration_dispatch.py`、`test_schema_parity.py`、`test_sqlite_fresh_schema.py` 守两侧一致，原型上全过。
5. 不涉及前端。

## 4. 审计时跑什么

- **S0**：逐条核对 §3 的行号和代码。有一条对不上就停下报告。
- 本地只跑：
  1. `python -m pytest -q tests/test_retro_regions_goal.py`（应 19 passed）
  2. `python -m pytest -q tests/test_character_roster.py tests/test_distill_draft_persist.py tests/test_distill_task_api.py tests/test_distill_usage_accounting.py tests/test_distiller_routing.py tests/test_domain_exception_exit.py tests/test_identify_failure_channels.py tests/test_migration_dispatch.py tests/test_postgres_store.py tests/test_schema_parity.py tests/test_sqlite_fresh_schema.py tests/test_storage.py tests/test_storage_contract_shape.py tests/test_storage_scope_lock.py tests/test_store_commit_contract.py tests/test_store_connect_lock.py tests/test_store_failure_visibility.py tests/test_usage_accounting_lock.py tests/test_usage_cache_tokens.py tests/test_phase_anchoring.py tests/test_lock_coverage.py`（审计实测 847 passed、1 skipped；原 spec 写的 3716 是错的）
  3. `npm test`

## 5. 判断清单（每条都读过代码或跑过）

| # | 判断 | 依据 |
|---|---|---|
| 1 | 实测脚本发出的请求与 `retro_regions.detect` 逐字相同 | 脚本直接导入 `RETRO_INSTRUCTION` / `RETRO_USER` / `parse`；沙箱比对两边 system、user 均为 True |
| 2 | 请求前缀与蒸馏主调用一致；首次蒸馏时回忆区调用先发、冷缓存，主调用随后命中 | g4b；真原文上 `system.startswith(book_prefix(src))` 为 True |
| 3 | 真原文认出 509 章，第 491 章起点 1135490 | 沙箱在原文 3335472a438b 上跑 `chapter_starts` |
| 4 | 现有蒸馏替身没有 `detect_retro_regions` 也不会挂 | `resolve_retro_regions` 兜住异常；g7c；原有路由测试全过 |
| 5 | 不碰变异驱动的靶子行 | `card_from_draft` 两处调用、`save_distilled_card` 未改；`test_lock_coverage.py` 全过 |

## 6. 已知边界

分组路径（超长书）和没有「第N章」标题的书得到「未标」，行为同今天；章内夹的回忆（如第 399 章告白）这次管不到。

## 7. 审计更正与真模型实测（10-17 ~ 10-18）

**文档口径**：§4 测试数字原写 3716，实为 847 passed、1 skipped；「命中缓存」原写法不对首次蒸馏成立，已按 §0 改正。

**标准答案**（逐章读过原文 3335472a438b，含第 490、505–509 章开头结尾）：倒叙 = 第 491–504 章日札、第 508–509 章番外（云钺童年）。第 490 章「正文完」；第 505–507 章是结局之后的番外（第 507 章末「番外：误认为有孕篇（完）」），时间接在正文之后，不是倒叙。我最初把 505–507 记成「主线接续」，只数了【日札】标记、没读这三章结尾 —— 这是第一个错。

| 轮次 | 做法 | 结果 | 根因 | 依据 |
|---|---|---|---|---|
| 1（10-17，旧探针） | 按体裁问「日记 / 番外」，模型写章号 | 2 次都漏 0；多标 [505,506,507]、[505] | 口径错：代码要的是「位置 ≠ 故事时间」，不是体裁；505–507 是番外但时间顺接。旧 g2 只查不漏、放过多标 | 代码用途：阶段按原文位置归，默认位置 = 时间（`phase_anchoring.phase_at`）；原文第 490 / 507 章的标记 |
| 2（06e403ad） | 按时间顺序问，模型写章号 | 1/2：run1 多 [505] | 内容判断对了（506–507 不再标），但章号是模型在 80 万 token 里数的，边界错一章 | 4 次运行 3 次把 505 并进日札，换了口径照样 |
| 3（fa0b7cf9） | 模型给首末两句原文，代码定位换算章号 | 1/2：run1 引文重复 → 整本未标；且即使唯一，「末章结尾附近」两次取到前一章末句 → 漏 502、504 | 同一个根因换了形式：让模型找边界（不管用章号还是用句子）都不可靠 | 第三轮原始回复（Tracy 本地）逐条换算 |

**6 次运行的共同事实**：每一次，模型都认对了**哪些内容**是倒叙（6 位角色的日札、云钺番外），错只错在**边界落在哪一章**。

**第四轮修法（cf42b4ea）**：边界交给代码。代码按「第N章」标题列出全书目录（每章章号 + 开头 40 字，真原文约 2.4 万字），附在指令后；模型只从目录里选首末章号。日札章开头都是「【日札…】」，第 505 章开头是「自云绮从长达一月的昏睡中醒来」，章号与内容就近对照。这是本仓已经在用的做法 —— WP17「代码列编号候选、模型只回编号」（`core/quotes.py::extract_candidates` + `_numbered`）。去掉第三轮的引文定位，`RETRO_MAX_TOKENS` 回到 1024（实测输出 331–748 token）。章号不在目录里或首尾颠倒 → 整本未标、不缓存。

**未查**：「长上下文里模型找位置不可靠」没有查 2026 年文献佐证，依据只有上表的 6 次实测和本仓 WP17 的既有做法。

**验收**：`e2e/retro_ranges_probe.py`（直接调分支上的 `retro_regions.detect`，只注入模型调用），两次都逐章相等才合并；没过不合并、不进 PR-4。g2 用同章数的替身书检验解析（原文不入库，不能把真实回复写进单测）。

**本地超时**：本地上传 3.5MB 请求体时 `chat_stream_long` 建连预算（7s/8s）会 WriteTimeout，需 `LLM_STREAM_ATTEMPT_S=120 LLM_STREAM_DEADLINE_S=180`。主蒸馏调用同一函数、同一预算，生产上一直能过，属本地网络；不在本 PR 改。

**第四轮实测（d74d106a，Tracy 本地，10-18）：2/2 逐章相等。** 两次都解析为「第 491–504 章日记、第 508–509 章番外」，漏 0、多 0。用量：prompt 834,347 token（目录约 1.7 万 token；run2 缓存命中 834,176），completion 225 / 174 token（第三轮引文版 654–748）。
