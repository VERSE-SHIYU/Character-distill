# E7-2：用存下的草稿重新出卡（不重蒸）

> v2（10-19 12:10，对照 rework-lessons 自查后修订，替换 12:05 的 v1）：G1 改为验证「重定位后的草稿真被用上」；G3 计数改对；第四节写明要改的 4 个 return 行号；补机制表、调用点矩阵、行为副作用。

基线：main `b187a499`（含 #185）。分支 `feat/card-redraft`，新 worktree。
目的：改了转卡规则（如 #185 的阶段起点重定位）之后，已有的卡用蒸馏时存下的草稿免费重新出卡，不再付费重蒸。
范围：只做后端接口。不碰前端。

## 零、与隔壁 PR（refactor/dedupe-export-limiter-eol）并行的约束

- 不改 `web/routers/distill.py` 的 `export_card`（:1463–1509）及其上下 10 行。新路由放在 `dismiss_examples_pending`（:1386–1406）之后、`move_unlocated_item`（:1409）之前。
- 不改 `tests/conftest.py`。任何测试文件里都不写 `limiter.reset(`，也不写 `limiter.limit =`（隔壁合并后它的 G4 会扫这两处）。新路由不加 `@limiter.limit`（与 `/start` 一致，靠按用户并发闸限流）。
- 不改 `web/frontend/` 下任何文件。
- 本地测试和隔壁错开跑（两边共用 Docker PG）。

## 一、目标检查（第 1 个提交只放这些检查；G1、G2、G4 标 `xfail(strict=True)`，之后每个提交都跑）

新建 `tests/test_card_redraft_goal.py`。替身和搭 app 全部复用 `tests/test_distill_draft_persist.py`：`_DraftStub`（:74–110）、`_install`（:161–180）、`_run_start`（:182–194）、`_seed_text`、`_only_card_id`、`_load_payload`；`_build_client` 从 `test_distill_task_api` 导入（照该文件 :28–33 的写法）。

- **G1 用草稿重新出卡，不重蒸**：
  1. 先用 `_run_start` 蒸一次，拿到 `card_id`。
  2. 用 `store.update_card(card_id, 改过的 dict, expected=原 card_json)`（sqlite 版签名见 `storage/sqlite_store.py:1261`）把卡改掉一个字段（比如 `background` 换成另一个串），模拟线上卡被手改过、和草稿不一致。
  3. 换一个替身：继承 `_DraftStub`，`distill_incremental_stream` 被调用就抛 `AssertionError("重新出卡不应重蒸")`，`fill_relationships` 同样抛；`locate_phase_starts` 记下调用次数，并且**就地改草稿**（模拟 #185 换了锚点）：把草稿里 `background` 改成一个只在这里出现的独有串 `_RELOCATED`。用它重新 `_install`。
  4. `POST /api/distill/card/{card_id}/redraft`，得到 200 和 `task_id`；join 后台线程。
  5. 断言：`GET /api/distill/task/{task_id}` 的 status 是 `done`，`card_id` 与第 1 步相同；`locate_phase_starts` 恰好调用 1 次；库里卡片的 `background` 等于 `_RELOCATED`（证明转卡用的是重定位之后的草稿，第 2 步的手改被盖掉）；再按 `test_stored_draft_round_trips_to_the_saved_card`（:266–288）的方法，用此时存下的草稿重建，与库里卡片逐字段相等（去掉 `POST_FORMAT_FIELDS`）。
- **G2 没草稿 / 不是自己的卡**：
  - 直接 `store.save_card` 存一张卡，不存草稿：redraft 返回 409，消息含「没有存草稿」。
  - 另一个用户请求别人的卡：返回 404。
  - 两种情况都不起线程（`_install` 返回的 `threads` 为空），也不建任务行。
- **G4 和 `/start` 共用按用户并发闸**：先 `D._reserve_user_slot(uid)` 占住，redraft 返回 429；测试结束 `D._release_user_slot(uid)`。
- **G3 不新增第三份转卡流程**（结构锁，现状即绿，不标 xfail）：读 `web/routers/distill.py` 源码，子串 `save_distilled_card(` 恰好 2 次（:585 后台线程、:1213 `/run_stream`），子串 `locate_phase_starts` 恰好 2 次（:550、:1187）。不数 `card_from_draft(`：:1193 写成 `asyncio.to_thread(card_from_draft, ...)`，数括号会漏。

