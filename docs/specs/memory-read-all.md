# 长期记忆「读全量」真的读全（修「最多只读到 20 条」）

放到 `docs/specs/memory-read-all.md`。基线 main `224951a`（#135 合并后）。分支 `fix/memory-read-all`，开 PR。

这是 ④ 的第 2 个 PR，小改动：实现只动一个方法。

## 1. 目标

一个存档（或一个群）里的长期记忆不管有多少条，下面 4 个「读全部」的地方读到的都是全部，不再只是其中 20 条：

| 读的地方 | 读不全的后果（现在就是这样） |
|---|---|
| 到期事件 `core/event_service.py:39` | 该问的事不问；问过的事因为读不到「已问过」标记而再问一次 |
| 重逢问候 `core/chat_engine.py:1568` | 取不到最重要的那两条记忆 |
| 反思 `core/reflection_service.py:64` | 高质量素材数不够，反思一直不触发 |
| 记忆面板列表 `web/routers/memory.py:107` | 用户只看得到 20 条，其余的看不到也删不了 |

不在本 PR：检索进 prompt（`search`，本来就是「取最相关的 N 条」，不是读全量）；`delete_all`（mem0 自己分批删到空，`memory/main.py:1918–1935`，不受 20 条影响）；旧记忆归回存档（下一个 PR）。

## 2. 已查实的事实（main `224951a`，`mem0ai==2.0.20`，`requirements.txt:170`）

| # | 事实 | 坐标 |
|---|---|---|
| F1 | 4 个读的地方最后都走同一个方法 `MemoryView.get_all`。前三个经 `ScopedMemory.get_all`，面板经 `MemoryManager.get_all` | `core/memory_scope.py:86`、`core/memory_manager.py:266–268`、`:472–483` |
| F2 | 这个方法调 mem0 时没传条数：`results = self._mem.get_all(filters=scope.filters())` | `core/memory_manager.py:477` |
| F3 | mem0 的 `get_all` 默认 `top_k: int = 20`，没有「全部」这个选项；内部多取一些再截到 `top_k` 条 | `mem0/memory/main.py:1259`、`:1310`（`fetch_limit = limit if show_expired else max(limit * 4, 60)`）、`:1327` |
| F4 | 返回的不是最新 20 条：本地 qdrant 按 id 顺序翻页，id 是随机的，所以是随机的 20 条 | `mem0/vector_stores/qdrant.py:585–591`（`client.scroll(..., limit=top_k)`） |
| F5 | 这个方法出错时吞掉异常、返回空列表、只打 WARNING | `core/memory_manager.py:481–483` |
| F6 | `top_k ≥ 50` 时 mem0 会走一个「规模提示」分支，但遥测关着时第一行就返回；线上镜像关了遥测 | `mem0/memory/notices.py:123`、`:560`；`Dockerfile:30` |
| F7 | 仓库没有用 mem0 的过期功能（`grep -rni expiration core web` 无输出），所以「拿回来的比要的少」就等于「读完了」 | — |
| F8 | 有一个测试替身的 `get_all` 不收 `top_k`：`def get_all(self, filters=None)` | `tests/test_user_own_keys.py:207` |

实跑（沙箱，真 mem0 + 本地 qdrant，未改代码）：一个存档写入 25 / 66 / 205 / 460 条，`view.get_all(scope)` 都只返回 20 条。

## 3. 方案

| 方案 | 能否达成目标 | 取舍 |
|---|---|---|
| A. 传一个很大的固定 `top_k`（比如 10000） | 不能：超过这个数又悄悄截断，只是把 20 换成了更大的数 | 最省事，但问题还在 |
| **B. 按批要，拿满了就翻倍再要，直到拿回来的比要的少** | 能，没有上限 | 只用 mem0 的公开接口；超过第一批时会重读一遍 |
| C. 绕过 mem0，直接用 qdrant 客户端先数条数或自己翻页 | 能 | 要用 mem0 的私有方法拼过滤条件，还得自己重做一遍结果格式化，和 mem0 内部绑死 |

定 B（对照返工经验第 1、6 条：先保证达成目标，再在能达成的里面选最简单、耦合最少的）。

## 4. 改什么

只有一处实现：`core/memory_manager.py` 的 `MemoryView.get_all`。

