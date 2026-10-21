# 段 1：蒸馏小重放工具 `scripts/replay_distill.py`（写工具 + 测试 + PR，不花钱）

仓库：`VERSE-SHIYU/Character-distill`（不是 neylo-python）。基线：main `f92aa456`（#198 合并提交）。
执行环境：**云端会话**（本机 Docker 引擎 10-21 11:05 起停止；本段测试会碰 LLM 替身，按返工经验第 40 条也要先在沙箱带内存上限跑）。
交付：分支 `feat/replay-distill`，开**草稿 PR**，标题、描述、提交信息用英文。PR 描述第一句写「Not reviewed, do not merge」。

## 0. 为什么做（目标）

#198（P3 修法丙）已合并。花 P11 的钱之前，要用真模型小重放（祈灼、谢凛羽各 2 次）验证它修好没有，并且**失败时不重跑就能定位是哪一步错**。现有日志做不到：每组模型原始输出不落盘，重定位前的草稿不保存，分不出「G7 标错阶段号」还是「重定位挪错」。

本段只写工具、不跑真模型。段 2（在 SG 上跑）另给提示词。

| 假设 | 工具要输出的判据 |
|---|---|
| H1 标注不再堆在一个阶段 | 每条带阶段条目：模型标的阶段 vs 摘录位置所在阶段，一致率 + 各阶段条数分布。**只出数据，不判过 / 不过**：跟踪文档没有 H1 的判定线（P3 验收口径写的是「阶段对准真实转折，弧线小重放两次都对上真值表」，待 Tracy 确认），不由工具自定门槛 |
| H2 起点对准真实转折 | 验收清单 3 条逐条过 / 不过 + 每阶段起点章号 vs 真值 |
| H3 两次稳定 | 同一输入 N 次：阶段数、各起点章号并排 |
| H4 重定位触发与否 | 是否触发、触发前后起点 |
| H5 每组 8192 上限够不够 | 每次调用的输出 token 数 + 是否被判为截断。**截断判据复用生产唯一的那一处**（`core/distiller.py:964` `_truncation_evidence`、`:989` `_unfinished_disposition`，按 `length` 终态判），不自定 token 阈值 |
| H6 章内回忆 | 起点或条目落在第 383、399 章的列出 |

## 1. 环境硬规则

1. 不调真模型、不读不设任何 API key；测试一律用替身。
2. pytest 每条命令带 `(ulimit -v 8000000; timeout 300 …)`；只跑本段新增 / 受影响的测试文件，全量看分支 CI。
3. 测试库：沙箱 PostgreSQL 16 起在 55432（命令见下）；不要用 docker。
4. 任何命令被杀、超时、退出码非 0/1：停，贴原始输出，不重试。
5. 起库前先查冲突：`ss -ltn | grep 55432`、`ls /var/tmp/pgtest`；端口已占或目录已存在 → 停，报告，不要删（返工经验第 16 条）。

```bash
D=/var/tmp/pgtest; rm -rf $D; mkdir -p $D && echo ci_test_password > $D/pw && chown -R postgres $D
su postgres -c "/usr/lib/postgresql/16/bin/initdb -D $D/data -U charsim --pwfile=$D/pw -A md5"
su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D $D/data -o '-p 55432 -k /tmp' -l $D/log start -w"
PGPASSWORD=ci_test_password psql -h localhost -p 55432 -U charsim -d postgres -qc "create database charsim_test"
```
（10-21 在同类沙箱实测通过；数据目录不能放 `/tmp/claude-*` 下。克隆后执行 `git config core.hooksPath .githooks`。）

## 2. Skill

| 步骤 | skill | 用途 |
|---|---|---|
| S0 | `@search-first` | 先在仓库找可复用的部件，不另写 |
| S1 | `@tdd` | 第一个提交先写目标检查（`xfail(strict=True)`） |
| S2 | `@incremental-implementation` | 按下面的步骤小步提交 |
| 收尾 | `@verification-before-completion` | 报告里每个「通过」附原始输出 |

