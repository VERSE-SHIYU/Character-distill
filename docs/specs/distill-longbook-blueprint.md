# 蓝图：红楼梦级长书的识别与蒸馏（Character-distill 蒸馏线）

> 基线：main `e621720`（2026-09-25 复核；与前一版基线 `c695e7b` 相比，本文引用的文件里只有 `storage/postgres_store.py` 挪了行）。坐标都在此提交上现读；执行任一工作包前的 S0 逐条复核，不成立即停下报告。
> 本文件是这条线唯一的设计文档，取代 `distill-stream-timeout.md` 与其 addendum（二者从未入库）。
> 用法：本文是整体设计，Shiyu 按第 5 节拆成若干份执行 spec；每份执行 spec 只摘所需工作包，并带上第 8–11 节中对应的行。
> 执行方 skill：`@search-first`（复用现有机制，先 grep）、`@tdd`（先红后绿）、`@verification-before-completion`（真实验收）。commit 用英文。

## 0. 目标

《红楼梦》级长书（实测全文 866,149 字符，按生产配置切 242 片）：
- 识别主要人物：不超时、主要人物不丢（含只集中出场几回的刘姥姥、妙玉）；
- 蒸馏任一主要人物（最坏情况：宝玉，出现在 207/242 片）：不失败、卡片质量不降，**目标约 3 分钟**。

方案已由 Shiyu 拍板。设计依据：LangExtract（Google，分片并行抽取 + 按出现次数汇总）、CoSER（ICML 2025，模型只做别名→标准名映射）、LLM×MapReduce（ACL 2025，折叠后归约）、EMNLP 2024 CROSS（分片方法中分层合并最优）、DeepSeek 官方并发上限（v4-pro 每账号 500）。

**为什么现在做**：生产 `/api/distill/identify` 对红楼梦报 500（第 1 节第 6 条）；演示账号要预置红楼梦角色卡，这条线不通就放不上去。

## 1. 已查实的约束（基线 `e621720`）

**生产配置**
1. 生产容器无 `config.yaml`（`git ls-files` 零命中；镜像 `COPY . .`；`docker-compose.prod.yml` 只挂 `./data`），回退读 `config.example.yaml`（`core/distiller.py:350-352`）：`chunk_size: 5000`、`map_concurrency: 30`、模型 `deepseek-v4-pro`、思考关闭。
2. 并发取自 `self._map_concurrency`（`core/distiller.py:370`），Map 原语用 `asyncio.Semaphore(self._map_concurrency)`（`:1454`，滑动窗口，非分轮）。
3. 并发 250 的本地前提：Map 用的 `AsyncOpenAI`（`adapters/llm_adapter.py:626-627` 的 `self._async_client`，经 `async_chat` `:745` 取用，`openai==3.13.0`）默认连接池 `max_connections=1000`，够用；分批合并 `_run_reduce_concurrent` 自带 `Semaphore(sem_size=6)`（`core/distiller.py:1499`），≥ 宝玉的 3 批。DeepSeek 官方并发上限：`deepseek-v4-pro` 每账号 500，超出返回 429（`adapters/llm_adapter.py` 已有 429 独立重试预算）。
4. 模型实测速度：`docs/evidence/thinking-maplen-after.json`（同模型、生产提示词）单次 1.4–31.3 s，输出 556–2,097 token，约 55–78 t/s（含首字约 1.5 s）。

**识别（`POST /api/distill/identify`，同步 HTTP，前端 `useAppStore.js:820`，前端超时 600 s、nginx 600 s）**
5. 多分片识别：`_identify_over_chunks`（`:1056`）→ Map 用 `_run_map_with_client`（非流式 `async_chat`）→ 各片名单 JSON 拼接 → `_identify_merge`（`:1109`）让模型**读全部分片名单、写出整份全书名单**（`IDENTIFY_MERGE_PROMPT`，`max_tokens=IDENTIFY_MERGE_MAX_TOKENS=65536`，`:322`）。
6. 合并的输出量随人物数线性增长（红楼梦数百人 × 每条约 34 token），按实测速度需数分钟，且长输入 prefill 静默撞 7 s 读超时 —— 生产 500 的现场即在此（第 27 条）。
7. 识别结果形状 `{name, aliases, importance, reason}`；前端 `CharCard.jsx:467-474` 直接显示 `importance` 与 `reason`。结果经 `resolve_characters`（`core/character_roster.py:96`）按 `IDENTIFY_VERSION`（`:327`，现为 2）落库缓存；memo 键已含版本（缺陷 89）。
8. 逐片识别提示词 `IDENTIFY_SYSTEM_PROMPT` 已要求每片给出 `importance`（该片内主要/次要）。
9. **别名是下游的选片依据**：`aliases_for`（`core/character_roster.py:116`）的 docstring 写明「下游按子串用别名（蒸馏选片、RAG 打标签），所以别名只该来自唯一指向此人的称呼」。调用点：蒸馏选片 `web/routers/distill.py:378`、`:1060`（进 `match_terms`，`core/distiller.py:1838` 按 `t in c` 子串筛片）；RAG 打标签 `core/text_manager.py:613`；非流式蒸馏 `core/text_manager.py:454`。**名单里混进一个泛称别名（如「二爷」），宝玉的蒸馏就会把贾琏的片段也选进来。**
10. 逐片条目只经 `_normalize_identify_items`（`:996`）补 `aliases`、丢非对象；**`importance` 取值未做规范化**（模型可能给出「主要」「次要」以外的写法）。
11. 识别合并的用量记账：`_identify_merge` 经 `_chat_accounted(..., "distill_identify")` 记账（唯一出口，缺陷 16）；`tests/test_usage_identity_context.py` 等按行数/动作断言用量。
12. 实测（Shiyu 上传的《红楼梦》，用人名表估算）：按「出现分片数」排序，刘姥姥第 70、妙玉第 53、晴雯第 33 —— **按次数截断会丢掉公认主要人物**。字符串匹配会把「夫人」「婆子」误当人名 —— **计数必须基于模型逐片识别出的名字，不能用代码匹配字符串**。

**蒸馏（前端走 `POST /api/distill/start` 后台任务 → `distill_incremental_stream`，`web/routers/distill.py:401`；不受 10 分钟 HTTP 上限约束）**
13. 流程：按名字+别名筛相关片（`:1838`）→ 逐片 Map（非流式 `async_chat`，单次 45 s / 总 60 s 墙钟）→ 相关片 > `SAFE_SINGLE_REDUCE=80`（`:313`）时分批合并 → 总合并（≤80 批结果时走 `_single_reduce_stream`，流式）→ 格式化（`:2057`，**已是流式** `chat_stream`，`max_tokens=CARD_MAX_TOKENS=8192`）。
14. **分批合并是非流式**：`_reduce_batches`（`:1982`）→ `_run_reduce_concurrent`（`:1494`）→ `_single_reduce_async`（`:1546`）→ `self._llm.async_chat(...)`，未传 `max_tokens`（取 `llm.max_tokens=4096`），受 `_GEN_ATTEMPT_S=45`、`_GEN_DEADLINE_S=60` 墙钟约束。每批输入约 80 份 Map 结果（约 10 万 token），输出上限 4096 token，按实测速度需 60–130 s ⇒ **必超时**。
15. 宝玉 207 片、王夫人 171、凤姐 161、贾母 157、宝钗 149、黛玉 143、袭人 131（实测估算）都 > 80 ⇒ **红楼梦主要人物蒸馏都会走到第 14 条并失败**。
16. 逐片 Map 实测最慢 31.3 s < 45 s 墙钟，**不改**。
17. 格式化阶段的消费方式：后台任务（`web/routers/distill.py:399-470`）与 SSE（`:1064` 起）都是把生成器吐出的 `str` 片段**累加成一个字符串再 `json.loads`**；前端走后台任务、只轮询状态，不消费逐 token 流。⇒ 格式化改为「并行生成后一次性吐出完整 JSON 字符串」与两条消费方式都兼容。
18. 卡片字段（`core/schema.py:70-88`）可分为互不依赖的组；格式化提示词为 `DISTILL_PROMPT_BEFORE_NAME + 名字 + DISTILL_PROMPT_AFTER_NAME + schema`（`core/distiller.py:86-88`，流式路径 `:2054-2063`）。
19. 非流式的 `distill_incremental`（`:1597`，含非流式压缩 `:1728` 与格式化 `:1755`）只被 `/run` 与 legacy `/api/distill` 经 `TextManager.get_or_distill`（`core/text_manager.py:458`）调用；前端不走这条。**本段不改，只在报告中记录**。