## 二、S0 开工前核对（任何一条对不上就停下报告）

先确认 `docs/specs/card-redraft.md` 在当前 worktree 里。

| 位置 | 应看到 |
|---|---|
| `web/routers/distill.py:250-253` | `class DistillTaskRequest`：`text_id`、`character_name=""`、`force=False` |
| `web/routers/distill.py:356-361` | `def _run_distill_task(task_id, text_id, char_name, force, user_id, content, text_type, llm, embedding_key="", embedding_region="", resume_candidates=None)` |
| `web/routers/distill.py:412-419` | 线程里解析名单 `resolve_characters` 与回忆区 `resolve_retro_regions` |
| `web/routers/distill.py:423-547` | 「Step 2」蒸馏 → 解析 JSON → `fill_relationships`（:543）。块内顶层的 `return` 恰好 4 个：:455、:460、:538、:547；嵌套函数 `_persist_chunk`（:429）、`_save`（:434）里没有 `return`。:550–640 不再用到块内定义的变量（`full`、`full_format`、`cur_phase`、`stripped`、`EXPECT_CHARS`） |
| `web/routers/distill.py:550` | `distiller.locate_phase_starts(data, content, name, retro)` |
| `web/routers/distill.py:554` | `card = card_from_draft(data, content, retro=retro)` |
| `web/routers/distill.py:585-589` | `save_distilled_card(..., draft=data, character=name)` |
| `web/routers/distill.py:640` | `_set_task(task_id, update_dict)`（status done） |
| `web/routers/distill.py:779-804` | `distill_start`：`_reserve_user_slot` → `count_running_distills` → `_distill_start_impl` → 异常时 `_release_user_slot` |
| `web/routers/distill.py:855` | `if not req.force:`（force 时不找可续跑任务） |
| `web/routers/distill.py:925-929` | `C.ctx_thread(_run_distill_task, args=(...))` |
| `web/routers/distill.py:1193`、`:1213-1217` | `/run_stream` 里的 `card_from_draft`、`save_distilled_card` |
| `storage/postgres_store.py:3824` | `load_card_draft_owned(card_id, user_id) -> str`（没有或非属主返回 ""）；sqlite 版 `storage/sqlite_store.py:4568` |
| `core/text_manager.py:587-589` | `save_distilled_card` 落卡前 `_guard_card`（注入审核） |
| `core/text_manager.py:597-601` | 草稿存为 `{"character": ..., "draft": ...}`。存的是 :543 补完关系、:550 重定位之后的 `data`（:588 `draft=data`），所以重新出卡不用再补关系 |
| `storage/postgres_store.py:686-700` | `save_card` 按 `text_id + name + user_id` 查已有行，有就 `UPDATE card_json`，card_id 不变 |
| `core/text_manager.py:623-631` | `save_distilled_card` 每次都新建一个会话（`_create_session`）并 `save_session` |

全量扫描原始输出（基线 b187a499）：
```
$ grep -rn "card_from_draft(" --include=*.py . | grep -v "^./tests"
./web/routers/distill.py:554:            card = card_from_draft(data, content, retro=retro)
./docs/specs/artifacts/arc_phase_fields_perf.py:85:        card_from_draft(draft, SOURCE)
./docs/specs/artifacts/arc_phase_fields_perf.py:92:    card = card_from_draft(draft, SOURCE)                     # 先跑一次，确认样本有效
./docs/specs/artifacts/arc_phase_fields_perf.py:106:        card_from_draft(draft, SOURCE)
./core/distiller.py:1800:            return card_from_draft(data, text, retro=NOT_DETECTED)
./core/distiller.py:1904:            return card_from_draft(data, text, retro=NOT_DETECTED)
./core/distiller.py:2555:            card = card_from_draft(data, text, retro=NOT_DETECTED)
./core/card_draft.py:421:def card_from_draft(data: Any, source_text: str, *, retro: RetroRegions) -> CharacterCard:
（distill.py:1193 的那处写成 asyncio.to_thread(card_from_draft, ...)，上面的模式扫不到）

$ grep -rn "load_card_draft_owned(" --include=*.py . | grep -v "def "
./tests/test_postgres_store.py:509 / :515 / :520
./tests/test_distill_draft_persist.py:215 / :260 / :363
（生产代码没有调用方）
```

