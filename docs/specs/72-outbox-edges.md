@incremental-implementation @search-first @tdd

# 72 线 · 撤回不丢队、按幂等键对账、消息丢失告警

## 目标
撤回失败时不再连带丢掉队里未落库的消息；点「重试」时以数据库为准回答每条「未保存」消息的真实状态（已存 / 仍在排队 / 已丢失）；消息真正丢失的两个时刻（关停时仍未落库、对账判定已丢失）记 ERROR。

## 已定决策（Shiyu 拍板）
- 第 8 项：撤回改为「持队列锁 → 先删库 → 成功后清队」
- 第 9 项：方案 A（按 `client_key` 对账），**只在点「重试」时触发**，不做页面切回前台自动对账
- 丢失一律 `logger.error`（进现有日志管道；可观测性线接入 GlitchTip 后自动上报），不新增机制

## 已查实的约束（基线 origin/main `c695e7b`）
1. 撤回：`web/routers/chat.py:678 revoke_messages` → `:697 session["outbox"].clear()` → `:700 storage.delete_messages_after(...)`。删库抛错时 500 由统一出口处理，但队列已清，队里未落库的消息丢失
2. 队列：`core/message_outbox.py:100 MessageOutbox`，队列 `self._queue: list[(key, write_fn)]`、锁 `self._lock`（:106）；`flush` 持锁（:131–134）；`clear()`（:177）不持锁；`discard()`（:170）不持锁
3. 对账依据：写入时的幂等键就是队列 key（`write` 里 `uuid4().hex`，经 `write_fn(key)` 作为 `client_key` 落库）；PG 上 `messages(session_id, client_key)`、`group_messages(group_id, client_key)` 各有部分唯一索引（`storage/migrations_pg/026_message_client_key.sql:18–24`）；前端 `saveKey` 即此 key
4. 读数形状：`FlushReport.as_json()`（:76）= `{"flushed": [{"key","id"}], "dropped": [key]}`；前端 `web/frontend/src/utils/withSaveResult.js::applyFlushReport` 按 key 填 id / 标 `failed`，认不出的 key 跳过
5. 重试入口：一对一 `chat.py:714 flush_session_messages`（`_ensure_session` 做属主门，会话被逐出后会重建出**空队列**）；群聊 `web/routers/group.py:570 flush_group_messages`（`_get_owned_group`）。两者当前只返回 `outbox.flush()` 的读数，请求体为空 `{}`
6. 前端调用：`web/frontend/src/store/useAppStore.js:1544 flushMessages` → `postJSON('/api/chat/${sessionId}/flush', {})`；`web/frontend/src/components/GroupChatPage.jsx:755 flushGroupMessages` → `postJSON('/api/group/${id}/flush', {})`；重试按钮只在 `saveState === 'pending'` 时出现（`UnsavedHint`）
7. 关停：`web/server.py:141–146` 调 `flush_outboxes` 后只 `print` 补上的条数；库不可达时剩余消息随进程退出消失，无任何记录
8. 存储契约锁：`tests/test_storage_contract_shape.py` 要求各实现与 `storage/base.py` 签名逐格相同 —— 新方法要同时加到 base / postgres_store / sqlite_store

**S0**（只核坐标，几分钟）：逐条读代码复核 1–8，不跑测试；不成立就停下报告。

## 约束
- 对账逻辑只写一处：`MessageOutbox` 上的一个方法，chat / group 两个入口都调用它；存储只提供「按 key 查 id」
- 响应体仍是 `FlushReport` 同形，前端 `applyFlushReport` 不改
- 请求体 `keys` 可选：不传 = 旧行为（只补写）；限定最多 200 个、每个是 32 位十六进制，否则 422
- 只查属主自己会话 / 群聊范围内的 key（查询按 `session_id` / `group_id` 约束）
- 丢失告警只记 key、会话 / 群聊 id、条数，不记消息正文
- 新发现属本段改动面的直接修；会撞车或需拍板的停下报告；不自行记账

## 步骤（按依赖顺序，每步独立 commit）

### 步骤 1：[core+web] 撤回持锁、先删库后清队
- `MessageOutbox` 新增 `async def clear_after(self, action)`：持 `_lock` 执行 `await action()`，成功后清队并返回其结果；`action` 抛错则队列原样保留、异常上抛
- `revoke_messages`：`count = await session["outbox"].clear_after(lambda: storage.delete_messages_after(req.session_id, req.message_id))`，删掉 `:697` 的裸 `clear()` 与那段「先清队再删库」注释，改写为新顺序的理由（持锁使补写无法插入两步之间）
- 测试（先红）：① 删库抛错 → 队里的 pending 仍在；② 删库成功 → 队空；③ 删库进行中另起一次 `flush` → 等撤回结束后才执行，且不会把已撤回范围的消息写回
- commit：`fix(outbox): revoke deletes first and clears the queue only after, under the queue lock`