**2026-09-25 复核补充**
20. **流式用量存在共享属性上**：`LLMAdapter.chat_stream` 写 `self.last_usage`（`adapters/llm_adapter.py:786` 清空、`:816` 写入、`:840-841` 估算兜底）；`_collect_stream`（`core/distiller.py:660`）经 `core/utils.py:82-83` 读它记账；span 收尾 `_infer_finalize`（`adapters/llm_adapter.py:299`）也读它。已复现：几条流几乎同时结束、且 usage chunk 之后还有一次 `[DONE]` 读时 300/300 串号；结束时刻错开时 300/300 正确。
21. **`T.spanned` 的生成器包装能透传返回值**：`result = yield from fn(...)`（`core/telemetry.py:272`）+ `return result`（`:288`）。
22. **分批合并单批失败被静默丢弃**：单批异常 → 结果置空（`core/distiller.py:1511-1513`）；流式路径空批 print 后跳过（`:2024`）；只有全空才报错（`:1528`）。
23. **续跑只认 interrupted**：`/start` 经 `find_interrupted_distill`（`storage/postgres_store.py:3380`，`WHERE ... status = 'interrupted'`）找可复用任务（`web/routers/distill.py:786-810`）。合并失败的任务状态是 `error`，再点蒸馏是新任务，Map 全部重跑。`AGENTS.md` 已登记：非 interrupted 行命不中是已知取舍，「按 (user, text, character) 找最近一条可复用行」属加功能，另立项。
24. **分片缓存键不含模型与 Map 提示词版本**：分片级门只比 `text_fingerprint(chunk)`（`core/distiller.py:254`；写入在 `:1913`）；任务级门只比 `chunk_size` 与全文指纹（`web/routers/distill.py:799-802`）。对照：识别的缓存键已含模型与版本（`core/distiller.py:966`，`{指纹}:{model}:{IDENTIFY_VERSION}`）。
25. 前端蒸馏默认 `force=false`（`web/frontend/src/store/useAppStore.js:831`）。
26. 本地 worktree 有 `config.yaml`，生产没有；`Distiller` 与 `web/deps.py:36-38` 都是「有 `config.yaml` 就读它」。本地跑出的数若不先移走它，就不是生产配置。

**生产故障证据（SG，执行方只读排查，2026-09-25）**
27. 500 完成于 2026-09-24 15:06:40.54Z，镜像 `c3ea722`，容器内无 `LLM_*` 覆盖。异常 `httpx2.ReadTimeout`（socket 超时直接上抛，无中间层）。栈：`distill.py:651` → `identify_characters`（`:984`）→ `_identify_over_chunks`（`:1107`）→ `_identify_merge`（`:1123`）→ `_collect_stream`（`:645`）→ `chat_stream` 的 `for chunk in stream`（生产镜像 `:805`，基线 `:814`）。**落在合并，Map 已全部完成**。
28. 那次请求：866,149 字、`text_type=classic`、120 段（均长 7,216 字，每段都超 `chunk_size`，走硬切）→ 识别 242 片（识别用 `self._chunk_size`=5000，不看 text_type）。Map 242 次调用：prompt 692,097 / completion 160,701 token（上游实测，均 664 token/片）；合并输入约 199,106 token（失败后按字符估算）。合并开始到报错约 7.9 s，与流式 7 s 读超时 + 建流对得上。
29. **classic 的蒸馏分片是 6000 字**：`effective_chunk_size`（`core/distiller.py:381-387`）对 classic 取 `max(chunk_size, 6000)`；识别不受影响。第 15 条的「宝玉 207 片」是按 5000 估的，classic 下以验收实测为准。
30. **蒸馏里直接调 `chat_stream` 的有 5 处**：`core/distiller.py:645`（`_collect_stream`）、`:1277`、`:1363`、`:1560`（`_single_reduce_stream`）、`:2057`（流式格式化）。聊天只有 `core/chat_engine.py:467` 一处，它需要 7 s 读超时保证首字快。
31. **LLM 错误的统一出口**：`llm_error_types()`（`adapters/llm_adapter.py:478-485`，现为 `(IncompleteResponseError, LLMCallRefused)`）注册到 `_llm_error_handler`；状态码按 `llm_error_payload` 的 `kind` 查 `_LLM_ERROR_STATUS`（`web/server.py:212-218`，未登记默认 502）。`UpstreamFailure`（`:401`）带 `user_message`，但**不在** `llm_error_types()` 里，`llm_error_payload` 也不认它；流读到一半的 `httpx` 错误不被包装，原样上抛成 500。

## 2. 红线

1. 不新增配置项：并发只改 `config.example.yaml` 里已有的 `map_concurrency` 的值。
2. 识别结果的对外形状 `{name, aliases, importance, reason}` 不变，前端零改动。
3. 逐片 Map 的提示词与调用方式（识别、蒸馏两处）不改。
4. 不新建模块：汇总逻辑放在 `core/distiller.py` 或 `core/character_roster.py` 现有文件内，按职责就近放。
5. 不做上传时预识别（识别改后约 1–3 分钟，在 10 分钟上限内）。
6. WP8 不新增表、不加列：只改续跑的查找条件与分片缓存键。

## 3. 总体设计（谁负责什么）

| 层 | 模块 | 本线之后的职责 |
|---|---|---|
| 适配器 | `adapters/llm_adapter.py` | 长输出读超时（WP1）；上游失败归类（WP2）；`chat_stream` 以返回值交出本次用量（WP4） |
| 纯计算 | `core/character_roster.py` | 名单汇总、并组、判主次、取理由（WP3，纯函数，不碰 LLM） |
| 数据定义 | `core/schema.py` | 角色卡 4 组字段划分，定义一处（WP7） |
| 编排 | `core/distiller.py` | 识别：逐片 → 汇总 → 别名判断 → 出名单；蒸馏：逐片 → 分批合并（任一批失败即整体失败）→ 4 组并行格式化；长输出一律走 `_chat_accounted(stream=True)` → `_collect_stream`，这是唯一的长输出与记账出口；分片缓存键 `chunk_cache_key`（WP8） |
| 路由 | `web/routers/distill.py` | `/start` 续跑：可复用 `interrupted` 与 `error` 两种任务（WP8） |
| 存储 | `storage/*_store.py` | `find_resumable_distill`（由 `find_interrupted_distill` 改名并放宽状态条件，WP8） |

**落点细则（红线 4「按职责就近放」的具体化）**
- WP3：汇总、并组、判主次、取理由是**纯计算**，写成 `core/character_roster.py` 里的纯函数（输入逐片条目 + 别名判断给出的组对 + 全书分片数，输出名单）；`_identify_over_chunks` 只做编排：逐片调用 → 纯函数建组 → 别名判断调用 → 纯函数出名单。测试直接喂列表测纯函数，不需要 LLM 桩。
- WP7：4 组字段划分只在 `core/schema.py` 定义一处（紧挨 `CharacterCard`），格式化与测试都读它；合并后仍由 `CharacterCard.model_validate` 统一校验，不另写校验。

## 4. 工作包

### WP1 [adapter + distiller] 长输出流式读超时
**根因**：流式调用的 `timeout` 是标量 7 s（`_STREAM_ATTEMPT_S`），httpx 的标量超时同时设了读超时，即「两个数据块之间最多等 7 s」，贯穿整条流。DeepSeek 只在排队等调度时发 keep-alive，开始推理后读长输入（prefill）期间可能完全无数据，长输入一 prefill 就超 7 s（第 1 节第 27、28 条）。已在沙箱用真 adapter 复现：静默 9 s 在第 7.0 s 报 `httpx2.ReadTimeout`；改分项超时后 9.0 s 成功。
1. **入口放在适配器**（裁决 2026-09-25，路 D）：适配器公开两个流式方法，共用一个私有实现。
   - 私有 `_stream(system_prompt, messages, max_tokens, *, read_s)`：现 `chat_stream` 的全部逻辑，唯一差别是 `create()` 的 `timeout` 传 `openai.Timeout(<本次 attempt 的 ceiling>, read=read_s)`：connect / write / pool 不变，只调读。
   - `chat_stream(...)`：交互用，`read_s` = 本次 attempt 的 ceiling，行为逐字不变（聊天仍是 7 s）。
   - `chat_stream_long(...)`：长输出批量用，`read_s = _BATCH_STREAM_READ_S`。
   - 两个公开方法各自保留 `@T.spanned`，都以 `return (yield from self._stream(...))` 透传返回值（WP4 的用量随返回值交出靠这一点）。不设公开的 `long_output` 布尔参数。