## 3. S0 与交付位置

- 第一个提交把本提示词原样存为 `docs/specs/replay-distill.md`（返工经验第 17 条）；之后的补充写回同一文件。
- S0：逐条复核下面 K1–K8，贴出实际读到的行；有一条不成立就停下报告（第 18 条）。U1–U5 读代码后给结论。
- 写代码前，在 spec 里先填「结构自检」表（第 36 条），三问逐条附代码行：同一条规则写了几处；调用方是否被迫知道不该知道的事；以后加一个假设 / 一个角色的真值要改几处（答案应是一处）。

## 4. 已查实的约束（@ `f92aa456`，本轮逐行读过）

| # | 事实 | 读到的行 |
|---|---|---|
| K1 | 生产 `/start` 的产卡链：名单 → 回忆区 → 出草稿 → 重定位 → 转卡 → 后置 → 存卡 | `web/routers/distill.py:540-575`：`resolve_characters(...)`、`resolve_retro_regions(...)`、`_distill_to_draft(...)`、`distiller.locate_phase_starts(data, content, name, retro)`、`card_from_draft(data, content, retro=retro)` |
| K2 | 回忆区按书缓存，命中不发 LLM | `core/retro_cache.py:24-38` `get_retro_regions_owned` 命中即返回 |
| K3 | 名单按书缓存 | `core/character_roster.py:98-115` |
| K4 | 「摘录位置 → 所在阶段」已有唯一实现，不另写 | `core/phase_anchoring.py`：`build_anchors`（:231）、`phase_at`（:206）、`verify`（:309，返回 `Verification`）、`chapter_labels`（:249，注释写「全仓唯一换算处」） |
| K5 | 蒸馏器唯一装配出口 | `web/deps.py:373` `get_distiller(llm)` → `Distiller(llm, storage=get_storage())`；全局 LLM 走 `_make_global_llm`（:348，读 config.yaml / `DEEPSEEK_API_KEY`） |
| K6 | 同类先例：入库的真模型重放脚本 | `scripts/replay_session_record.py`（判定线写在脚本顶部常量，按 spec 出结论） |
| K7 | **`scripts/` 不进镜像** | `.dockerignore:28` `scripts/`；镜像 `WORKDIR /app`（`Dockerfile:23`）、`COPY . .`（:42）→ 段 2 要 `docker cp` 进容器，并 `cd /app && PYTHONPATH=/app python /tmp/replay_distill.py`（返工经验⑤#5：曾因没带 PYTHONPATH 在 SG 第一次跑失败） |
| K8 | 被正文引用的数字只走证据出口 | `.claude/rules/project.md`「调试脚本不入 main」条；`tests/perf/evidence_writer.py` 的 `write_evidence(id, payload, claim=, script=, env=)` |

**未核实（执行方 S0 读代码确认，不成立就停下报告）**：
- U1 `verify` 返回的 `Verification` 里能否直接拿到「每条条目：标的阶段 / 摘录所在阶段 / 是否改挂 / 未定位」；拿不到时最少要加什么（只许加只读访问，不许改它的规则）。
- U2 `_distill_to_draft` 依赖 `_set_task` / `_tasks`；工具复用它还是直接调 `distiller.distill_incremental_stream` 收草稿——以「和生产走同一段代码、不复制解析逻辑」为准。
- U3 怎样拿到每次 LLM 调用的组名、输出原文、usage（`prompt_tokens` / `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` / `completion_tokens`）：包一层记录型适配器透传给真 `LLMAdapter`（不改生产代码），还是读 `last_usage`。读 `adapters/llm_adapter.py` 后定。
- U4 后台线程里的 DB 调用用 `submit_to_main_loop`；脚本独立进程时如何建 loop / storage——参照 `scripts/` 下已有的连库脚本，不另造。
- U5 这条链上的全部写库点（返工经验第 11 条：往已有路径加东西前先列已有机制）。已知至少三处：记账 `try_record_usage`（`core/utils.py:76`，身份读 `core.request_context.current_user_id()`，经 `submit_to_main_loop(wait=False)` 投递）；名单缓存 `storage.save_characters`（`core/character_roster.py:113`）；回忆区缓存 `storage.save_retro_regions`（`core/retro_cache.py:32`）。grep 出完整清单贴原始输出，逐个写：脚本里会不会走到、走到时写进哪张表、记在谁名下。缓存写入与生产同口径，可以接受；**记账会混进线上 `usage_stats`** —— 怎么区分重放和线上（例如身份或 action），读代码后列出可选做法**停下报告**，由 Shiyu 定，不改生产代码。