### 步骤 2：[storage] 按幂等键查行 id
- base / postgres / sqlite 各加 `find_message_ids_by_client_keys(session_id, keys) -> dict[str, int]` 与 `find_group_message_ids_by_client_keys(group_id, keys) -> dict[str, int]`；空列表直接返回 `{}`
- 测试：存两条带 key 的消息，查三个 key（两存在一不存在）→ 返回两项；跨会话同 key 查不到；契约锁通过
- commit：`feat(storage): look up message ids by client key`

### 步骤 3：[core+web] 按 key 对账
- `MessageOutbox` 新增 `async def reconcile(self, keys, *, ping, lookup) -> FlushReport`：先 `flush`（持锁）；对 `keys` 里既不在本次读数、也不在队里的，用 `lookup(keys)` 查库 —— 查到的并入 `flushed`，查不到的并入 `dropped` 并 `logger.error("queued message lost: scope=%s keys=%s", ...)`
- 两个 flush 入口：解析可选 `{"keys": [...]}`（按约束校验），有则调 `reconcile`（`lookup` 绑定各自的存储方法与 id），无则保持 `flush`
- 测试（先红）：① 会话被逐出后重建（空队列）+ 库里已有该 key → 返回 `flushed`；② 库里没有、队里也没有 → `dropped` 且记一条 ERROR（caplog）；③ 仍在队里 → 两边都不出现（保持 pending）；④ 非属主 404；⑤ 非法 key → 422。chat、group 各一组
- commit：`feat(outbox): reconcile pending messages against the database by client key`

### 步骤 4：[frontend] 重试时带上 pending 的 key
触发链：
- 一对一：点 `UnsavedHint` 重试 → `flushMessages()` → 从当前 `messages` 取 `saveState === 'pending'` 的 `saveKey` → `postJSON('/api/chat/${sessionId}/flush', { keys })` → `applyFlushReport` 按读数更新
- 群聊：点重试 → `flushGroupMessages()` → 同样收集 pending key → `postJSON('/api/group/${id}/flush', { keys })` → `applyFlushReport`
- 没有 pending 时仍发空 `keys`（行为同旧）
- 测试（vitest）：请求体里带上且只带上 pending 的 key；读数里 flushed 的那条标记消失、dropped 的变 `failed`
- commit：`feat(ui): send the pending keys when retrying so the server can reconcile`

### 步骤 5：[web] 关停时丢失告警
- `web/server.py` 关停 flush 之后，遍历一对一与群聊会话表，对仍 `has_pending` 的逐个 `logger.error("queued messages lost at shutdown: scope=%s count=%d", ...)`
- 测试：库不可达时跑关停段（沿用 `test_message_backfill.py::test_C9` 的 lifespan 驱动方式与 `main_loop_registered`），caplog 里出现该 ERROR 且带正确条数
- commit：`fix(shutdown): log queued messages that are lost at exit`

### 步骤 6：[docs] 台账
- `AGENTS.md` 条目 94 追加一句（不新开条目）：撤回改为持锁先删后清、重试按 `client_key` 对账、关停与对账两处丢失记 ERROR（附步骤 1–5 的 commit）；台账头部状态行只在计数变化时改
- commit：`docs(ledger): note 94's revoke order, reconcile and loss alerts`

## 测试
- 本地只跑受影响文件（Docker PG）：`test_message_outbox.py`、`test_message_backfill.py`、`test_group_save_failure.py`、`test_client_key_idempotency.py`、`test_storage_contract_shape.py`、`test_ownership_404.py`，以及按改动的函数名 / 路由路径 grep `tests/` 得到的其余文件；前端 `npm test`
- 不跑全量；合并门是分支 CI

## 验证
- 变异（各一次，跑完还原）：`clear_after` 改回先清后删 → 步骤 1 ① 变红；`reconcile` 去掉查库 → 步骤 3 ① 变红；去掉丢失的 `logger.error` → 步骤 3 ② 与步骤 5 变红；前端不带 `keys` → 步骤 4 变红
- 推送、开 PR、分支 CI 绿后等审计；审计通过由执行方合并（Create a merge commit，不要 squash / rebase）。**合并只做 git 操作：不跑测试、不等 CI**
- 报告：S0 逐条结果、各 commit 与 `--stat`、先红后绿、变异表、本地测试清单与末行、CI 链接；不报本地全量数字

## 需 Shiyu 手测（静态与单测都证明不了的运行时行为）
1. 停掉本地库 → 发一条消息 → 出现「未保存」+ 重试
2. 恢复库 → 点重试 → 标记消失，引用 / 反应入口恢复
3. 停库 → 发消息 → 恢复库 → 等会话被闲置清理（可临时调小 TTL）→ 回到页面点重试 → 标记消失（这是本段新覆盖的场景）
4. 撤回时让删库失败（例如删库瞬间停库）→ 撤回报错，之前「未保存」的消息仍可重试