2. 新增一个具名常量 `_BATCH_STREAM_READ_S = 300.0`，放在 `_STREAM_ATTEMPT_S` 旁。依据写进注释：DeepSeek 对未开始推理的请求 10 分钟关连接、nginx 读超时 600 s，300 s 在两者之内且远大于合理 prefill。不做 env 出口。
3. 第 1 节第 30 条的 5 处改调 `self._llm.chat_stream_long(...)`。调用形态仍是 `self._llm.*`，记账形态锁按「传递闭包到 `create`」自动推出入口方法集（`tests/test_usage_accounting_lock.py` 文件头第 1 条事实），**锁不用改**。
   - 为什么不放在 `Distiller`：蒸馏器里包一层私有方法后，调用点不再是 `self._llm.*`，形态锁认不出；要么给锁开特例（锁的文件头明令禁止豁免名单），要么调用点漏账。放在适配器，「这类调用读超时多长」这条策略也只在一处定义。
4. 架构锁：一条 AST 断言——`core/distiller.py` 中不出现对 LLM 接收者的 `.chat_stream(` 调用（交互版只给聊天用）。不钉调用点数量（WP5、WP7 会合理地改变它）。
5. 测试桩：凡给 `Distiller` 用的假 LLM（`_make_llm` 的 `MagicMock` 等）改为桩 `chat_stream_long`；本节之后 WP3–WP7 表中写的「桩 `chat_stream`」一律指 `chat_stream_long`。
- 测试：第 8 节 S1、S2。

### WP2 [adapter] 流中断归类为上游失败，统一回 503
1. **传输层失败只定义一次**（裁决 2026-09-25，含 sentinel 发现）：
   - 模块级 `_TRANSPORT_ERRORS = (httpx2.TransportError, openai.APIConnectionError)`。依据：openai 3.13.0 读流途中的错误不经包装，原样抛 `httpx2` 异常（`ReadTimeout`、`ReadError`、`RemoteProtocolError` 都是 `TransportError` 子类）；openai 3.19.2 的 `_streaming.py` 会把它包成 `APITimeoutError` / `APIConnectionError`（前者是后者子类，已下载核对）。两者都是库公开的基类，不手写清单；锁定版本与新版本都覆盖。
   - `chat_stream` 的读流循环在 `except IncompleteResponseError` 之后加 `except _TRANSPORT_ERRORS as exc` → `raise UpstreamFailure(..., user_message=_upstream_user_message(exc)) from exc`。
   - `_upstream_user_message`（`:423`）是「上游异常 → 上屏文案」的唯一出口：在按状态码查表之前先判 `isinstance(exc, _TRANSPORT_ERRORS)` → 「模型服务暂时不可用，请稍后重试」。这样建流阶段重试耗尽的网络错误（今天落空串 → 通用文案）与读流中断得到同一句话，不在两处各写一份。
   - 不用「除截断外一切 Exception」：会把我们自己的 bug（如 chunk 形状变了的 `AttributeError`）报成 503「上游不可用」。
   - 流中断**不重放**（已吐出的片段不可撤回）。
2. 出口登记，三处都是在已有表里加一项，装配层零改动：`llm_error_types()` 元组加 `UpstreamFailure`；`llm_error_payload` 认它，`kind = "upstream"`；`_LLM_ERROR_STATUS` 加 `"upstream": 503`。
3. 影响面（已确认，属本次改动）：`UpstreamFailure` 今天还由 `_RetryBudget.on_failure`（`:203/:207`）在各类调用重试耗尽时抛出，登记后这些路径也从 500 变为 503 + `_upstream_user_message`（`:414-423`）的现成文案；聊天流中断同样走 503。报告里写明。
4. 边界锁补洞：`tests/test_chat_stream_error.py::test_no_exception_class_leaks_into_core_web_storage` 现只扫字面量 `IncompleteResponseError`，改为扫 `llm_error_types()` 返回的全部类名（从元组取，不写字面量），以后加一类自动覆盖。
5. `web/routers/auth.py:718` 的嵌入测试（Collision B 裁决）：改为 `except Exception as exc`，经 `llm_error_payload(exc)` 取 `kind`，`== "call_refused"` 就 `raise`，否则走原嵌入失败文案；删掉 `:23` 的异常类导入。行为等价。
6. 边界锁改为 AST 扫描：只看 core/web/storage 中 `import` / `Name` / `Attribute` 是否引用 `llm_error_types()` 的类名，不扫注释与 docstring（文档里提到类名不是耦合）。
7. `requirements.in` 声明 `httpx2>=2.13.0`（适配器直接 import 它）；按文件头命令带 `--constraint requirements.txt` 重锁，任何 pin 不变。单独 commit `build(deps)`。
- 测试：第 8 节 S3。

### WP3 [distiller + roster] 识别合并改为「代码汇总 + 模型只判别名」
替换 `_identify_merge` 的实现（多分片路径；单分片路径不动）：

1. **汇总（纯代码）**：以每片解析出的条目为输入。名字与别名做规范化（去空白）后建组：
   - 同一 `name` 必归一组；
   - 某别名只在**一个**主名下出现过时，按别名并组；同一别名挂在 ≥2 个不同主名下（如「二爷」）视为**有歧义**，不据此并组，交第 2 步。
   - **有歧义的别名从所有组的 `aliases` 中移除**（不只是不并组）：别名在下游按子串选片、打 RAG 标签（第 1 节第 9 条），留一个泛称就会污染蒸馏。别名判断确认两组是同一人后，合并组的别名同样只保留唯一指向此人的称呼。
   - 逐片 `importance` 先规范化：含「主」计为主要，其余一律按次要计。
2. **别名判断（一次模型调用）**：只把「组的主名 + 别名 + 出现分片数」列表（不含理由、不含正文）交给模型，问「哪些组其实是同一个人」，只输出需要合并的组对。走 `_chat_accounted(stream=True)` 长输出路径（WP1 已放宽读超时）。模型未明确判定为同一人的，**一律保持分开**。
3. **主次（纯代码）**：每组统计 `chunk_count`（出现的**不同**分片数，同片重复只计一次）与 `main_count`（被逐片识别判为「主要」的分片数）。
   - `importance = "主要"`，当 `main_count ≥ 2` **或** `chunk_count ≥ 全书分片数的 10%`；否则「次要」。
   - 排序：先主要后次要，组内按 `(main_count, chunk_count)` 降序。
   - **不截断**：逐片识别出的人物全部保留。
4. **理由（纯代码）**：取该组在「主要」分片中的第一条 `reason`；没有则取第一条非空 `reason`。不调用模型重写。
5. 别名判断调用经 `_chat_accounted` 记账，动作沿用 `distill_identify`（唯一出口，缺陷 16）；受影响的用量行数断言按新的调用次数更新，并在报告里逐条说明。
6. `IDENTIFY_VERSION` 从 2 升到 3（新口径的名单不能复用旧缓存）；更新其注释。
7. 删除不再使用的 `IDENTIFY_MERGE_PROMPT` / `IDENTIFY_MERGE_MAX_TOKENS` 及其专属测试；新的别名判断提示词只描述第 2 点这一件事。
- 落点：汇总、并组、判主次、取理由写成 `core/character_roster.py` 的纯函数（见第 3 节）。
- 测试：第 8 节 I1–I5。