- 新增一个模块常量（和 `REFLECTION_*` 放一起）：第一批要的条数，200。
- 方法体里：从第一批开始调 `self._mem.get_all(filters=scope.filters(), top_k=…)`；拿回来的条数小于要的条数就返回；否则把要的条数翻倍再调。出错的处理（F5）保持不变。

4 个读的地方、`ScopedMemory`、`MemoryManager.get_all`、面板接口都不用动（F1）。以后再加一个「读全部」的地方，也是 0 处要改。

连带改一个测试替身（F8）：`tests/test_user_own_keys.py:207` 加上 `top_k` 参数。**不改的话 `test_T7b` 会空跑着绿**：替身抛 `TypeError`，被 F5 吞掉变成空列表，而断言正好是 `== []`（沙箱实测，日志里有 `Mem0 get_all failed: _FakeMemory.get_all() got an unexpected keyword argument 'top_k'`）。

不改 F5 的吞异常（另一件事，记为面外）。

## 5. 目标检查

文件 `tests/test_memory_read_all_goal.py`。随 spec 附了一份我在沙箱跑通的原型（`test_memory_read_all_goal.py`），可以直接用，也可以改；改了要在报告里说明改了什么、为什么。

做法：真 mem0 + 本地 qdrant，embedding 用确定性替身，不调任何模型。同一张卡的存档 A 里写入「普通记忆若干 + 3 条高重要度记忆 + 1 个到期没问过的事件 + 1 个到期且已问过的事件和它的已问标记」，存档 B 里另写 30 条。A 的总条数取这 10 个值：

`6、20、21、60、61、200、201、400、401、460`（20 是旧上限；60 是 mem0 内部多取的下限；200、400 是第一批和翻倍后的边界）

每个总条数下都要成立（调用点 × 检查，没有空格）：

| 读的地方 | 检查 |
|---|---|
| 视图 `MemoryView.get_all` | 返回条数 = 库里这个存档的真实条数；里面没有存档 B 的内容 |
| 面板列表（`MemoryManager.get_all`，面板接口调的就是它） | 返回条数 = 真实条数 |
| 到期事件 | 返回的是没问过的那个；已问过的那个不再返回 |
| 反思 | 触发了，而且 3 条高重要度记忆都在送去反思的素材里 |
| 重逢问候 | 取到的两条都是高重要度记忆 |

沙箱结果：未改代码 **8 failed, 2 passed**（总条数 6 和 20 本来就该绿）；按第 4 节改完 **10 passed**。

## 6. 规模

线上现在：新加坡节点 1 张卡共 21 条；深圳节点没有记忆库。

沙箱实测读全量一次的耗时（真 mem0 + 本地 qdrant，磁盘库）：

| 一个存档的条数 | 耗时（中位数） |
|---|---|
| 100 | 2 ms |
| 200 | 12 ms |
| 1000 | 111 ms |
| 3000 | 341 ms |
| 30（库里另有 3000 条别的存档的） | 41 ms |

每轮对话读全量 1 次（到期事件，`core/chat_engine.py:1091`）；反思和重逢是偶尔。一轮对话的模型调用是秒级，所以一个存档到一千条以内没有可感知的影响。到几千条时，到期事件应改成按条件查而不是读全量 —— 那是另一件事，不在本 PR。

## 7. 提交顺序

1. spec + 目标检查（这个提交上跑出 8 failed, 2 passed）。
2. `MemoryView.get_all` 的改动 + 第 4 节那个测试替身（跑出 10 passed）。

## 8. 测试

- 本地跑全量 Python 测试（这次改了一个调用的参数，会碰到测试替身，按 0a 的教训跑全量）加 `npm test`，库用 Docker 起的 PG；合并门是分支 CI。
- 不跑变异（复核方做，并且自己补）；不调付费模型。
- 沙箱里按第 4 节改完后，全量与 main 的失败名单无差别。

## 9. 上线

和 0a 一样：「旧记忆归回存档」那个 PR 合并前不要部署。

## 10. 报告要写的

1. 第 2 节 F1–F8 逐条复核，有一条不成立就停下报告。
2. 第 1 个提交和最终的目标检查结果，各贴最后一行真实输出。
3. 全量测试最后一行，以及和 main 的失败名单的差（应为空）。
4. 这条的真实输出（应该只有 `core/memory_manager.py` 里那一处）：
   ```
   git grep -n -E '_mem\.get_all\(' -- core web scripts mcp_server
   ```
5. 和 spec 不一致的地方；面外发现只列不修。