## 5. 要做什么

`scripts/replay_distill.py`：

```
python scripts/replay_distill.py --text-id <id> --name 祈灼 --runs 2 --out /tmp/replay-<时间戳> [--truth 祈灼]
```

1. 按 K1 走**同一条生产链**到 `card_from_draft` 为止（含回忆区、重定位），**不调 `finalize_card`、不打标签、不存卡、不写 `distill_tasks`**。
2. 每次运行落盘到 `--out/run-<n>/`：
   - `calls.jsonl`：每次 LLM 调用一行（组名或 action、输出原文、四个 usage 数、耗时）
   - `draft_before_locate.json`、`draft_after_locate.json`、`retro.json`
   - `phase_starts.json`：每阶段标签、起点章号、是否回忆区、是否触发重定位
   - `entries.tsv`：每条带阶段条目一行（字段、条目、标的阶段、摘录所在阶段（K4）、一致 / 改挂 / 未定位、所在章）
3. 汇总 `summary.json` 与终端表：H1–H6 每条的数值与「过 / 不过 / 看不出」；N 次并排。
4. 判定线写在脚本顶部常量，**照抄跟踪文档「祈灼卡修复 · 当前问题」验收清单与真值表**（Tracy 10-19 12:09 定，跑前写死、事后不改）：
   - 清单（逐字照抄跟踪文档原文）：
     1. 卡能选阶段：所有起点齐全、严格递增，日志里没有「整卡跳过」。
     2. 每个阶段起点都不在回忆区（第 491–504、508–509 章），也不在第 383、399 章的章内回忆。
     3. 与真值表对得上的阶段，起点落在真值范围内。
   - 真值：祈灼 1 孤冷求索 0；2 一见倾心 12–13；3 甘为底牌 122；4 甘愿放手 343–344。谢凛羽 2 心动沦陷 29–30；3 甘愿共享 460–461。
   - 「两次都过才算过」（返工经验第 34 条）。
5. ~~只在 `--evidence <id>` 时，把 summary 里的数字（不含原文摘录）经 `write_evidence` 登记（K8）；草稿、调用原文只留在 `--out`，不入库。~~ **已删除（复审 F1/F2）**：H4/H6 含原文锚点与条目正文，证据出口会把它们带进 `docs/evidence/`；summary 只留服务器 `--out`。
6. 成本：按 usage 汇总 token；**单价不写死在代码里**，由 `--price-*` 参数传入（段 2 执行前查 DeepSeek 官方价格页填）。

## 6. 不做

- 不改任何生产代码的行为；U1/U3 若必须给生产代码加只读访问，先停下报告，由 Shiyu 定。
- 不跑真模型（段 2 做）。不做 ② ④、P2、P5、P6 的任何修复。

## 7. 目标检查（第一个提交，`xfail(strict=True)`）

用替身 LLM（照生产行为：按 `CharacterCard[Gn]` 回组字段、关系分批回详情，流有终点——参照 `tests/test_longctx_focused_calls_goal.py::_group_llm`，复用不复制）+ 一段带章节标题的小文本：