### WP4 [adapter + distiller] 流式用量随调用返回（独立 commit，排在 WP5 之前）
- 事实：`LLMAdapter.chat_stream` 把用量写在**实例属性** `self.last_usage`：`adapters/llm_adapter.py:786` 开头清空、`:816` 写入、`:840-841` 估算兜底。`_collect_stream`（`core/distiller.py:660`）流尽后经 `core/utils.py:82-83` 读这个属性记账；span 收尾 `_infer_finalize`（`adapters/llm_adapter.py:299`）也读它。WP5（3 批并行）、WP7（4 组并行）让同一个 adapter 实例同时跑多条流 → 互相覆盖 → 账与 span 都记成别人的，或被误判「无 usage」改按字符估算。
- 依据：OpenAI 协议里 usage 是**该次响应**最后一个 chunk 上的数据（Chat Completions streaming events 文档），本就属于单次调用；本仓 `async_chat` 已返回 `(text, usage)` 元组，Map 250 并发不串号靠的正是这一点；LangChain 同样把 usage 挂在该次调用的消息上，并要求并发请求不共用一个累加器。
- 复现（2026-09-25，真 `LLMAdapter.chat_stream` + 真 `_collect_stream`，假 client）：3 条流结束时刻错开时 300/300 正确；结束时刻重合、且 usage chunk 之后有一次读 `[DONE]` 的 socket 读（真实 SSE 就是这样，会释放 GIL）时 300/300 串号，三行全记成最后一条的 303。结论：竞态真实存在，但只在几条流几乎同时结束时触发，生产中属低概率的记账错误，不影响超时和卡片质量。
- 修法（已定）：`chat_stream` 以 `return usage` 交出本次用量（PEP 380：生成器 `return v` 即 `StopIteration(v)`）。`T.spanned` 的生成器包装已是 `result = yield from fn(...)` + `return result`（`core/telemetry.py:272`、`:288`），返回值能穿过；`_infer_finalize` 对 `chat_stream` 改用这个 `result`。`_collect_stream` 用 `next()` 驱动并取 `StopIteration.value` 记账，不再读共享属性。`last_usage` 照写，其余单线程调用方（`chat_engine`、`agent_loop`）不动。现有测试桩按新契约补 `return usage`。
- 否掉的：`threading.local` 存 usage（仍是「读一个隐式共享状态」，只是换了作用域）；每条并行调用各配一个 adapter 实例（改装配面大，治标）。
- **范围补充（WP4 S0 后裁决 + 补漏，2026-09-25）**：蒸馏里共有 **5 处**流式记账点，改后一律取「本次调用返回的那一份」，不再回头读共享属性 `last_usage`。本分支实测坐标（`feat/distill-reduce`）：① `_collect_stream`（`core/distiller.py:633`；`next()` 环，`usage = stop.value` `:671`，记账 `:683`）；② `distill_stream`（`def` `:1281`；`usage = yield from` `:1300`，记账 `:1305`）；③ `_distill_longcontext_stream`（`def` `:1370`；`usage = stop.value` `:1397`，记账 `:1406`）；④ `_single_reduce_stream`（`def` `:1592`；`usage = yield from` `:1595`，记账 `:1599`）；⑤ 流式格式化（`distill_incremental_stream` Phase 3，`def` `:1814`；`usage = yield from` `:2097`，记账 `:2104`）。第 ②④⑤ 处是 `usage = yield from self._llm.chat_stream_long(...)`、第 ①③ 处是滤思考态用的显式 `next()` 环，两者都把 `usage` 显式交给 `_try_record_usage`；改完后蒸馏里的流式记账只剩「随返回值」这一种口径。非流式的 `chat()` 调用点不在本项。
- 测试：第 8 节 R4、F1（记账部分）。

### WP5 [distiller] 分批合并走流式 + 任一批失败即整体失败
1. `_single_reduce_async` 不再用非流式 `async_chat`：改为复用 `_chat_accounted(stream=True)`（WP1 的长输出读超时随之生效），在 `_run_reduce_concurrent` 中以线程执行（沿用本仓已有的上下文传播方式，S0 报出采用的具体机制并说明为何能带上 `LLM_CALLER`）。
2. 分批合并的输出上限显式设为 `CARD_MAX_TOKENS`（8192；现状取 `llm.max_tokens=4096`，且非流式截断会抛 `IncompleteResponseError`，`adapters/llm_adapter.py:532`）。改走流式后若仍返回截断标志：**按失败处理并抛出**（沿用现有截断口径），不把半截档案交给格式化。真实验收报出每批的自然输出 token 数；若有批次撞 8192，停下报告，由 Shiyu 定上限，不自行调参。
3. 批次大小 `SAFE_SINGLE_REDUCE=80` 不改（即 LLM×MapReduce 的折叠阶段）。
4. `distill_incremental`（非流式，`/run` 用）调用的同一函数随之受益，但其自身的非流式压缩与格式化不在本段范围，只记录。
- 事实：`_run_reduce_concurrent` 单批异常 → 结果置空（`core/distiller.py:1511-1513`）；流式路径空批只 print 跳过（`:2024`）；只有**全空**才报错（`:1528`）。WP5 第 2 条「截断即失败」抛到这里会被吞掉 —— 宝玉 3 批丢 1 批，卡片少三分之一材料且无人知道。
- 依据：Hadoop MapReduce 的缺省语义是任务失败达上限即**整个作业失败**，输出只在全部任务成功后才提交（`mapreduce.reduce.maxattempts`，mapred-default.xml），不交付缺块的结果。
- 修法：在唯一汇合点 `_run_reduce_concurrent`，**任一批失败即整体失败**。「失败」= 抛异常**或**返回空正文。沿用现有 `DistillError` 上屏口径；全空守卫被这条覆盖，其测试保留。非流式 `_do_reduce` 共用此函数，随之变化，报告记录。不加批级重试（adapter 建流前已重试，再加一层是嵌套重试相乘；`UpstreamFailure` 分不清「建流耗尽」与「流中断」）。
- **线程机制（WP5 S0 后裁决，2026-09-25）**：每批的 `_chat_accounted(stream=True)` 用 `await asyncio.to_thread(...)` 放进线程，`asyncio.gather` + `Semaphore(6)` 结构不变。依据：仓内同形先例（`web/routers/chat.py:517/528`、`web/routers/distill.py:1070` 用 `to_thread` 逐片推进流式生成器；`core/character_roster.py:110`）；`to_thread` 内部 `copy_context()`，`LLM_CALLER` 随之进线程；流式 span 走 `_start_noncurrent`（不 attach），不需要载体配对，所以不另造 `ctx_submit` + `wrap_future` 的组合。改完若 `_run_reduce_concurrent` 的 `client` 参数不再被用到，连同调用点的实参一起删掉，不留死参数。
- **定案：失败即整体失败，同时做 WP8**。前者保证不交半张卡；WP8 让失败后的重试只重跑合并与格式化，两者合起来才同时满足「质量不降」与「失败代价小」。
- 测试：第 8 节 R1–R3。

### WP6 [config] 并发
`config.example.yaml` 的 `map_concurrency` 从 30 改为 250（官方上限 500/账号；242 片、宝玉 207 片基本一次发完，逐片阶段耗时≈最慢一片）。真实验收时报告是否出现 429；出现则停下报告，由 Shiyu 定是否下调，不自行调参。
- 已核（DeepSeek 官方 Rate Limit 页）：500 是**账号级**，不论用哪个 key；一个请求从发出到响应结束都占一个并发，长流式输出占得更久。用户自带 key 时互不影响；**同一账号**同时跑两个蒸馏就会到 500 —— 演示账号若共用你的 key 并允许访客蒸馏，需另议（不在本线范围）。

### WP7 [distiller + schema] 流式蒸馏的格式化改为按字段组并行生成
只改 `distill_incremental_stream`（前端走的路径）；非流式 `distill_incremental` 不动。

1. 字段按 `core/schema.py` 分为 4 组（S0 给出分组并说明依据，要求组间无相互引用）：身份与背景；性格/价值观/内在矛盾/情感与决策；说话风格/口癖/原文对话/开场白与苏醒台词；关系/关键经历/角色弧线/标签与心理认知画像。
2. 4 组**并行**调用，均走 `_chat_accounted(stream=True)` 长输出路径并记账（动作沿用 `distill_format`）。每组提示词 = 现有格式化提示词中该组相关的规则 + 该组子 schema。（不要求、也不依赖 DeepSeek 前缀缓存：同时发出的请求不保证命中缓存；本步提速来自并行，不来自缓存。）
3. **分批时跳过总合并**：相关片 > 80 时，4 组直接读取各批合并结果（≤3 份，每份 ≤ `CARD_MAX_TOKENS`），不再先做一次总合并——总合并与格式化是串行的两次长输出，跳过后省掉一整段。相关片 ≤ 80 时照旧先单次合并，再并行格式化。
4. 代码合并 4 组结果 → `CharacterCard.model_validate` 校验 → 以**一个** `json.dumps` 字符串 `yield`（与第 1 节第 17 条的两种消费方式兼容）。任一组失败或校验不过：按现有重修/报错口径处理，不拼半张卡。
5. 格式化进度：并行开始时 `yield {"status": "formatting"}`，每组完成可发一次心跳；不新增状态值。
- **分组定案（WP7 S0 后裁决，2026-09-25）**：G1 身份与背景 = name、identity、background；G2 内在 = personality_traits、values、inner_tensions、emotional_patterns、decision_style；G3 语言 = speaking_style、dialogue_examples、first_message、cognitive；G4 关系与经历 = relationships、key_memories、character_arc、psyche。`cognitive`（认知/语言画像，含 speech_style、vocabulary_level）归 G3，与 speaking_style 同组，消除两处语义重叠。`tags`、`awakening_message` 本来就由后置步骤生成（`_auto_tag`、`_generate_awakening`），不进 4 组。分组与后置字段都在 `core/schema.py` 定义一处（紧挨 `CharacterCard`），F3 断言「4 组 ∪ 后置字段 == `model_fields`，两两无交集」。
- **提示词装配**：现有格式化提示词拆成「共享前缀 + 4 个组片段」，只定义一次。共享前缀 = 分析铁律、输出要求、「所有字段按模板输出、不加自定义字段」，以及把「list 元素是一句字符串」改写成不点字段名的通用句。组片段 = 该组的维度说明、该组专属的「重要」规则、该组的 JSON 模板片段；「前置步骤（枚举全部有名字角色）」归 G4。非流式 `distill_incremental` 仍用完整提示词，由同一批片段按原顺序拼出，拼出的文本除两句外与改前逐字一致：「list 元素」那句改为通用句；模板引导句「完整 JSON 模板（所有字段必须包含，psyche 为必需嵌套对象）：」改为「JSON 模板（所有字段必须包含）：」（psyche 的要求已在 G4 的「重要」规则里，引导句进每组提示词时不能再点 psyche）。本次核对一次、报 diff，不加钉子测试。子 schema 由 `CharacterCard` 按组字段取，不手写。
- 测试：第 8 节 F1–F5。