## 三、行为变化（只有这一条）

| # | 变化 |
|---|---|
| B1 | 新增 `POST /api/distill/card/{card_id}/redraft`：用存下的草稿在后台重新出卡，覆盖同一张卡（同 text_id + 卡名 upsert，card_id 不变），返回 `{"task_id"}`，进度沿用 `GET /api/distill/task/{task_id}` 轮询 |

`/start`、`/run_stream` 的行为一律不变。

重新出卡的副作用和重新蒸馏完全一样（走的是同一段代码），写在这里让人知道，不是要改：
- 整张卡被草稿重建的内容替换，手动编辑会被冲掉；
- 新建一个会话（`core/text_manager.py:623-631`）；
- 重新跑引文核对、对话示例、打标签、注入审核、苏醒台词，都是小的模型调用；阶段起点只有核对不过时才调模型（`core/distiller.py:1075` 起）。花费未实测，按总文档估每张卡几分钱以内；
- 卡名在编辑页被改过的话，upsert 按草稿里的卡名找不到原卡，会多出一张新卡（`storage/postgres_store.py:686-700`）。祈灼的卡是否改过名未核实。

## 四、提交 2：后台任务支持「从草稿开始」

关键代码与做法：

1. **`_run_distill_task` 加关键字参数 `draft: dict | None = None`。**
2. **把 :423–547（蒸馏 → 解析 → 补关系）原样挪成一个函数 `_distill_to_draft(...) -> dict | None`**：函数体逐字搬过去；块内顶层的 4 个 `return`（:455、:460、:538、:547）改成 `return None`，嵌套函数 `_persist_chunk` / `_save` 原样不动；末尾加 `return data`。参数按函数体实际用到的变量传，不加新逻辑。返回 None 即它已经 `_set_task` 了错误状态。
3. 在原位置写：
   ```python
   data = draft if draft is not None else _distill_to_draft(...)
   if data is None:
       return
   ```
   之后从 :550 `locate_phase_starts` 起的转卡、后置、打标签、落卡（含注入审核）、待补、苏醒台词、done，全部不动、两种来源共用。
4. 名单、别名、回忆区（:412–419）照旧在线程里解析，草稿来源也走这一段。

结果：后台线程里只有一份「草稿 → 卡 → 落库」，重新出卡和首次蒸馏走同一条路，以后改转卡规则只改一处。

## 五、提交 3：新路由 + 共用并发闸

1. **把 `distill_start`（:779–804）的闸抽成一个函数**，例如 `async def _gated_start(user_id, storage, start: Callable[[], Awaitable[dict]]) -> dict`：占坑 → `count_running_distills` 复核 → `await start()` → 任何异常释放槽位再抛。`distill_start` 改为调它，行为不变。
2. **`_distill_start_impl` 加关键字参数 `draft: dict | None = None`**，原样传进 `C.ctx_thread` 的 args（作为 `_run_distill_task` 的 `draft`）。
3. **新路由**（放在 :1406 之后、:1409 之前）：
   ```python
   REDRAFT_NO_DRAFT = "这张卡没有存草稿，只能重新蒸馏"

   @router.post("/card/{card_id}/redraft")
   async def redraft_card(card_id, user=Depends(get_current_user), storage=Depends(get_storage)):
   ```
   流程：
   - `get_card_owned(card_id, user_id)` 为空 → 404 `"Card not found"`（非属主与不存在同判，同 :1316）。
   - `load_card_draft_owned(card_id, user_id)` 为 "" → 409 `REDRAFT_NO_DRAFT`。
   - `json.loads` 后取 `payload["character"]`、`payload["draft"]`。
   - `req = DistillTaskRequest(text_id=record["text_id"], character_name=payload["character"], force=True)`。`force=True` 让它不去找可续跑任务（:855）。
   - `return await _gated_start(user_id, storage, lambda: _distill_start_impl(req, user, storage, draft=payload["draft"]))`。
   - LLM 未配置（503）、原文不存在（404）、建任务行失败（503）都由 `_distill_start_impl` 现有逻辑给出，不重写。