- T1 工具跑完一次：`--out` 下 6 个文件都在；`calls.jsonl` 行数 = 实际 LLM 调用次数；不写 `distill_tasks`、不存卡（查库为空）。
- T2 `entries.tsv` 的「摘录所在阶段」与直接调 `core.phase_anchoring` 算出的一致（守「复用 K4、不另写」）。
- T3 判定：替身让阶段 2 起点落在真值外 → `summary` 里 H2 为「不过」；落在真值内 → 「过」。两次中一次不过 → 总结论「不过」。
- T4 替身某组输出 token 报 8192 → H5 标「疑似截断」。
- T6 照抄锁（第 33 条）：脚本里的清单原文、真值常量与本 spec §5 第 4 条逐字 / 逐值相等；删掉原文任一个词测试会红。
- T5 结构锁：脚本里不出现 `save_card`、`finalize_card`、`_auto_tag`、`create_distill_task` 等写路径名（源码检查 + 运行后查库，双查）。

## 8. 测试

本段新增的测试文件 + `tests/test_lock_coverage.py` + 受影响的既有文件（以 grep 为准，报告里列出 grep 原始输出），逐个带内存上限跑。推送前预检钩子要过。

## 9. 报告

结论先行：做了什么、是否符合本提示词、偏离了什么。附：S0 对 U1–U4 的结论（附代码行）、目标检查先红后绿的原始输出、每个测试文件末行、PR 链接。不宣称通过——复审另开会话。

---

## 返工经验对照（本提示词发出前逐条核过）

| 条目 | 依据 |
|---|---|
| 2 / 20 目标检查测「达成目标」 | §0 的 H1–H6 → §7 T1–T6，每条对应一个假设或一个「不写库」的目标 |
| 7 / 7a 判断附读过的行 | §4 K1–K8 每条附本轮读到的文件:行；读不到的列为 U1–U5「未核实」 |
| 7c 不写未核实的设计想法 | 记录型适配器、复用 `_distill_to_draft` 等都放在 U3/U2 由执行方读代码后定 |
| 8 全量扫描 | §8 受影响文件要求贴 grep 原始输出 |
| 9 一个 PR 一件事 | 只做工具；真模型运行另段 |
| 13 / 32 / 33 照抄权威来源 | 判定线逐字照抄跟踪文档验收清单与真值表（本轮读到的原文，rev 27）；T6 守逐字 |
| 1 先定目标与验收标准 | H2、H3 用跟踪文档的判定线；H1 没有判定线，只出数据不判（不为迁就工具自定门槛） |
| 16 起环境前查冲突 | §1 第 5 条 |
| 17 spec 入 docs/specs | §3 第一个提交 |
| 18 S0 逐条复核 | §3 |
| 36 结构自检先填 | §3 |
| 26 复用唯一实现 | 位置→阶段复用 `core.phase_anchoring`（K4，T2 守）；截断判据复用 `_truncation_evidence`（H5） |
| 34 靠模型的行为两次都过 | §5 第 4 条 |
| 35 替身照生产 | §7 指定复用 `_group_llm`（流有终点） |
| 39 环境硬规则 | §1 开头 |
| 40 碰替身先沙箱带内存上限 | 执行环境定为云端沙箱，§1 第 2 条 |
| 11 往已有路径加东西先列机制 | K7（镜像不含 scripts/、PYTHONPATH）；U5 列全部写库点，记账混入线上的问题停下报告 |
| 30 写和验分开 | §9 不宣称通过，复审另开 |

---

# S0 复核结果（2026-10-21）

基线 `f92aa456`，工作树 `.claude/worktrees/replay-distill`，分支 `feat/replay-distill`，`core.hooksPath=.githooks`。

## K1–K8 逐条