### WP8 [router + storage + distiller] 失败任务也可续跑
**目标**：蒸馏失败后再点一次（不勾「重新蒸馏」），已完成的 Map 片零调用复用，只重跑合并与格式化。WP6 把并发抬到 250，若撞 429 导致 Map 超失败率，重试同样只补失败片。

**改动**
1. **查找**：`find_interrupted_distill` 改名 `find_resumable_distill`（`storage/base.py:806`、两个 store、`web/routers/distill.py` 唯一调用点），条件改为 `status IN ('interrupted', 'error')`，仍取 `updated_at` 最新一条。`done` 不复用：已出卡的再次蒸馏是「要新版本」，沿用现状整跑。`force=True` 照旧跳过复用。
2. **缓存键 = 本次 Map 请求本身**：新增 `chunk_cache_key(system, user, model) = sha256(system + "\x00" + user) + ":" + model`，其中 `(system, user)` 就是 `_build_map_prompt(chunk)` 渲染出的那一对（`core/distiller.py:1867-1868`：`map_system(character_name), map_user(chunk, character_name)`）。写入（`:1913`）与校验（`_resume_hit`，`:254`）两处都改调它；`_build_map_prompt` 的定义提到命中循环之前。值存进现有的 `chunk_fingerprint` 列，不加列。
   - 提示词模板改动、角色名、chat/story 两套 Map 提示词、原文，都自动进了键，**不设需要人记得 +1 的版本常量**。模型单列，与识别缓存键同口径（第 1 节第 24 条）。
3. **复用路径本身不改**：`/start` 对复用行已是原子 UPDATE 盖章（置 running、刷新 `chunk_size` 与全文指纹，返回 0 行即拒绝启动），并整条重写内存 `_tasks` 条目（`web/routers/distill.py:823-853`）。interrupted 与 error 走的是同一段代码。
4. **上线影响**：旧断点的指纹只含原文，上线后一律命不中，只重跑一次。在报告里写明，不做兼容分支。
5. **文档**：`AGENTS.md` 里「非 interrupted 行命不中是已知取舍」一条改为已实现，并指向本项；`tests/perf/distill_resume_reachability.py` 的期望同步（error 命中，done 仍不命中）。

**为什么这样设计**
- 放宽的是「哪些任务可复用」这一处查找条件；分片级三重门（形状、非空、缓存键）不动。复用的安全性一直由分片门保证，与任务是怎么结束的无关。
- 缓存键必须覆盖所有影响输出的输入。原来只含原文，因为续跑窗口只有「崩溃到重启」几分钟；失败任务可能隔几天才重试，期间换了模型或改了 Map 提示词，就会复用错的结果。直接对「发出去的请求」取指纹，比手工维护版本号少一个会漏的地方。
- 已知取舍：若失败是由某片「非空但质量差」的 Map 结果引起，续跑会复用它；用户勾「重新蒸馏」（`force=True`）即绕开。
- 否掉的方案：`MAP_VERSION` 常量（改提示词时要记得 +1，漏一次就静默复用错结果）；给 `distill_tasks` 加列做任务级门（要迁移，而且模型是分片级事实）；按时间给断点设过期（拍一个数字，没有依据）。

**测试**：第 8 节 C1–C4。

### WP15 [scripts] 把验收产出的书与卡片搬进两个节点的演示账号（2026-09-26 拍板）
**目标**：把本地验收产出的红楼梦（原文 + 版本 3 的名单）与宝玉、刘姥姥两张卡，导入 SZ、SG 各自的演示账号 `offerPass`，零模型调用、不搬向量。

**已查实的约束**（main `4908a73`；执行方 S0 逐条复核，不成立即停）
1. 原文与名单在 `texts` 同一行：写入口 `save_text`（`storage/postgres_store.py:285`）、`save_characters`（`:379`，带版本）；读 `get_characters_owned`（`:395`）按版本精确比对，不等即当无缓存、会重新识别。
2. 卡片写入口 `save_card`（`:599`），按 (text_id, name, user_id) 命中则原地更新；`cards.text_id` 外键指向 `texts`，标签 / 开场白 / 苏醒台词都在 `card_json` 里。
3. 向量的 key 只来自用户自己（`web/llm_resolution.py:95-123`，全局一档在运行代码里不可达）；演示判定只有 `role == 'guest'`（`core/roles.py:48`），门禁只拦 HTTP 写（`web/demo_gate.py:78`），不拦脚本写库。
4. 两节点现无 guest 账号；SG 的 `offerPass` 名下有两本失败留下的副本：`cb455edd7ce6`（有名单，版本 0）、`b42f3d89ace4`（无名单）；两本都无卡片、无向量。私有卡不参与跨节点同步（`web/cross_border_sync.py` 只同步私信与公开卡）。
5. 先例：`scripts/migrate_data.py`（目标账号经环境变量 `TARGET_USERNAME` 传入，不写死 id）。

**拍板结果**：只搬库里的数据；向量由 Shiyu 导入后用演示账号各开一次会话、在各节点生成（每节点约 1 元）；SG 两本旧副本软删；只搬宝玉、刘姥姥两张卡。

**步骤**（每步独立 commit）
1. **[scripts] `scripts/demo_seed.py`，两个子命令**，都**经 storage 层现有写入口**落库，不写裸 SQL：
   - `export`（本地，只读）：给定 `--text-id` 与 `--cards 宝玉,刘姥姥`，导出原文与元数据、名单（含 `characters_version`）、两张卡的 `card_json`，打成一个 JSON 包，附条数与 sha256。
   - `import`（在目标节点的 app 容器内跑）：目标账号取自 `TARGET_USERNAME`。**默认只试运行**，报告将写入什么；加 `--apply` 才写。检查：账号存在；运行中代码的 `IDENTIFY_VERSION` 等于包里的版本；同一账号下已有同一原文（按 `text_fingerprint`）就复用那一行、不重复建；已有同名卡且内容一致则跳过、不一致则停下报告。`--soft-delete-text-ids` 显式给出要软删的旧副本 id，只软删**属于目标账号**的那几行。
2. **[tests]** 用 Docker 起的测试 PG 与一份小数据跑通：试运行零写入；`--apply` 后原文、版本号、两张卡都归到目标账号；再跑一次不重复；版本不等 / 账号不存在即停；软删只作用于目标账号名下的指定 id。

**执行顺序**（脚本现在就可以用假数据写好；真正导出、导入等两次验收通过、部署之后）：两次验收通过 → 合入 main → 部署 → Shiyu 在两节点开好 `offerPass`、填好 embedding key（region cn）、改成 guest → 本地 `export` → 两节点各 `import`（先试运行，再 `--apply`，SG 带软删参数）→ Shiyu 登录演示账号，对两张卡各开一次会话预热向量。

**测试**：本地只跑受影响的测试文件，再加一次 `npm test`；库用 Docker 起的 PG；合并门是分支 CI；合并只做 git 操作，不跑测试、不等 CI。

