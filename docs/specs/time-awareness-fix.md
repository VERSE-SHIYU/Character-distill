# spec：时间感知修正（PR #164 验收不过的那一条）

基线：main `80f038f7`（含 #163、#164）。放进 worktree 的 `docs/specs/`，并在 `docs/specs/arc-attitude-time-boundary.md` 末尾加「补充 3：验收不过与修正」一节指向本文件。

## 问题（SG 验收 10-15 已实测）

问「现在几点了」，角色答剧情时间「辰时」。实测 14 轮，没有一轮注入时间块。两个原因，都是原 spec B1 的设计错：

| # | 原因 | 坐标 |
|---|---|---|
| P1 | 「同一时段、10 分钟内不注入」把整块都关了，模型手里没有现实时间，「对方问起现实时间时才按这里回答」那句也一起没了 | `core/time_awareness.py` `build_block` 开头 `if not should_inject(...): return ""` |
| P2 | 首轮判定用 `first_turn=not self.history`，但开场白已写进 history，新会话第一轮也判成非首轮 | `core/chat_engine.py` `_build_time_awareness_block` |

原测试没发现：测试替身的 history 在首轮是空的，与生产（首轮已有开场白）不一致（返工经验第 35 条）。

## 改法

`core/time_awareness.py` 拆成两个纯函数，各管一件事；`ChatEngine` 只负责把它们放到对的位置：

1. **现实时间事实**（例 `reality_line(now)`）：「【现实时间】现在是{时段}（HH:MM）。对话里已写明剧情时间（如『第二天早上』）时以剧情为准；对方问起现实时间时才按这里回答。」**每轮都给，放进系统提示**，由 `_build_all_enhancements` 拼入（普通聊天与重逢问候都经过它，`core/chat_engine.py:360`、`:1497`）。**不再接在用户消息末尾。**
   - 为什么换位置：注入离提示末尾越近、对下一条回复影响越大（[SillyTavern Author's Note 文档](https://docs.sillytavern.app/usage/core-concepts/authors-note/)：「The closer the Author's Note is to the bottom of the prompt, the more impact it has」）。这一行只是供模型查用的背景事实，要的是「有」而不是「显眼」，放系统提示里最不容易被每轮复述。原 D-1 每轮重复，就是因为它接在用户消息末尾、还带示范。
   - 缓存不受影响：系统提示每轮本来就含好感、情绪、内心独白（`_build_affinity_persona_block`），已是每轮变化的。
2. **间隔句**（例 `interval_note(now, last_activity_at)`）：距上次 ≥ 600 秒才返回「你们昨天聊过」等句，否则空串（时段变化不再单独触发：现实时间行每轮都有；原「你们刚刚还在聊」一档随之删除）；`last_activity_at` 为空返回空串。**仍接在用户消息末尾**（`_attach_turn_blocks`），因为它只在真隔了一段时间时才出现，是这一轮该注意的事。
3. **删掉 `first_turn` 参数与 `should_inject` / `build_block`**（`core/time_awareness.py`、`core/chat_engine.py:1173-1174`），不保留旧名。
4. `_build_time_awareness_block` 改名为只表达它现在做的事（返回间隔句），同步改 `tests/test_storage_scope_lock.py`、`tests/test_reunion_greeting.py`、`tests/test_life_exchange.py`、`tests/test_arc_behavior_inject.py` 里的替身与 `docs/specs/artifacts/arc_behavior_inject_mutations.py` 的锚点。

## 测试

- 本地只跑 `tests/test_time_awareness.py`、`tests/test_clock.py`、`tests/test_reunion_greeting.py`、`tests/test_life_exchange.py`、`tests/test_arc_behavior_inject.py`、`tests/test_storage_scope_lock.py`。合并门是分支 CI。
- 测试替身照生产：构造引擎时 history 里先放一条开场白。
- 新增断言：
  - 系统提示每轮都含现实时间行与剧情时间句（间隔 1 分钟、同时段也有）。
  - 用户消息末尾：间隔 1 分钟同时段 → 无间隔句；间隔 12 小时 → 有。
  - history 里只有开场白、`last_activity_at` 是 30 秒前：系统提示有现实时间行，用户消息末尾无间隔句。
  - 用户消息末尾不再出现现实时间行；全部注入文字不含「怎么还不睡」。
  - 重逢问候的系统提示也含现实时间行。

## 对账表（复核方验，自补放宽、过严两个方向的变异）

| 行为 | 守它的测试 | 让它变红的变异 | 触发状态 |
|---|---|---|---|
| 现实时间每轮都给 | 间隔 1 分钟系统提示仍有现实时间行 | 给现实时间行加门控 | 连续两轮 |
| 现实时间不在用户消息末尾 | 末尾不含「【现实时间】」 | 把它挪回 `_attach_turn_blocks` | 任一轮 |
| 间隔句受门控 | 间隔 1 分钟无间隔句 | 间隔句恒加 | 连续两轮 |
| 新会话首轮有现实时间 | history 只有开场白时系统提示有现实时间行 | 以「history 为空」为条件才给 | 新会话第一轮 |
| 无行为示范 | 块不含「怎么还不睡」 | 恢复示范句 | 任一轮 |

## 技能

| 步骤 | skill | 用途 |
|---|---|---|
| 改动 | `@tdd` | 先写失败测试（含开场白的替身）再改 |
| 交付 | `@verification-before-completion` | 贴受影响测试原始输出 |

## 交付

单独一个 PR，英文标题、描述、commit。不写部署步骤。不调模型、不花钱。

## 补充 1（实现记录，2026-10-15）

- 实现：`reality_line` / `interval_note`（`core/time_awareness.py`），`_build_interval_note`（`core/chat_engine.py`，取代 `_build_time_awareness_block`）；现实时间行由 `_build_all_enhancements` 拼入，故普通聊天、重逢问候、群聊（`core/group_session.py` 三处调用）都带上。
- 重逢问候的指令改一句：「（间隔见上方，时段见【现实时间】）」。
- 受影响测试本地（PG 16）：`test_time_awareness` `test_clock` `test_reunion_greeting` `test_life_exchange` `test_arc_behavior_inject` `test_storage_scope_lock` `test_catchwords` `test_session_record_goal` `test_group_session_record_goal` 共 199 passed；`test_group_*` + `test_message_evidence` 59 passed；`arc_behavior_inject_mutations.py` 30/30 全红、源文件逐字节还原。
- 作者自跑的 6 条变异（门控现实时间行、挪回末尾、间隔句恒加、阈值 `<=`、恢复示范句、删剧情句一词）全红。按规矩，独立复核方须自补放宽、过严两个方向。