| # | 复核 | 实际读到的行 |
|---|---|---|
| K1 | 成立 | `web/routers/distill.py:543-544` `chars = submit_to_main_loop(resolve_characters(get_storage(), distiller, text_id, user_id, content))`；`:551-552` `resolve_retro_regions(...)`；`:558-559` `_distill_to_draft(...)`；`:566-567` `distiller.locate_phase_starts(data, content, name, retro)`；`:572` `card = card_from_draft(data, content, retro=retro)` |
| K2 | 成立 | `core/retro_cache.py:27-29` `cached = await storage.get_retro_regions_owned(...)` / `if cached: return RetroRegions.from_json(cached)`；`:30-32` 未命中才 `detect_retro_regions` 并 `save_retro_regions` |
| K3 | 成立 | `core/character_roster.py:109-111` 命中缓存即返回；`:112-114` 未命中 `to_thread(distiller.identify_characters, content)` 后 `save_characters` |
| K4 | 成立 | `core/phase_anchoring.py:231` `def build_anchors(...)`；`:206` `def phase_at(...)`；`:309` `def verify(...)`；`:249` `def chapter_labels(...)`（docstring：「全仓唯一换算处」） |
| K5 | 成立 | `web/deps.py:373` `def get_distiller(llm=None)`；`:383` `return Distiller(llm, storage=get_storage())`；`:348` `def _make_global_llm()` |
| K6 | 成立 | `scripts/replay_session_record.py:1-13` 顶部 docstring 写明重放走生产同一函数；`:28-32` 判定线常量（`FILL_KNOWN_MAX` / `BLOCK_TOKENS_MAX` / `MONOLOGUE_MARKERS`） |
| K7 | 成立 | `.dockerignore:28` `scripts/`；`Dockerfile:23` `WORKDIR /app`；`:42` `COPY . .` |
| K8 | 成立 | `.claude/rules/project.md:51`「调试脚本不入 main……该类的唯一路径」；`tests/perf/evidence_writer.py:112` `def write_evidence(...)`；`:134-140` 未注册 id 抛 `ValueError` |

## U1–U5 结论（附代码行）

**U1：`Verification` 的只读访问够用，不加生产代码。**
`core/phase_anchoring.py:44-66` 的 `Verification` 直接给出：`tag_targets[i][p]`（第 i 条标在阶段 p 的最终落点；空 = 没有位置证据）、`unlocated`（无任何位置证据的条目序号）、`starts`（各阶段起点）。逐字读到的字段注释：`:48-53`「`tag_targets[i][p]` 是模型标的阶段 p 按摘录位置最终落到的阶段（空 = 没有位置证据）」。工具据此分「一致 / 改挂 / 未定位」，不碰 `verify` 的规则。

**U2：不复用 `_distill_to_draft`，直接调 `distill_incremental_stream`。**
`web/routers/distill.py:367-378` 的 `_persist_chunk` 会 `get_storage().save_distill_chunk(...)`（写 `distill_chunks`）；`:386-388` 读 `_tasks` 判取消。工具不要这两件事，故按生产同一段 `distiller.distill_incremental_stream`（`core/distiller.py:2687`）收帧，用 `core.llm_json.loads_llm_json`（`:90`）解析最后那个 JSON 串——解析器与生产共用，不复制路由里的 fence/brace 代码。

**U3：包一层记录型适配器 + `Distiller` 子类只读重写 `_chat_accounted`。**
`adapters/llm_adapter.py:972` `chat` 把 `last_usage` 写成共享槽；`:1104` `_stream` 的成功用量经 `StopIteration.value` 交出（`:1175` `return usage`），不读共享槽；`:1014-1031` `async_chat` 返回 `(result, usage)`。适配器包装器在调用返回处记录原文与 usage；组名从 system 里的 `CharacterCard[Gn]` 取（`core/distiller.py:2586-2590` 拼这个标记）。`core/distiller.py:1183` `_chat_accounted` 是唯一知道 action/label 的入口，子类重写后 `super()` 再补记 action/label/truncated，不改进程内任何生产函数。

**U4：一个 `asyncio.run`，同步蒸馏丢 `asyncio.to_thread`。**
`web/deps.py:264` `get_storage()`（lazy `get_store()`）、`:373` `get_distiller(llm)`；`storage/postgres_store.py:221` `close()`。脚本不注册 `set_main_loop`，也不需要 `submit_to_main_loop`：整段重放包在 `core/request_context.py:54` 的 `system_llm_context()` 里，`core/utils.py:102-106` 读到 `current_user_id()` 为 `None` 即返回，不会投递任何记账协程。

**U5：写库点清单与决定。**
grep 到的本链写点（原始输出见报告）：