**范围规矩**：执行中新发现的问题，属于本段改动面的直接修；只有会撞车或需要 Shiyu 拍板时才停下报告，不自行记账。

### WP9 真实验收
第 10 节。合并进 main 前必须做完；拆分执行时，放在最后一个触及蒸馏路径的执行 spec 里。

## 5. 依赖、冲突与拆分建议

| WP | 依赖 | 主要文件 | 冲突 |
|---|---|---|---|
| WP1 | — | `adapters/llm_adapter.py`、`core/distiller.py`（5 处改调入口） | 与 WP2、WP4 同文件；`distiller.py` 与 WP3–WP8 同文件 |
| WP2 | — | `adapters/llm_adapter.py`、`web/server.py` | 与 WP1、WP4 同文件 |
| WP3 | WP1 | `core/distiller.py`、`core/character_roster.py` | 与 WP5、WP7、WP8 同在 `distiller.py` |
| WP4 | — | `adapters/llm_adapter.py`、`core/distiller.py` | 同上 |
| WP5 | WP1、WP4 | `core/distiller.py` | 同上 |
| WP6 | — | `config.example.yaml` | 无 |
| WP7 | WP1、WP4、WP5 | `core/distiller.py`、`core/schema.py` | 同上 |
| WP8 | — | `storage/*`、`web/routers/distill.py`、`core/distiller.py`（仅缓存键函数和常量） | `distiller.py` 只改 `:220-256`、`:1856-1868`（命中循环与提示词构造前移）与 `:1913` |
| WP9 | 全部 | — | — |

**拆法（2026-09-25 修订：WP8 已合入 main `0dbb3eb`）**
- **A 线**（现窗口，`feat/distill-longbook`）：WP1 + WP2 → 合入 → WP4 → WP5 → WP6 → WP7 → 蒸馏验收（§10 C、D）。
- **C 线**（新开，WP1 + WP2 合入 main 之后再开）：WP3 → 识别验收（§10 A、B）→ 合入。
- 为什么能并行：WP3 只动识别段（`core/distiller.py:942-1135` 的识别函数）与 `core/character_roster.py`；A 线后续改的是 `_collect_stream`（`:618-680`）、分批合并（`:1494-1560`、`:1982-2030`）、格式化（`:2040-2070`）与 `core/schema.py`，行不重叠。测试文件也不重叠（C 线：`test_identify_whole_book.py`、`test_character_roster.py`；A 线：`test_distiller_routing.py`、`test_distill_usage_accounting.py`）。
- 为什么 C 线要等 WP1 + WP2 合入：WP3 的编排测试要桩 `chat_stream_long`，这个方法是 WP1 才加的。
- 为什么不再多开：WP4 要改 WP1 新建的 `_stream`；WP7 的「分批时跳过总合并」与 WP5 改的是同一段；这三个拆开只会制造冲突。
- 两次真实验收不同时跑：同一个 DeepSeek 账号并发上限 500，识别与蒸馏各 250 会顶满。
- 两条线各自合入前先 `git merge origin/main`。

## 6. 决策记录

| # | 决策 | 依据 | 否掉的 |
|---|---|---|---|
| D1 | 识别合并改为代码汇总，模型只判别名 | LangExtract、CoSER、LLM×MapReduce、CROSS（第 0 节）；按次数截断会丢刘姥姥、妙玉（第 1 节第 12 条） | 模型写整份名单（输出量随人数线性增长，生产 500 的现场）；Top-K 截断 |
| D2 | 流式用量以返回值交出 | OpenAI 协议 usage 属于单次响应；本仓 `async_chat` 已返回 `(text, usage)`；复现见第 1 节第 20 条 | `threading.local`；每条并行调用各配一个 adapter 实例 |
| D3 | 任一批合并失败即整体失败，不加批级重试 | Hadoop：任务失败达上限即作业失败，输出只在全成功后提交；嵌套重试相乘是本仓 D1a 的教训 | 丢批继续；收集层重放 |
| D4 | 失败任务也可续跑，缓存键 = 渲染后的 Map 请求指纹 + 模型 | 第 1 节第 23、24 条；识别缓存键先例 | 手工版本常量；加列做任务级门；按时间过期 |
| D5 | 分批时跳过总合并，4 组直接读批结果 | 总合并与格式化是串行的两次长输出，跳过省一整段 | 先总合并再格式化 |
| D6 | 格式化 4 组并行（Skeleton-of-Thought） | 提速来自并行，不依赖前缀缓存 | 单次串行格式化 |
| D7 | `IDENTIFY_VERSION` 2→3 不加测试 | 缺陷 89 的锁（`tests/test_character_roster.py:97`）用 monkeypatch 验机制，不看字面值；其余测试都按符号引用 `IDENTIFY_VERSION`。不加字面值钉子（改一次动一次，不是机制），2→3 由审计核对。 | 字面值钉子 |

## 7. 时间推导

输入：逐片单次耗时实测 1.4–31.3 s、输出速度实测约 55–78 t/s、首字约 1.5 s（第 1 节第 4 条）；保守速度取 32 t/s（Artificial Analysis 对 V4 Pro 非推理的最低报数）；10 万 token 输入的首字按约 13 s 计（DeepSeek 官方：128K 提示词无缓存首字约 13 s）。

| 环节 | 实测速度下 | 保守速度下 |
|---|---|---|
| 识别·逐片（242 片一次发完，每片输出 ≤2.7k token） | ≤约 45 s | ≤约 90 s |
| 识别·别名判断（输入约 4k token，输出 ≤2k token） | 约 30 s | 约 65 s |
| **识别合计** | **约 1.3 分钟** | **约 2.6 分钟** |
| 宝玉·逐片（207 片一次发完） | 约 31 s（实测最慢） | 约 70 s |
| 宝玉·分批合并（3 批并行，每批输入约 10 万 token，输出 3k–8k token） | 约 1–2.3 分钟 | 约 1.8–4.5 分钟 |
| 宝玉·格式化（4 组并行，每组输出 1–2k token） | 约 20–35 s | 约 35–65 s |
| **宝玉合计** | **约 1.8–3.4 分钟** | **约 3.5–6.7 分钟** |

决定宝玉耗时的主要未知量是**分批合并的自然输出长度**（3k 还是接近 8k），第 10 节必须实测报出。
（WP8 不改变首跑耗时，只缩短失败后重试的耗时：宝玉重试 ≈ 分批合并 + 格式化，约 1.3–2.9 分钟，推导同上表。）

## 8. 测试清单

**通用规则**
1. **先红后绿**：每行测试先在改前跑红（贴读数），再实现变绿。
2. **桩的层级**：WP3、WP7 用 `MagicMock` 桩 `llm.async_chat` / `llm.chat_stream`（沿用 `tests/test_identify_whole_book.py:56` 的 `_make_llm`、`tests/test_distiller_routing.py:11` `TestReduceAllEmptyBails` 的 100 片生产形状假件）。只有「墙钟」「截断信号」两类走**真 `LLMAdapter` + 本地假 SSE 服务**，复用 WP1 已建的那个（先 grep，不建第二个）。
3. **时间缩放，不真等**：`monkeypatch.setattr(adapters.llm_adapter, "_GEN_ATTEMPT_S", 0.5)`、`("_GEN_DEADLINE_S", 1.5)`（下限 0.25 / 1.25，`adapters/llm_adapter.py:94-95`），假服务按比例静默。单条用例 ≤ 5 s。
4. **并发一律用 `threading.Barrier(n, timeout=2)` 判定**，不用 sleep 比时间戳：串行时 barrier 超时抛 `BrokenBarrierError`，确定性变红、不抖。
5. **变异流程**（每行一次）：工作区只改「变异」列那一处 → 只跑该行测试节点 → 记「红/绿 + 首条失败断言」→ `git checkout -- <文件>` 还原；变异不进 commit。先对**现有**测试跑同一变异，已红的不新增测试，读数照报。
6. **放置**：按表中文件加；表里没列的不新建测试文件。

### WP1、WP2（`tests/conftest.py` 新建进程内线程版假 SSE fixture：可配静默毫秒、吐片数、吐 N 片后断连，只交出 base_url；`tests/perf/mock_llm_server.py` 不动——它是 perf 子进程装置、不是 fixture，也表达不了「吐一片后断连」。WP5 的 R1、R2 复用这个 fixture）