## 五点五、已有机制（加了新入口后逐个核对，都不变）

| 机制 | 位置 | 对重新出卡 |
|---|---|---|
| 按用户并发槽 | :112–124、:779–804 | 共用 `_gated_start`，同一用户正在蒸馏时 redraft 返回 429（G4） |
| 全局蒸馏信号量 | :381 `_DISTILL_SEMAPHORE.acquire(timeout=300)` | 共用；排队行为同蒸馏 |
| 任务行 + 内存状态 | :889–916 建行、:918–922 `_tasks` | 共用；`force=True` 一律新建行 |
| 续跑发现 | :855 | `force=True` 跳过。redraft 失败留下的 error 行，之后点「蒸馏」时可能被当成可续跑行复用（同 user、text、角色名）；它没有分片，结果就是整跑，和新任务一样 |
| 取消 | `DELETE /task/{task_id}`、按文本取消 | 取消检查只在蒸馏流循环里（:448–455）。草稿来源不走蒸馏流，取消后线程仍会跑完落卡（约 1 分钟）。重新出卡很短，本 PR 不加取消检查，报告里写一句 |
| 落卡超时 | `submit_to_main_loop(_save_card(), timeout=120)` | 共用 |

## 五点六、调用点矩阵

| 入口 | 草稿来源 | 测试 |
|---|---|---|
| `POST /start` | 现蒸 | `tests/test_distill_draft_persist.py`、`tests/test_distill_task_api.py`（不改，必须保持绿） |
| `POST /card/{id}/redraft` | 存下的草稿 | G1、G2、G4 |
| `POST /run_stream` | 现蒸 | 不改；`test_run_stream_persists_the_draft` 保持绿 |

## 六、结构自检

| 问题 | 改后 |
|---|---|
| 「草稿 → 卡 → 落库」在后台线程里写了几处 | 1 处（`_run_distill_task` 的后半段），首次蒸馏与重新出卡共用 |
| 按用户并发闸写了几处 | 1 处（`_gated_start`） |
| 起后台任务写了几处 | 1 处（`_distill_start_impl`） |
| 注入审核 | 复用 `save_distilled_card` 里已有的，不另写 |
| 以后改转卡规则 | 只改转卡代码，已有的卡调 redraft 即可，不付费重蒸 |

已知且本 PR 不处理：`/run_stream`（:1186–1240）里另有一份「重定位 → 转卡 → 后置 → 落卡」，渲染方式（SSE 帧）不同。报告里写一句即可，不要改。

## 七、验证

本地（Docker PG），只跑受影响的文件：
```
pytest -q tests/test_card_redraft_goal.py tests/test_distill_draft_persist.py tests/test_distill_task_api.py tests/test_ownership_404.py tests/test_security_authz.py
```
全量回归交给分支 CI，gate / 前端 / sentinel 全绿才算过。

报告内容：
- 每个提交的 `git show --stat`；
- 第 1 个提交 G1、G2、G4 的红色输出（xfail strict），最后一个提交的绿色输出；
- 上面命令最后的汇总行；
- CI 结果。

末尾附「已核实 / 未核实」。范围内的问题直接修，碰到冲突或需要决定的事项才停下。

## 八、本 spec 的已核实 / 未核实（作者在 b187a499 上读过的行）

已核实：第二节表里每一行；:855 是 `force` 在请求侧唯一的用处，`_run_distill_task` 函数体里不读 `force`（只在 :357 签名、:927 传参出现）；块 :423–547 的 4 个顶层 return 和嵌套函数；草稿存的是补关系、重定位之后的 `data`；`save_card` 按名 upsert；`save_distilled_card` 每次新建会话。

未核实：生产库里祈灼 10-18 的草稿在不在（合并部署后第一次调 redraft，返回 409 就是没有）；祈灼的卡改没改过名；重新出卡的实际花费和耗时。