| 写点 | 脚本会走到？ | 写进哪张表 | 记在谁名下 |
|---|---|---|---|
| `try_record_usage` → `storage.record_usage`（`core/utils.py:76-120`，`storage/postgres_store.py:3400`） | 走得到，但在 `system_llm_context()` 下 `current_user_id()` 为 `None`，入口即返回 | 不写 | — |
| `storage.save_characters`（`core/character_roster.py:113`） | 走得到（缓存未命中时） | `texts.characters_json` | 文本属主（生产同口径） |
| `storage.save_retro_regions`（`core/retro_cache.py:32`） | 走得到（缓存未命中且 `final`） | `texts.retro_json` | 文本属主（生产同口径） |
| `save_distill_chunk`（`web/routers/distill.py:373`） | 不走（不调 `_distill_to_draft`） | `distill_chunks` | — |
| `_set_task` / `_tasks`（`web/routers/distill.py:392` 等） | 不走（进程内任务表，路由私有） | 不写 | — |

**决定（Shiyu 2026-10-21）**：选 A。整段重放用 `with system_llm_context():` 包住（`core/request_context.py:54`），不靠「不设 `LLM_CALLER`」——`web/llm_gate.py:65-67` 在调用者为 `None` 时会抛 `LLMCallerMissing`。**重放的用量只进 `calls.jsonl` / `summary.json`，不进 `usage_stats`**。名单/回忆区缓存写入与生产同口径，保留。

## 结构自检

| 问题 | 结论 | 依据 |
|---|---|---|
| 同一条规则写了几处 | 运行期只有一处：`scripts/replay_distill.py` 顶部 `ACCEPTANCE_CHECKLIST` / `TRUTH`；spec 里是文档副本，由 T6 逐字锁住，二者不会各自演化 | T6 直接比较脚本常量与 spec §5 第 4 条原文 |
| 调用方是否被迫知道不该知道的事 | 不需要：CLI 只知道 `--text-id/--name/--runs/--out`；组提示词、JSON 解析、位置核对、截断判定都在生产函数背后。仅只读地重写 `_chat_accounted` 取 action/label/truncated，不改其行为 | `core/distiller.py:1183`、`adapters/llm_adapter.py:1104` |
| 以后加一个角色真值 / 一个假设要改几处 | 角色真值：一处（`TRUTH` 加一项）。新假设：在 `_replay` 的 `summary` 加一个 `Hn` 键 + 一个纯函数；判定逻辑集中在 `_checklist`（H2）/ `_h1` / `_h5`，不散落在输出层 | `scripts/replay_distill.py` 的 `_checklist`、`_h1`、`_h5` |
| 运行路径 | 测试文本 3,166 tokens < 900,000，走生产的一次读完路径（7 个组调用）；判定逻辑与分片路径无关 | `core/distiller.py:1900-1905`、`core/length_budget.py:49` |

## 环境偏离（必须报告）

- 提示词假设 Linux 云端沙箱（原生 PG 16 + `su postgres` + `/var/tmp/pgtest`）。实际执行机是 Windows 11 + Git Bash + Docker 29.4.3，没有 `psql` / `su postgres` / 原生 55432 实例。无法执行 §1 的初始化命令。
- Shiyu 2026-10-21 选定：用 `docker compose -f docker-compose.test.yml up -d --wait` 起 55432 的 `charsim_test`。这不改测试语义（`tests/conftest.py` 只在会话开始核库名以 `_test` 结尾）。
- 跟踪文档「祈灼卡修复 · 当前问题」不在仓库里；验收清单原文只能取自本提示词，真值表另与 `docs/specs/phase-locate.md:38-44` 交叉核对一致。spec 里注明来源。
- ~~`tests/perf/evidence_writer.write_evidence` 拒绝未注册 id；本 PR 注册 `replay-distill` 白名单（只收统计量），否则 `--evidence` 无出口。~~ **已删除（复审 F1/F2）**：白名单与 `--evidence` 一并撤回，`write_evidence` 对本工具回到未注册状态。