| # | 守的行为 | 文件 | 构造 → 断言 | 变异（每条都要红） |
|---|---|---|---|---|
| S1 | 长输出流容忍 prefill 静默，聊天流不变 | `tests/test_llm_adapter_retry.py` | 假服务先回 200 头、静默 1.0 s 再吐内容；monkeypatch `_STREAM_ATTEMPT_S=0.5`、`_STREAM_DEADLINE_S=1.5`：`chat_stream_long` → 成功；`chat_stream` → 读超时。`test_timeout_family_defaults_unchanged` 补 `_BATCH_STREAM_READ_S == 300.0` | ① `chat_stream_long` 的 `read_s` 改回 ceiling ② 放宽时把 connect 也放大（断言 connect 仍 = ceiling） |
| S2 | 蒸馏只用长输出流 | `tests/test_llm_adapter_retry.py`（已落此处） | AST：`core/distiller.py` 中没有对 LLM 接收者的 `.chat_stream(` 调用；形态锁原有断言保持绿 | 任一处改回 `chat_stream` |
| S3 | 流中断 → 503 + 上屏文案；边界锁覆盖全部 LLM 异常类 | `tests/test_chat_stream_error.py` | 参数化两例（真 openai 客户端 + 真 socket）：假服务吐一片后断连；假服务静默超过聊天读超时（生产现场的 `ReadTimeout`）。最小 app 装 `register_domain_error_handlers`，路由消费 `chat_stream` → 503，`detail` == 「模型服务暂时不可用，请稍后重试」（与兜底文案差一词，有分辨力），`__cause__` 是 `httpx2` 异常；边界锁改为扫 `llm_error_types()` 全部类名 | ① 不包装 ② 包装面放宽成 `except Exception`（喂一个循环体内的 `AttributeError`，断言仍非 503） ③ `_TRANSPORT_ERRORS` 去掉 `APIConnectionError`（用例：桩一个抛 `openai.APIConnectionError(request=httpx2.Request("POST","http://x"))` 的流） ④ `llm_error_types` 不含 `UpstreamFailure` ⑤ 不登记 503 ⑥ `_upstream_user_message` 不认传输层（断言建流阶段网络错误重试耗尽后的文案 == 读流中断的文案） ⑦ 边界锁改回字面量 |

### WP3
纯函数测试放 `tests/test_character_roster.py`（直接喂列表）；编排测试放 `tests/test_identify_whole_book.py`（`TestWholeBookCoverage` 按新口径重写，其余两类保留）。

| # | 守的行为 | 构造 → 断言 | 变异（每条都要红） |
|---|---|---|---|
| I1 | 合并阶段只 1 次别名判断，输入无理由、无正文 | 编排测试：3 片桩，reason 与片内各放可搜标记 → `chat_stream.call_count == 1`，其输入不含标记，含每组主名与出现分片数 | 恢复 main 上的 `_identify_merge` |
| I2 | 别名判断的重修也走流式 | 编排测试：首次回「不是 JSON」→ `chat_stream.call_count == 2`、`chat.call_count == 0`（改写自原 `test_merge_repair_also_uses_stream`） | 重修改 `stream=False` |
| I3 | 歧义别名：不并组、且从所有组移除 | 纯函数：片1 甲{二爷}、片2 乙{二爷}、组对为空 → 甲乙分开，两者 aliases 都不含「二爷」；再给组对（甲, 丙），丙的「三爷」也挂在丁名下 → 合并组不含「三爷」 | ① 共享任一别名即并 ② 删掉移除那一步 |
| I4 | 主次、排序、理由、不截断 | 纯函数，一套 40 片数据（10% = 4）：X 3 片其中 2 片主要（写法「主角」「主要角色」）→ 主要，理由取主要片那条；Y 3 片、1 片主要、同片重复一次 → 次要、`chunk_count == 3`；Z 恰 4 片全「配角」→ 主要；再加 57 个一次性人物 → 输出组数 == 60，主要在前、组内按 `(main_count, chunk_count)` 降序 | ① 只按 chunk_count 判 ② `>=` 改 `>` ③ 规范化改 `== "主要"` ④ 按条目计数 ⑤ 去掉主次分层 ⑥ 理由取第一条 ⑦ 加 `[:50]` |
| I5 | 记账 = 逐片汇总 1 行 + 别名判断 1 行 | 先把变异跑在 `tests/test_usage_accounting_lock.py`，红了就不新增；没红才在 `tests/test_distill_usage_accounting.py` 加一条断言 `["distill_identify"] * 2` | 别名判断改为直接 `self._llm.chat_stream(...)` |

收尾：删 `test_merge_call_uses_stream`、`test_merge_max_tokens_is_raised`；改写 `tests/test_usage_identity_context.py:398` 的注释；`git grep -n "IDENTIFY_MERGE_PROMPT\|IDENTIFY_MERGE_MAX_TOKENS"` 为 0。版本号见 第 6 节 D7。

### WP4
| # | 守的行为 | 文件 | 构造 → 断言 | 变异（每条都要红） |
|---|---|---|---|---|
| R4 | 并发流式各记各的账（D2） | `tests/test_distill_usage_accounting.py` | 3 批并发，桩按批 `return` 各自 usage（ct = 101/202/303），吐完末片后先 `Barrier(3).wait()` 再结束 → 落账三行 == {101, 202, 303} | `_collect_stream` 改回读 `self._llm.last_usage` |
| R4b | `yield from` 的两处也记本次返回值 | `tests/test_distill_usage_accounting.py` | 桩 `chat_stream_long` 吐完后 `return {ct: 404}`，同时把 `last_usage` 设成 `{ct: 999}` → `_distill_longcontext_stream`、`_single_reduce_stream` 各跑一次，落账 == 404 | 任一处改回读 `last_usage` / 不传 usage |

### WP5
| # | 守的行为 | 文件 | 构造 → 断言 | 变异（每条都要红） |
|---|---|---|---|---|
| R1 | 分批合并不受 45/60 s 墙钟 | `tests/test_distiller_routing.py`（用 conftest 的假 SSE fixture） | 真 adapter 指向假服务，缩放 0.5/1.5；假服务先回 200 头、静默 2.0 s、再 0.5 s 内吐完（非流式请求同样 2.5 s 后回整包）→ `_single_reduce_async` 返回全文 | 恢复 `async_chat` |
| R2 | 任一批失败即整体失败，不进格式化（D3） | `tests/test_distiller_routing.py` | 100 片（2 批），参数化两例：第 2 批 `finish_reason=length`（真 adapter + 假服务）/ 第 2 批回空串 → 帧含 `{"error": …}`、无 `formatting`、格式化 0 次；同时断言分批调用的 `max_tokens == 8192` | ① 截断照常交出半截 ② 恢复单批吞异常 ③ 恢复空批跳过 ④ 不传 `max_tokens` |
| R3 | 批线程带上调用方身份 | `tests/test_distiller_routing.py` | `LLM_CALLER.set(Caller(user_id="u_ctx"))`，桩在每批调用内读 `current_user_id()` → 全部 == `"u_ctx"` | 线程执行换成 `loop.run_in_executor(None, …)` |

### WP6
无测试；30→250 由审计核对。

### WP7（均在 `tests/test_distiller_routing.py`，F4 除外）
| # | 守的行为 | 构造 → 断言 | 变异（每条都要红） |
|---|---|---|---|
| F1 | 4 组并行、各记各账、状态帧不变 | 格式化桩内 `Barrier(4, timeout=2).wait()` → 通过；`distill_format` 落 4 行且各不相同；`formatting` 帧恰 1 次、在 4 组调用之前 | ① 改回串行 ② 每组各发一次 `formatting` |
| F2 | 批数决定是否先总合并 | 参数化：100 片 → reduce 调用恰 2 次（无总合并），4 组输入都含两批结果；50 片 → reduce 恰 1 次，4 组输入 == 这次合并的输出 | ① 恢复总合并 ② 一律跳过合并 |
| F3 | 字段划分完整、互不重叠 | 读 `core/schema.py` 的分组定义：并集 == `CharacterCard.model_fields`，两两无交集 | 从某组删一个字段 |
| F4 | 一张完整卡，两条消费路径都能解析 | `tests/test_identify_failure_channels.py`：复用 `:174` 后台任务、`:195` SSE 两套装置 → 恰 1 个 `str` 帧，累加串 `json.loads` + `CharacterCard.model_validate` 通过 | 逐组 `yield` |
| F5 | 任一组失败不拼半张卡 | 第 3 组桩抛异常 → 有 `{"error"}` 帧、无 `str` 帧 | 失败组填 `{}` 继续 |

