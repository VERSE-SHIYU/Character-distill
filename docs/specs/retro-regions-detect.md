# PR-3：识别并存储「回忆区」（只算、不用）

基线：main `a39b1512`。分支：`feat/retro-regions-detect`。代码由 Claude 在沙箱写好、跑过全部检查后直接推送；本地执行方负责审计。

## 0. 这一步做什么、不做什么

- **做**：每本书蒸馏时，把它的回忆区（日记 / 书信 / 番外 / 整章回忆的章节范围）标出来，按书存一份；同一本书再蒸就直接读缓存，不再调模型。
- **不做**：位置核对还不用它（锚点、证据句照旧），那是 PR-4。本 PR 上线后行为唯一的变化是：每本书第一次蒸馏多一次小调用（整书前缀命中缓存，按缓存价估算不到 ¥0.05），日志多一行 `[retro]`。
- 依据：共享文档「方案 1」（Tracy 已定）；提示词就是 10-17 小重放实测那一版（2 次，日札和番外一章没漏）。

## 1. 目标检查（`tests/test_retro_regions_goal.py`，diff 里已有；main 上因缺模块而全红，原型上 19 passed）

| 检查 | 守什么 |
|---|---|
| g1 / g1b | 只认行首「第N章」；没有标题、章号重复、不递增 → None（不猜） |
| g2 | 两次真实回复解析后，第 491–504、508–509 章一章不漏 |
| g3 / g3b | 章号对不上原文、首尾颠倒的范围丢掉；回复不是 JSON → 「未标」且不缓存 |
| g4 / g4b | 没有章节标题就不调模型；请求以整书前缀开头（与蒸馏主调用同一个 `book_prefix`，命中缓存） |
| g5 | JSON 存取来回一致 |
| g6–g6e | 按书缓存：第二次不调模型；版本不符重算；非属主读不到；偶发失败不缓存；调用抛错不往外抛 |
| g7 / g7b | 真 `/start`、真 `/run_stream`：回忆区被算出并落库，卡照常落库 |
| g7c | 回忆区标不了时，卡照常落库 |

## 2. 改动（11 个文件）

| 文件 | 改什么 |
|---|---|
| `core/chapters.py`（新） | `chapter_starts(source_text) -> [(章号, 原文起点)] \| None`，正则 `(?m)^[ \t　]*第(\d+)章` |
| `core/retro_regions.py`（新） | `RETRO_VERSION = 1`、`RETRO_INSTRUCTION`、`RetroRegions`（`status` marked/unavailable、`ranges`、`reason`、`final` 能否缓存）、`parse`、`detect(source_text, *, prefix, call)`。**不认识 Distiller**，模型调用由外部注入（同 `core/relationship_batch.py`） |
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
  2. `python -m pytest -q tests/test_character_roster.py tests/test_distill_draft_persist.py tests/test_distill_task_api.py tests/test_distill_usage_accounting.py tests/test_distiller_routing.py tests/test_domain_exception_exit.py tests/test_identify_failure_channels.py tests/test_migration_dispatch.py tests/test_postgres_store.py tests/test_schema_parity.py tests/test_sqlite_fresh_schema.py tests/test_storage.py tests/test_storage_contract_shape.py tests/test_storage_scope_lock.py tests/test_store_commit_contract.py tests/test_store_connect_lock.py tests/test_store_failure_visibility.py tests/test_usage_accounting_lock.py tests/test_usage_cache_tokens.py tests/test_phase_anchoring.py tests/test_lock_coverage.py`（原型上与 §1 合计 3716 passed、1 skipped）
  3. `npm test`

## 5. 判断清单（每条都读过代码或跑过）

| # | 判断 | 依据 |
|---|---|---|
| 1 | 提示词与小重放那版逐字相同 | 沙箱比对 `retro_ranges_probe.INSTRUCTION == RETRO_INSTRUCTION` 为 True，`RETRO_USER` 也相同 |
| 2 | 请求前缀与蒸馏主调用一致，会命中缓存 | g4b；真原文上 `system.startswith(book_prefix(src))` 为 True |
| 3 | 真原文认出 509 章，第 491 章起点 1135490 | 沙箱在原文 3335472a438b 上跑 `chapter_starts` |
| 4 | 现有蒸馏替身没有 `detect_retro_regions` 也不会挂 | `resolve_retro_regions` 兜住异常；g7c；原有路由测试全过 |
| 5 | 不碰变异驱动的靶子行 | `card_from_draft` 两处调用、`save_distilled_card` 未改；`test_lock_coverage.py` 全过 |

## 6. 已知边界

分组路径（超长书）和没有「第N章」标题的书得到「未标」，行为同今天；章内夹的回忆（如第 399 章告白）这次管不到。