### WP8（`tests/test_distill_task_api.py` 已有续跑测试类 `TestEResumeGate`，就近放；存储层放 `tests/test_postgres_store.py`）

| # | 守的行为 | 构造 → 断言 | 变异（每条都要红） |
|---|---|---|---|
| C1 | error 任务可续跑，done 不可，force 跳过 | 存储层：同一 (user, text, character) 分别造 interrupted / error / done 三种最新行 → 前两种能查到、done 查不到；路由层：error 行带 2 片断点 → `/start` 复用原 task_id，Map 调用数 = 相关片数 − 2；`force=True` → 新 task_id、全量 Map | ① 条件只留 `interrupted` ② 条件加上 `done` ③ 忽略 `force` |
| C2 | 换模型不复用 | 断点以模型 m1 写入，用 m2 再蒸 → 这些片全部重跑 | 缓存键去掉 model |
| C3 | 改 Map 提示词不复用 | 断点写入后把该 Distiller 实例的 `_map_system_prompt` 换成返回文本多一个字的函数→ 全部重跑 | 缓存键只取原文、不取渲染后的提示词 |
| C4 | 缓存键只有一处定义 | 写入与校验两处都经 `chunk_cache_key`：写入后用它校验能命中（往返测试） | 写入处改回只用 `text_fingerprint(chunk)` |

## 9. 测试运行

1. `docker compose -f docker-compose.test.yml up -d --wait`（端口 55432，`tests/conftest.py:27-32` 强制连它）。
2. 每步只跑以下文件：
   - WP1 + WP2：`test_llm_adapter_retry.py test_usage_accounting_lock.py test_chat_stream_error.py test_llm_adapter_finish_reason.py test_domain_exception_exit.py test_exception_pickle_lock.py`
   - WP3：`test_identify_whole_book.py test_character_roster.py test_identify_failure_channels.py test_usage_identity_context.py test_distill_usage_accounting.py test_usage_accounting_lock.py test_distill_task_api.py`
   - WP4 + WP5：`test_distiller_routing.py test_distill_usage_accounting.py test_usage_accounting_lock.py test_llm_access_gate.py test_distill_resume.py test_llm_adapter_finish_reason.py`
   - WP8：`test_distill_task_api.py test_postgres_store.py test_storage.py test_distill_resume.py`
   - WP7：`test_distiller_routing.py test_identify_failure_channels.py test_distill_progress.py test_distill_task_api.py test_distill_usage_accounting.py`
3. 每步另跑一次 `npm test`（`web/frontend`）：红线要求前端零改动，用它确认没被波及。
4. 不跑本地全量；报告里不出现本地全量数字。合并门是分支 CI；合并只做 git 操作，不跑测试、不等 CI。

## 10. 真实验收（WP9）

**A. 环境对齐（先做，任一不符停下）**
1. 生产无 `config.yaml`：验收期间把 worktree 根的 `config.yaml` 改名 `config.yaml.bak`，结束后改回。起服务后打印 `get_distiller()` 的 `_chunk_size` / `_map_concurrency` 与 adapter 的 model、thinking → 必须 5000 / 250 / `deepseek-v4-pro` / 关。
2. 登录生产（SG）读 app 容器 `env | grep -E '^LLM_'`，本地 `.env` 的 `LLM_*` 逐项与之一致；报 diff。
3. 红楼梦全文：字符数 == 866,149，且 `Distiller._split_chunks(text, 5000)` == 242 片（与生产那次一致，第 1 节第 28 条）；不符 = 不是同一份，停下。上传选 `text_type=classic`，与生产那次相同；蒸馏按 6000 字分片，报实际相关片数（第 1 节第 29 条）。
4. 本地整栈（docker PG + 本地 app），测试账号配真 key；全程经 HTTP 路由。

**B. 识别**
1. 经 `/api/distill/identify` 跑一次。计时：整请求 = 客户端计时；逐片结束、别名判断结束 = PG `usage_stats` 本用户 `action='distill_identify'` 两行的 `created_at` 减请求发出时刻；多出的行（重修）照报。
2. 质量判据（对响应 JSON 用代码判，不肉眼）：
   - 宝玉、黛玉、宝钗、凤姐、贾母、王夫人、袭人、晴雯、平儿、探春、妙玉、刘姥姥：每人在「name ∪ aliases」中恰命中 1 组，且该组 `importance == "主要"`；
   - 宝玉与宝二爷、凤姐与王熙凤各自命中**同一**组；
   - 泛称 {夫人, 婆子, 二爷, 奶奶, 太太, 姑娘}：不是任何组的 name，不在宝玉、凤姐、贾琏、黛玉的 aliases 里；
   - 报主要人物总数与完整列表，四人 aliases 原样贴出。
3. 选片对比：宝玉按生产规则（`core/distiller.py:1831` `match_terms`，`:1838` 子串筛片）数相关片：「本名 + 新别名」与「仅本名」两个数，差出来的片逐个归到具体别名。（旧名单不另跑：旧合并正是生产 500 的现场，重跑成本高、结果不稳；对照用仅本名。）
4. 硬门槛：整请求 ≤ 8 分钟；目标 1–3 分钟。逐片输出 token 分布不单测（HTTP 路径只有一行汇总账），逐片阶段超时就看这一行的耗时。

**C. 蒸馏（宝玉，再刘姥姥）**
1. 经 `/api/distill/start` 起任务，每 1 s 轮询 `/api/distill/task/{id}`，记每次状态变化时刻。
2. 分阶段（取本用户 `usage_stats` 行）：逐片 = `distill_map` 行时刻 − 任务起点；每批合并 = 该批 `distill_reduce` 行时刻 − `distill_map` 行时刻，并报该行 completion_tokens（即自然输出长度）；每组格式化 = 该组 `distill_format` 行时刻 − 最后一批 `distill_reduce` 行时刻；整任务 = 客户端首尾。
3. 报相关片数、批数。
4. 卡片：`CharacterCard.model_validate` 通过；所有顶层字段非空；`dialogue_examples`、`catchphrases` 各取前 5 条，去首尾引号与空白后在原文做子串查找，报命中数，未命中的原样贴出由 Shiyu 判。卡片不入库、不进 commit。

**D. 通用**
1. 服务端日志计数：`429`、HTTP 5xx、`读取流式响应失败`、`Reduce batch`。
2. 停下条件（贴原始读数，不调参、不合并）：识别 > 8 分钟；宝玉 > 5 分钟；任一批 completion_tokens ≥ 8192 或出现截断；出现 429；任一 5xx；B2 任一条不成立。
3. 读数模板（照填）：

```
[识别] 整请求 __s | 逐片 __s | 别名判断 __s | 账行数 __
[识别] 主要人物 __ 人: ...
[识别] B2 判据: 12人 __/12 | 同组 2/2 | 泛称 通过/失败(贴)
[识别] 宝玉相关片: 本名+别名 __ | 仅本名 __ | 差异归因: ...
[蒸馏-宝玉] 相关片 __ | 批数 __ | 逐片 __s | 批1 __s/__tok | 批2 __s/__tok | 批3 __s/__tok | 组1..4 __s | 整任务 __s
[蒸馏-宝玉] 卡片校验 通过/失败 | 空字段 __ | 原文命中 对话 __/5 口癖 __/5
[蒸馏-刘姥姥] 同上
[日志] 429 __ | 5xx __ | 读取流式响应失败 __ | Reduce batch __
```

**E. 续跑**：不另做真实演练。C1 走真 `/start` 路由 + 真 PG，已直接断言「失败后重试的 Map 调用数 = 相关片数 − 已存片数」，这就是产品层结果。

## 11. 交付报告

1. S0：第 1 节中本执行 spec 涉及的坐标逐条复核（含第 20–26 条）；WP5 线程执行采用的机制与 `LLM_CALLER` 传播依据（R3 的测试节点名即证据）。
2. 每个 WP：commit subject、改动文件、第 8 节行号 → 测试节点名 → 改前红读数 → 变异读数（红/绿 + 首条失败断言）。
3. 第 10 节读数模板全文。
4. 记账：本段改动面内的新发现直接修，写进报告；只有撞车或需 Shiyu 拍板时停下报告；不自行新增台账条目。
5. 记录项（不修）：非流式 `distill_incremental` 的压缩与格式化仍是非流式；D3 使 `/run` 的 `_do_reduce` 也变为单批失败即整体失败。技术债：`last_usage` 仍供单线程的 `chat_engine`、`agent_loop` 读，adapter 里的用量暂有两个来源；迁移这两处另立项。
