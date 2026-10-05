# distill-capacity：以官方 tokenizer 精确计数决定上限与路径（单一来源）

> **v3，取代 v1（`5def4716`）与 v2（`0dd04e9c`/`20b5e55e`）**：v1 用「字数 × 0.72」自研估算；v2 改为官方 tokenizer 精确计数但只换了蒸馏一处；v3 按「先搜后写」把仓库里**全部三套** token 估算统一到一个计数函数（Shiyu 2026-10-04 拍板）。
> 基线：main @ `ad3bc7e1`。分支：`feat/distill-capacity`。本文件先于实现提交供审阅；实现、测试、变异预跑后追加到 §8，补充写进 §9。末尾 §10 为对照标准的自检表。

## 0. 目的

1. 小说能一次读完的上限，从「100 万字」改为**按官方 tokenizer 数出的 token 数 < 90 万**（约合 120–130 万中文字，因文本而异），覆盖 100 万字以上的长篇（例：120.1 万字的网文实测 816,821 tokens，可收）。
2. 收下的小说**一定**走一次读完、**一定**不超窗；判断用真实 token 数，不再用系数近似。
3. 阈值、窗口、各类上限、文件体积上限各只有一处定义，前后端共用；**全仓库只有一个 token 计数函数**（现有三套系数估算全部删除）。
4. 万一超窗，用户看到「文本过长」，而不是「请稍后重试」。

## 1. 已拍板（2026-10-04）

| # | 决定 |
|---|---|
| D1 | 独立模块 `core/length_budget.py` 集中定义阈值、窗口、各类上限、文件体积上限；`Distiller`、`TextManager`、上传路由、前端只从它取 |
| D2 | 去掉配置对 `longctx_threshold` 的覆盖（生产无 `config.yaml`，C6） |
| D3 | 一次读完阈值仍为 90 万 tokens，本段不动 |
| D4 | 聊天记录仍按 200 万**字**限（产品上限，已有角色过滤，不动其流程） |
| D5 | **token 一律用官方 tokenizer 精确计数**（现成库 `tokenizers` + DeepSeek 官方 `tokenizer.json`），删除系数估算 |
| D6 | 统一范围：蒸馏选路径、聊天上下文预算、用量估算兜底三处现有估算（C12）全部改用同一个 `count_tokens`，不再新增第四套 |

### 1.1 方案对比（D5 的依据，按「先找现成库」）

| 方案 | 做法 | 结论 |
|---|---|---|
| **A 官方 tokenizer（选）** | HuggingFace `tokenizers`（Apache-2.0，**已在锁定依赖里**：`requirements.txt:349` `tokenizers==0.23.2`，由 chromadb 引入）加载官方 `tokenizer.json` | 精确、零新增依赖；实测加载 0.27s、整本 120 万字编码 2.18s |
| B 系数估算 | `len × 0.72` | 近似，靠保守系数兜余量；自研，否 |
| C 接口返回 `prompt_tokens` | 调用后才知道 | 只能事后核对；保留为 §7 监测 |

## 2. 已查实约束（坐标为 `ad3bc7e1`；S0 由执行方逐条复核，任一不成立即停下报告）

C1. 阈值：`core/distiller.py:597` 读配置 `distill.longctx_threshold`，默认 900000；判断为严格小于（`:2066`、`:2251`）。
C2. 估算：`core/distiller.py:1550-1552` `int(len(text) * 0.6)`，仅 `:2064`、`:2249` 用于选路径；聊天在估算**之后**才做角色过滤（`:2066-2069`），本段保持该顺序（D4）。
C3. 上传上限两处重复：`core/text_manager.py:274-277`（`upload_text_from_file`，`:250`）与 `:399-402`（`upload_text`，`:392`），两者都是 `async def`；文案模板 `core/text_failure.py:27`。
C4. 文件体积：后端 `web/routers/text.py:108` 30MB，413 文案 `:156` 写死「100 万字」；前端写成 100MB 且两处各自定义：`TextPanel.jsx:19`（`MAX_BYTES`，`:46` 用）、`useAppStore.js:686-688`（`MAX_SIZE`）。
C5. `TextPanel.jsx:57-63` 的 `charCountClass` 写死 100 万档，**全文件无调用**（死代码）；`:268` 上传提示写死「小说上限 100 万字 · 聊天记录上限 200 万字 · 单文件最大 100MB」。前端目前没有从后端取上限的接口；现有相关测试只有 `components/__tests__/TextPanelDeleteError.test.jsx`。
C6. 生产两台（SZ 47.107.42.111、SG 43.134.55.201）容器内均无 `/app/config.yaml`，`config.example.yaml` 中 `longctx` 零命中 → 生效值为代码默认 900000（Shiyu 实查）。
C7. 思考：`adapters/llm_adapter.py:414` DeepSeek 统一 `thinking: {type: disabled}`，推理不占窗口。
C8. 一次读完输出上限 `LONG_OUTPUT_MAX_TOKENS = 16384`（`core/distiller.py:534`）；除原文外的提示词实测 4,807 tokens。
C9. 超窗现状：400 判确定性失败、不重试（`llm_adapter.py:242-246`）；上屏文案唯一取值处 `_upstream_user_message`（`:520-531`）只查状态码表（`:486`，无 400）→ 落通用文案「服务暂时不可用，请稍后重试」。
C10. 人物识别按段落切片，调用数 = 片数 + 5；红楼梦 866,149 字 242 片、约 4 元 38 秒（`docs/specs/distill-longbook-blueprint.md:56`、`:306`）；不受本段影响。
C12. 仓库现有**三套**互不一致的 token 估算（`git grep` 全量，见附录 B）：
- `core/context_engine.py:28-30` `_count_tokens = max(1, int(len × 0.8))`：聊天上下文预算，调用方 `core/chat_engine.py:21/386/389/393/522-523`、`core/group_session.py:11/118`、`scripts/run_agent_eval.py:43/102/116/418`；
- `core/distiller.py:1550-1552` `_estimate_tokens = int(len × 0.6)`：蒸馏选路径（C2）；
- `core/utils.py:13-29` `_CHARS_PER_TOKEN = 1.5` 与 `estimate_usage_from_chars(prompt_chars, completion_chars)`：接口不回用量时的估算兜底（结果带 `estimated=True`），调用方 `adapters/llm_adapter.py:1124`（流式逐片累加 `completion_chars`）、`core/distiller.py:825/877/882/1900/2166`；各调用点手上都有原文（`parts`、`system/messages`、流式 `piece`）。
- 测试引用：`tests/test_distiller_routing.py:272-416`（`_estimate_tokens`）、`tests/test_distill_usage_accounting.py:37/245/274/301`（`estimate_usage_from_chars`）。

C11. 官方 tokenizer（2026-10-04 §7.1 核对后修正）：V4.1 官方文件与 V3 系列**文件不同**——HF `deepseek-ai/DeepSeek-V4.1-Flash` 根目录 `tokenizer.json` sha256 `c90dfa01…`（6,367,257B）；官方 V3 `621ac2e3…`、V3.1/V3.2-Exp `32b34a41…`（执行方实测）；DeepSeek 官方 GitHub `deepseek-ai/deepseek-recipe` 的 `static/tokenizers/v41/tokenizer.json`（`81f64d12…`，只改了 id 129264 图像 token）。用 V4.1 文件实测：词表含特殊 token 129,280、不含 128,000；《孔乙己》2,635 字 → **1,848** tokens（解码还原一致），《阿Q正传》22,101 字 → 15,438，120 万字网文 → 816,821，提示词（除原文）4,807 —— 与此前沙箱文件**逐个相同**：差别只在特殊 token，正文分词一致（与 V4 报告 §4.1「在 V3 基础上只加少量特殊 token」相符）。入库用 HF V4.1 官方文件。

### 2.1 路径机制表（通道 × 执行上下文 × 守它的测试）

| 通道 | 新增的量 | 执行上下文 | 前提与处理 | 守它的测试 |
|---|---|---|---|---|
| 文件上传 `upload_text_from_file` | token 计数、体积上限 | 请求协程 | 计数是 CPU 密集（整本约 2s），**必须 `asyncio.to_thread`**，不阻塞事件循环 | R1、R2 |
| 文本上传 `upload_text` | 同上 | 请求协程 | 同上 | R3 |
| 蒸馏选路径（同步 `distill_incremental`） | token 计数 | 后台线程 | 直接调用 | R4 |
| 蒸馏选路径（流式 `distill_incremental_stream`，`/start` 与 `/run_stream` 共用） | token 计数 | 后台线程 / 生成器 | 直接调用；不改心跳与超时 | R5 |
| tokenizer 加载 | 一次（约 0.27s） | 首次使用的线程 | 惰性单例 + 锁；`Tokenizer.encode` 线程安全 | U3 |
| 上屏文案 `_upstream_user_message` | 超窗判定 | 任意线程（纯函数） | 不改重试 | R6 |
| 聊天上下文预算（`chat_engine`、`group_session`） | token 计数 | 聊天请求协程内同步调用 | 单条消息毫秒级，不放线程；检索块的削减循环（`chat_engine.py:389`）每轮重算一次，量级同 | R8、R9 |
| 用量估算兜底（`llm_adapter` 流式、`distiller` 五处） | token 计数 | 调用 LLM 的线程 | 改为传原文；流式把 `piece` 累加成文本再计数 | R10 |
| `GET /api/text/limits` | 上限 JSON | 请求协程 | 纯读常量 | R7 |
| 前端上传校验与提示 | 上限 | 浏览器 | 只读接口返回值 | F1、F2 |

### 2.2 规模表

| 量 | 值 | 来源 |
|---|---|---|
| 窗口（输入 + 输出） | 1,048,576 | NVIDIA 官方 V4.1-Flash 模型卡 |
| 阈值（小说上限） | token 数 < 900,000 | 代码默认（C1、C6） |
| 最坏总量 | 899,999 + 4,807 + 16,384 = 921,190 | 余量 127,386（约 12%）；模块导入时断言 阈值 + 提示词预留 + 输出上限 < 窗口 |
| 约合字数（仅说明，不参与判断） | 120–130 万 | 实测：小说 0.680–0.701 token/字 |
| 聊天上限 | 2,000,000 字 | 产品上限（D4） |
| 文件体积 | 30MB | 后端现值（C4） |
| 上传计数耗时 | 整本 120 万字约 2.2s | 沙箱实测；在线程里跑 |
| 人物识别 | 片数 + 5 次 | C10 |

### 2.3 出处对照表

| 依据 | 条目 | 对应 |
|---|---|---|
| DeepSeek-V4.1-Flash 技术报告（arXiv 2609.19969） | 最长一百万 tokens 上下文 | §2.2 窗口 |
| NVIDIA 官方模型卡（build.nvidia.com/deepseek-ai/deepseek-v4.1-flash；docs.api.nvidia.com NIM 参考） | 输入 + 输出合计 1,048,576；词表 129,280 | §2.2、C11 |
| DeepSeek-V4 技术报告（arXiv 2606.19348）§4.1 | 沿用 V3 tokenizer、词表 128K | C11 |
| DeepSeek API 文档：Chat Completions、Thinking Mode、Token 用量 | `thinking.type: disabled`；离线 tokenizer | C7、D5 |
| huggingface/tokenizers（Apache-2.0） | `Tokenizer.from_file`、`encode` | D5、§3.1 |
| deepseek-harness 讨论 #3399（DeepSeek 官方仓库） | 生产超窗报文：HTTP 400、`Input token exceed the limit`、`code: quota_limit_reached`；另有 `maximum context length` 措辞 | §3.4 |

## 3. 设计

### 3.0 `core/tokens.py`（全仓库唯一的 token 计数）

```python
def count_tokens(text: str) -> int   # 官方 tokenizer 精确计数；惰性单例 + 锁；空串 → 0
```

- `tokenizer.json` 放 `core/assets/deepseek_tokenizer/`，同目录 `SOURCE.md` 记来源与 sha256。
- 三处现有估算的去向：
  - `context_engine._count_tokens` 删除，调用方（`chat_engine`、`group_session`、`scripts/run_agent_eval`）直接用 `count_tokens`。原函数的 `max(1, …)` 下限：逐个调用点核对是否依赖「至少为 1」，依赖的就在调用点写明，不在计数函数里加特例。
  - `distiller._estimate_tokens` 删除（§3.2）。
  - `utils.estimate_usage_from_chars` 与 `_CHARS_PER_TOKEN` 删除，换成 `estimate_usage(prompt_text, completion_text="")`：内部用 `count_tokens`，仍返回 `estimated=True`（语义不变：这是「接口没回用量」时的数，不是接口的数）。六个调用点改为传原文。
- **已知局限**：计数用的是 DeepSeek 的 tokenizer。用户在设置页换成别家模型时，这个数对他们的模型是近似值；线上默认模型是 `deepseek-flash`，对它是精确值。

### 3.1 `core/length_budget.py`（纯常量与纯函数）

```python
CONTEXT_WINDOW_TOKENS   = 1_048_576
LONGCTX_THRESHOLD_TOKENS = 900_000      # 唯一定义处（D2/D3）
PROMPT_RESERVE_TOKENS    = 8_192        # ≥ 实测 4,807，留余量
CHAT_MAX_CHARS           = 2_000_000    # D4
MAX_FILE_BYTES           = 30 * 1024 * 1024
# 导入时断言：阈值 + PROMPT_RESERVE + LONG_OUTPUT_MAX_TOKENS < 窗口

def fits_one_pass(n_tokens: int) -> bool  # n_tokens < 阈值 —— 选路径与小说上限共用这一个判断
def check_upload(text: str, text_type: str) -> None   # 超限抛 ValueError（文案见下）；chat 按字、其余按 token
def public_limits() -> dict               # {story_max_tokens, chat_max_chars, max_file_bytes}
```

- 计数一律调 `core.tokens.count_tokens`。
- 小说超限文案：`文本共 {n:,} tokens，超过小说上限 {阈值:,} tokens，请分卷上传`（精确数，不换算字数）。

### 3.2 后端接线（去重，不留旧值）

- `Distiller`：删 `_estimate_tokens`；两处选路径改为 `fits_one_pass(count_tokens(text))`；`self._longctx_threshold` 取 `LONGCTX_THRESHOLD_TOKENS`（保留属性名，现有测试调小它走分片）；删读配置那一行与过时注释。
- `TextManager`：两段重复判断收为私有 `_check_length`，内部 `await asyncio.to_thread(check_upload, parsed, text_type)`；两条上传路径都调它。
- `text.py`：`MAX_FILE_SIZE` 引用 `MAX_FILE_BYTES`；413 文案由常量生成。新增 `GET /api/text/limits`，沿用该路由的登录依赖。
- `requirements.in` 把 `tokenizers` 升为**直接依赖**（版本沿用锁定的 0.23.2，重新编译锁文件不应引入别的变化）。

### 3.3 前端（单一来源 = 接口）

- 新增 `web/frontend/src/lib/textLimits.js`：取 `/api/text/limits` 并缓存；失败时禁用上传并显示「暂时无法获取上传限制」，不在前端写兜底数字。
- `TextPanel.jsx`：`validateFile` 体积上限取接口值；`:268` 提示文案由接口值生成（小说写「按 token 计，上限 90 万 tokens」）；删除 `MAX_BYTES` 与无人调用的 `charCountClass`（C5，含旧上限）。
- `useAppStore.js`：删除 `uploadText` 自带的 `MAX_SIZE`，体积只在 `validateFile` 校验一处。
- 视觉：沿用现有样式与组件，不新增视觉元素（读过 `frontend-design` skill：本段只改文案与禁用态，适用的只有「可访问性下限」——禁用态用 `disabled` 属性并给出可见说明）。

### 3.4 超窗上屏（`adapters/llm_adapter.py`）

- 新增纯函数 `_is_context_overflow(exc)`：状态码 400 且报文命中已知措辞（`Input token exceed the limit`、`maximum context length`、`exceeds model context limit`）。措辞表一处定义、附出处（§2.3）。
- `_upstream_user_message` 先判它 → 「文本过长，超出模型一次能处理的长度，请缩短后再试」；其余不变。重试行为不变。

## 4. 测试计划

### 4.1 单元

| 编号 | 断言 |
|---|---|
| U1 | `count_tokens` 与官方 tokenizer 直接编码结果一致（孔乙己公版样本固定期望值） |
| U2 | `fits_one_pass(899_999)` 真、`(900_000)` 假 |
| U3 | tokenizer 只加载一次（多线程并发首调用） |
| U4 | 导入时预算断言成立；把预留调到使总和 ≥ 窗口时断言失败 |
| U5 | `public_limits()` 三个字段等于模块常量 |

### 4.2 调用点矩阵（行 = 调用点，列 = 可观测输出，格内 = 测试名）

| 调用点 \ 输出 | 收 / 拒 | 拒绝文案 | 选的路径 | 响应 JSON | 界面状态 |
|---|---|---|---|---|---|
| R1 文件上传·小说 | `test_file_upload_story_boundary` | `test_file_upload_story_message` | — | — | — |
| R2 文件上传·体积 413 | `test_file_upload_size_413` | `test_413_message_from_constants` | — | — | — |
| R3 文本上传·小说 / 聊天 | `test_text_upload_story_boundary`、`test_text_upload_chat_boundary` | `test_text_upload_story_message` | — | — | — |
| R4 同步选路径 | — | — | `test_sync_route_by_token_count` | — | — |
| R5 流式选路径 | — | — | `test_stream_route_by_token_count` | — | — |
| R6 超窗上屏 | — | `test_overflow_400_user_message`、`test_other_400_unchanged` | — | — | — |
| R7 limits 接口 | — | — | — | `test_limits_endpoint` | — |
| R8 聊天上下文预算（`chat_engine`） | — | — | — | 预算内保留的上下文条数按精确计数：`test_chat_context_budget_uses_count_tokens` | — |
| R9 群聊预算（`group_session`） | — | — | — | `test_group_budget_uses_count_tokens` | — |
| R10 用量估算兜底（流式 / 非流式失败 / 截断） | — | — | — | 记账载荷 `prompt_tokens`/`completion_tokens` 等于原文的精确计数且 `estimated=True`：`test_estimated_usage_counts_text`（改写 `test_distill_usage_accounting.py` 现有三条） | — |
| F1 前端上传校验 | `TextLimits.test.jsx: rejects over max_file_bytes` | 同文件 `shows size message` | — | — | 同文件 `disables upload when limits fail` |
| F2 前端提示文案 | — | — | — | — | 同文件 `hint built from limits` |

边界用例按真实规模复刻关系：小说边界用「token 数 = 阈值 − 1 / = 阈值」（打桩 `count_tokens` 返回指定值，不造百万字文本）；计数本身的正确性由 U1 用公版《孔乙己》验证（受版权保护的长篇不入库）。

### 4.3 结构锁

| 编号 | 断言 |
|---|---|
| S1 | `core/ web/routers/ web/frontend/src/` 不再出现：`* 0.6`、`1_000_000`（上传上限）、`900000`/`900_000`（`length_budget` 以外）、`30 * 1024 * 1024`（以外）、`100 * 1024 * 1024`、「100 万」；`longctx_threshold` 不再被读取 |
| S2 | `core.tokens.count_tokens` 是全仓库唯一的 token 计数：`core/ web/ adapters/ scripts/` 中不再出现 `_count_tokens`、`_estimate_tokens`、`_CHARS_PER_TOKEN`、`estimate_usage_from_chars`、`len(...) * 0.6/0.8`、`/ 1.5`，`Tokenizer.from_file` 只在 `core/tokens.py` |

## 5. 变异清单（实现后在 PG 上预跑，全部打红才推；驱动基于 `tests/perf/mutation_framework.py`，产物 `tests/perf/distill_capacity_red_lines.json`）

| 编号 | 变异 | 应红 |
|---|---|---|
| M1 | `fits_one_pass` 用 `<=` | U2、R4、R5 |
| M2 | 一条上传路径绕过 `_check_length` | R1 或 R3 |
| M3 | `check_upload` 对小说按字数判 | R1、R3 |
| M4 | 计数不放线程（直接同步调用） | 计数在事件循环线程执行的断言（R1 附带） |
| M5 | 选路径换回字数估算 | R4、R5、S2 |
| M6 | `_upstream_user_message` 不判超窗 | R6 |
| M7 | 超窗判定不看状态码 | R6（`test_other_400_unchanged` 的反例） |
| M8 | 413 文案写死 | R2、S1 |
| M9 | 前端接口失败时放行 | F1 |
| M10 | 预算断言删掉 | U4 |
| M11 | 聊天预算改回 `len × 0.8` | R8、S2 |
| M12 | 用量兜底改回按字数除 1.5 | R10、S2 |
| M13 | 流式兜底只计最后一片（不累加） | R10 |
| M14 | `public_limits` 字段写死字面量（脱离常量） | U5 |
| M15 | limits 接口篡改字段（不原样回 `public_limits`） | R7 |
| M16 | 群聊预算改回按字数（`count_tokens` → `len`） | R9 |
| M17 | 聊天分支不判上限（`if n > CHAT_MAX_CHARS` → `if False`） | R3 |

**M9 不在驱动里**：它落在前端 vitest 文件上，而驱动框架的覆盖域、判别器解析（Python AST）、
红源解析（pytest `--tb=long`）都只认 `.py`，前端变异无从入域；F1 的覆盖走 §6 的 `npm test`。
M9 已**手动**跑过一次（2026-10-05）：把 `uploadDisabled` 改成 `return false`（接口失败时放行）
→ `npm test` **1 failed | 346 passed**，红的正是 `TextLimits.test.jsx > disables upload when
limits fail`（`expect(element).toBeDisabled()`，第 129 行「Received element is not disabled」）；
还原后该文件 **4 passed**，F1 有分辨力。

**M14–M17 是 §5 之外补的**：§5 原本只列到 M13，但 U5/R7/R9/R3-chat 这四条判别器**没有**任何
M1–M13 撞得到，元锁 `tests/test_lock_coverage.py` 会当场判「名单外缺口」（而这些判据都在本分支
新写的文件里，入不了「只收建档时刻就有」的缺口名单）。故每条补一条只改一处的专属变异，改法即
「修复前的代码形态」。

## 6. 本地命令（只跑受影响的文件；合并门是分支 CI）

```powershell
docker ps --format "{{.Names}} {{.Ports}}" | Select-String "55432"   # 有别的容器占用 55432 → 停下报告，不要停别人的容器
docker compose -f docker-compose.test.yml up -d --wait                # 项目名 character-distill-test，tmpfs 空库
python -m pytest -q tests/test_tokens.py tests/test_length_budget.py tests/test_error_user_facing.py tests/test_distiller_routing.py tests/test_chat.py tests/test_context_engine_evidence.py tests/test_group_members.py tests/test_agent_loop.py tests/test_text_failure_messages.py tests/test_card_draft.py tests/test_distill_resume.py tests/test_distill_usage_accounting.py tests/test_identify_failure_channels.py tests/test_usage_identity_context.py tests/test_lock_coverage.py
python tests/perf/distill_capacity_mutations.py
cd web/frontend; npm test
```

不跑本地全量；合并只做 git 操作。

## 7. 执行方专属步骤与线上核对

1. **tokenizer 来源核对（S0 必做）**：从 V4.1 官方仓库下载 `tokenizer.json`，算 sha256。与 C11 的 V3 值相同 → 证实 V4.1 沿用 V3 tokenizer，入库文件不变；不同 → 用 V4.1 的文件替换入库文件，更新 `SOURCE.md`，重跑 §6，结果写 §9。
2. **线上核对（监测，不是实验）**：上线后第一次蒸馏 100 万字以上的书，记录接口返回的 `prompt_tokens` 与本地 `count_tokens` 之差，写 §9；差超过提示词部分（约 5 千）即停下报告。

## 8. 进度

- [ ] 交接：`Test-Path docs/specs/distill-capacity.md` 为 True；`git log --oneline -3` 与远端一致
- [ ] 执行方 S0：C1–C11 复核、§7.1 tokenizer 来源核对（需访问 Hugging Face，只有本地能做，故本段由执行方实现）
- [ ] 实现（执行方，测试库一律 PG：`docker-compose.test.yml`，不用 SQLite）
- [ ] §4 先在 `ad3bc7e1` 上红、再在本分支上绿；§5 变异全红（执行方跑，贴原始输出）
- [ ] 审计（Claude 用 token 读 diff，逐文件给结论）
- [ ] 分支 CI 绿 → PR → Shiyu 合并

## 9. 补充

本段改动面内新发现的问题直接修并写进这里；需要拍板的停下报告，不自行记账。

### 9.1 §7.1 tokenizer 来源核对结果（2026-10-04）

- 执行方：V4.1 官方 `tokenizer.json` 与 C11 原期望值不一致；C11 原值来自第三方 GitHub 镜像，与官方 V3 也不一致（审计方原依据有误）。
- 审计方用 DeepSeek 官方 GitHub `deepseek-recipe` 的 V4.1 文件重算（见 C11）：正文 token 数与此前全部相同，§0、§2.2 的数字不变；U1 期望值为《孔乙己》1,848。
- 入库：HF V4.1 官方文件；`SOURCE.md` 记 URL、revision、下载日期、sha256、字节数，以及与 V3/V3.1/V3.2 官方文件不同、正文分词一致这一事实。

### 9.3 §4.3 S2 正则过宽修正（2026-10-05）

- **问题**：S2 原用裸正则 `\*\s*0\.[68]\b`，且扫描范围含 `web/frontend/src`，于是把前端一处**非 token 估算**的视口判断也算了命中：`web/frontend/src/components/ChatArea.jsx:395` 的 `window.screen.height * 0.8`（移动端键盘弹起检测）。这是**测试写宽**导致的误报，业务代码正确，未改 `ChatArea.jsx`。
- **修法（只改测试）**：① S2 扫描范围收为 **Python 源码**（`core/ web/ adapters/ scripts/` 下的 `*.py`）——前端没有 token 计数；② 比例形态收窄为 token 估算的形态 `len\s*\(.*?\)\s*\*\s*0\.[68]\b`（长度 × 小数系数），不再匹配任意的 `* 0.6/0.8`。其余标识（`_count_tokens`、`_estimate_tokens`、`_CHARS_PER_TOKEN`、`estimate_usage_from_chars`、`/ 1.5`）不变。
- **改动前完整命中列表**（旧正则、旧范围全扫，非截断）：

  ```
  web/frontend/src/components/ChatArea.jsx:395  pat='\*\s*0\.[68]\b'  ->  if (window.visualViewport.height < window.screen.height * 0.8) {
  ```

  共 **1 条** —— 确认除该视口判断外，没有任何真问题被这次误报盖住。基线 `fee2bf7b` 上该模式的两处**真·旧估算**（`core/context_engine.py:30` 的 `int(len(text) * 0.8)`、`core/distiller.py:1552` 的 `int(len(text) * 0.6)`）此前已随本次实现删除。
- **改动后**：`tests/test_length_budget.py` **19 passed**；在**扩大后**的 Python 范围（`core/ web/ adapters/ scripts/`）重扫 **0 命中**（`Tokenizer.from_file` 仍只在 `core/tokens.py`）。
- **分辨力未减**：收窄后仍能打红 §5 的 M5/M11/M12（它们正是把 `len(...) × 0.6/0.8`、`/ 1.5` 或旧标识塞回来）。

### 9.4 §5 变异驱动跑通与元锁闭合（2026-10-05）

- **驱动跑通**：`python tests/perf/distill_capacity_mutations.py` —— **17 条全红**（M1–M8、M10–M17），产物 `tests/perf/distill_capacity_red_lines.json` 已刷新，收尾 sha256 逐字节还原通过。
- **元锁闭合**：首跑后 `tests/test_lock_coverage.py` 报 **5 条名单外缺口**，都在 `tests/test_length_budget.py`：U5（`public_limits`）、R7（limits 接口）、R3-chat（聊天边界）、R9（群聊预算）、以及测试替身里一条 `raise RuntimeError("boom")`。处置：
  - **补四条专属变异 M14–M17**（§5 已加）：分别只改 `core/length_budget.py`、`web/routers/text.py`、`core/group_session.py`，打红 U5/R7/R9/R3-chat 各一条。`core/group_session.py` 随之进驱动 `TARGETS`（否则变异不被基线快照，还原不回来）。
  - **测试替身的失败注入改形态**：`test_stream_usage_fallback_accumulates_every_piece` 里 `raise RuntimeError("boom")` 是**就地构造**，按判别器定义算一条 `ast.Raise`，但它是替身的**机制**、任何合法变异都撞不到（与 `test_health_probe_targets.py` 的 `raise self._exc` 同类）。照文档已认可的形态改成存起来的异常 `_exc = RuntimeError("boom")` + `raise self._exc` —— 转抛不计判别器，机制不变。**不是删代码**。
- **两条测试写错，用变异逼出来的**（改测试，不改业务代码）：
  - **R3-chat 判据满足于错的守卫**：`test_text_upload_chat_boundary_by_char_count` 原先只写裸 `pytest.raises(ValueError)`。M17（`if n > CHAT_MAX_CHARS → if False`）下它**仍是绿的** —— 一堵「同字」正文会被 `ChatPreprocessor` 整行丢掉，清洗后的空文本那条 `chat_clean_empty` 也能让它通过。加 `match="超过聊天记录上限"` 后 M17 才红（实测红源正是 `Regex pattern did not match`）。
- **现测数**：`pytest tests/test_lock_coverage.py` **30 passed**；§6 的 Python 组 **265 passed**（120.63s）。

### 9.5 `.gitignore` 的 `lib/` 误伤前端 `src/lib/`（交付必需，2026-10-05）

- **问题**：仓库 `.gitignore` 那条 `lib/` 是 Python 打包规则，**按目录名匹配任意深度**，把 §3.3 要求新增的 `web/frontend/src/lib/textLimits.js`（上传上限接口的消费端 + 校验/提示纯函数）静默忽略了 —— `git status` 里看不到它，`git add <目录>` 也不会带上，交付就缺这一个文件，而 CI 不会报（它压根不在库里）。
- **修法**：在 `lib/` 段后加锚定例外 `!web/frontend/src/lib/`（只解禁前端这个目录；Python 的 `lib/` 忽略规则不受影响）。
- **验证**：`git status --ignored web/frontend/src/lib` → `??`（未忽略）；`git ls-files --others web/frontend/src/lib` → 仅 `web/frontend/src/lib/textLimits.js` 一条；目录里也只有这一个文件。
- **判定**：这是**交付必需**，不是顺手改 —— 少这一行，§3.3 的产物永远进不了库。


## 10. 自检表（对照 Shiyu 的标准，逐条）

| 标准 | 本 spec 落在哪 | 状态 |
|---|---|---|
| 1 事实在最新 main 上现读、带坐标、S0 复核 | §2 C1–C11（`ad3bc7e1`），§8 S0 | ✅ |
| 2 设计问题先给 2–3 方案再写 spec | 单一来源 A/B（已拍板）；计数方式 §1.1 A/B/C（已拍板） | ✅ |
| 3 测试一节固定写法 | §6 | ✅ |
| 4 spec 交 `.md` 文件 | 本文件 | ✅ |
| 5 审计逐文件清单 | 审计时执行（本段不适用于 spec） | — |
| 6 新问题不自行记账 | §9 | ✅ |
| 路径机制表（通道 × 上下文 × 测试） | §2.1 | ✅ |
| 真实规模算一遍、测试按比例复刻 | §2.2 最坏总量与计数耗时；§4.2 边界按 token 数复刻 | ✅ |
| 只在样本上实测的前提写明范围 | §2.2 约合字数「因文本而异」；C11 tokenizer 间接证据 + §7.1 核对 | ✅ |
| ① 出处对照表 | §2.3 | ✅ |
| ② 全量扫描原文 | 附录 A | ✅ |
| ③ 规模表 | §2.2 | ✅ |
| ④ 调用点矩阵逐格到测试名 | §4.2 | ✅ |
| 变异发出前先实跑 | §5（执行方实现后跑，未全红不推；Claude 审计结果） | ⏳ 实现阶段 |
| 经验 1 复用先找现成库 | §1.1：`tokenizers` 已在锁定依赖中，零新增；先搜代码库（C12）把三套现有估算统一，不新增第四套 | ✅ |
| 经验 2 替换原生控件先列行为 | 不替换控件，只用 `disabled` 属性 | 不适用 |
| 经验 3 行为照抄权威来源 | 超窗措辞出自官方仓库讨论；窗口出自官方模型卡 | ✅ |
| 经验 4 事实全量现查 | 附录 A 扫描命令与原始输出 | ✅ |
| 经验 5 变异按矩阵、先实跑 | §5 每条对应矩阵格 | ⏳ 实现阶段 |
| 经验 6 能自动验的不手动 | 前端用 vitest 断言，无需手看 | ✅ |
| 经验 7 交接可核对 | §8 `Test-Path` + 提交号；v2 首行标明取代 v1 | ✅ |
| 经验 8 起环境前查冲突 | §6 首行 55432 检查、项目名、tmpfs | ✅ |
| 不打补丁、隔离、抽象、复用 | 一个模块管所有上限；两处重复判断收一；前端两处体积常量收一；死代码 `charCountClass` 随旧上限删除 | ✅ |
| skill 用上不过度 | 前端读过 frontend-design（只取可访问性下限）；执行方配两个：`@search-first`（实现前搜可复用的现成实现）、`@verification-before-completion`（报通过前附实际输出） | ✅ |

### 附录 A：全量扫描输出（`ad3bc7e1`）

```
$ git grep -n -I -E "_estimate_tokens|longctx_threshold|\* 0\.6|0\.6 tokens" -- core web adapters mcp_server scripts config.example.yaml
core/distiller.py:597:        self._longctx_threshold: int = int(distill_cfg.get("longctx_threshold", 900000))
core/distiller.py:1550:    def _estimate_tokens(text: str) -> int:
core/distiller.py:1551:        """Estimate token count: Chinese text ~0.6 tokens per char."""
core/distiller.py:1552:        return int(len(text) * 0.6)
core/distiller.py:2064:        estimated = self._estimate_tokens(text)
core/distiller.py:2065:        print(f"[distiller] Token estimate: ~{estimated} (threshold: {self._longctx_threshold})")
core/distiller.py:2066:        if estimated < self._longctx_threshold:
core/distiller.py:2249:        estimated = self._estimate_tokens(text)
core/distiller.py:2250:        print(f"[distiller] Token estimate: ~{estimated} (threshold: {self._longctx_threshold})")
core/distiller.py:2251:        if estimated < self._longctx_threshold:

$ git grep -n -I -E "1_000_000|2_000_000|1000000|2000000|100 ?万|200 ?万|150 ?万" -- core web/routers web/frontend/src adapters ":!*__tests__*"
core/text_manager.py:274:        max_chars = 2_000_000 if text_type == "chat" else 1_000_000
core/text_manager.py:276:            limit_text = "200 万" if text_type == "chat" else "100 万"
core/text_manager.py:399:        max_chars = 2_000_000 if text_type == "chat" else 1_000_000
core/text_manager.py:401:            limit_text = "200 万" if text_type == "chat" else "100 万"
web/frontend/src/components/AdminPanel.jsx:1139:    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M'
web/frontend/src/components/ApiConfigPanel.jsx:468:    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M'
web/frontend/src/components/TextPanel.jsx:61:  if (n <= 1000000) return 'chars-orange'
web/frontend/src/components/TextPanel.jsx:268:          {`支持 ${ALLOWED_EXT.join(' ')} · 小说上限 100 万字 · 聊天记录上限 200 万字 · 单文件最大 100MB`}
web/routers/auth.py:372:    code = f"{secrets.randbelow(1000000):06d}"
web/routers/text.py:156:                        raise HTTPException(413, "文件体积超过 30MB 上限（内容字数上限另为 100 万字，PDF/Word 因格式体积更大）")

$ git grep -n -I -E "MAX_FILE_SIZE|100MB|100 MB|30MB|30 MB" -- core web/routers web/frontend/src
web/frontend/src/components/TextPanel.jsx:47:    return '文件超过 100MB 上限'
web/frontend/src/components/TextPanel.jsx:268:          {`支持 ${ALLOWED_EXT.join(' ')} · 小说上限 100 万字 · 聊天记录上限 200 万字 · 单文件最大 100MB`}
web/frontend/src/store/useAppStore.js:686:    const MAX_SIZE = 100 * 1024 * 1024 // 100MB
web/frontend/src/store/useAppStore.js:688:      set({ error: `文件过大（${(file.size / 1024 / 1024).toFixed(1)}MB），最大支持 100MB` })
web/routers/text.py:108:MAX_FILE_SIZE = 30 * 1024 * 1024  # 30MB
web/routers/text.py:153:                    if total_size > MAX_FILE_SIZE:
web/routers/text.py:156:                        raise HTTPException(413, "文件体积超过 30MB 上限（内容字数上限另为 100 万字，PDF/Word 因格式体积更大）")

$ git grep -n -I -E "too_long|chars-red|chars-orange|chars-blue|chars-green" -- core web/routers web/frontend/src
core/text_failure.py:27:    "too_long": "文本超过 {limit_text} 字上限，请分卷上传",
core/text_manager.py:277:            raise ValueError(_MSG["too_long"].format(limit_text=limit_text))
core/text_manager.py:402:            raise ValueError(_MSG["too_long"].format(limit_text=limit_text))
web/frontend/src/components/TextPanel.jsx:59:  if (n <= 100000) return 'chars-green'
web/frontend/src/components/TextPanel.jsx:60:  if (n <= 500000) return 'chars-blue'
web/frontend/src/components/TextPanel.jsx:61:  if (n <= 1000000) return 'chars-orange'
web/frontend/src/components/TextPanel.jsx:62:  return 'chars-red'
web/frontend/src/styles/global.css:43:  --info: #3b82f6;   /* 信息蓝；暗色主题用 color-mix 提亮（对应原 .chars-blue #2e86c1/#60a5fa） */
web/frontend/src/styles/global.css:678:.theme-midnight .chars-green,
web/frontend/src/styles/global.css:679:.theme-galaxy .chars-green { color: color-mix(in srgb, var(--success) 82%, white); font-weight: 600; }
web/frontend/src/styles/global.css:680:.theme-midnight .chars-blue ,
web/frontend/src/styles/global.css:681:.theme-galaxy .chars-blue  { color: color-mix(in srgb, var(--info) 82%, white); font-weight: 600; }
web/frontend/src/styles/global.css:682:.theme-midnight .chars-orange,
web/frontend/src/styles/global.css:683:.theme-galaxy .chars-orange { color: color-mix(in srgb, var(--warning) 82%, white); font-weight: 600; }
web/frontend/src/styles/global.css:684:.theme-midnight .chars-red  ,
web/frontend/src/styles/global.css:685:.theme-galaxy .chars-red   { color: color-mix(in srgb, var(--danger) 82%, white); font-weight: 600; }
web/frontend/src/styles/global.css:2641:.chars-green  { color: var(--success); font-weight: 600; }
web/frontend/src/styles/global.css:2642:.chars-blue   { color: var(--info); font-weight: 600; }
web/frontend/src/styles/global.css:2643:.chars-orange { color: var(--warning); font-weight: 600; }
web/frontend/src/styles/global.css:2644:.chars-red    { color: var(--danger); font-weight: 600; }

$ git grep -l -I -E "100 ?万|1_000_000|too_long|_estimate_tokens|longctx_threshold" -- tests web/frontend/src
tests/eval/injection/upload_extra.py
tests/lock_coverage_gaps.py
tests/perf/e2e_otel.py
tests/perf/route_facts_mutations.py
tests/test_card_draft.py
tests/test_distill_resume.py
tests/test_distill_usage_accounting.py
tests/test_distiller_routing.py
tests/test_identify_failure_channels.py
tests/test_text_failure_messages.py
tests/test_usage_identity_context.py
web/frontend/src/components/TextPanel.jsx
```

### 附录 B：token 估算全量扫描（`ad3bc7e1`）

```
$ git grep -n -I -i -E "tiktoken|from tokenizers|Tokenizer\.from|count_tokens|token_count|estimate_tokens|_CHARS_PER_TOKEN|estimate_usage_from_chars" -- core web adapters mcp_server scripts storage tests
adapters/llm_adapter.py:25:from core.utils import estimate_usage_from_chars  # 字符→token 估算的唯一出口
adapters/llm_adapter.py:1124:                usage = estimate_usage_from_chars(prompt_chars, completion_chars)
core/chat_engine.py:21:from core.context_engine import _count_tokens
core/chat_engine.py:386:            tok = _count_tokens(retrieval_block)
core/chat_engine.py:389:                while _count_tokens(retrieval_block) > budget and len(parts) > 1:
core/chat_engine.py:393:                if _count_tokens(retrieval_block) > budget:
core/chat_engine.py:522:                _count_tokens(user_msg.get("content", ""))
core/chat_engine.py:523:                + _count_tokens(asst_msg.get("content", ""))
core/context_engine.py:28:def _count_tokens(text: str) -> int:
core/context_engine.py:281:        budget -= _count_tokens(card_core) + _count_tokens(rules_block)
core/context_engine.py:317:            allowed = min(_count_tokens(content), max_tok, budget)
core/context_engine.py:324:        total_used = _count_tokens(result)
core/distiller.py:55:from core.utils import aggregate_usage, estimate_usage_from_chars, try_record_usage
core/distiller.py:825:                usage_action, estimate_usage_from_chars(prompt_chars, len(text)),
core/distiller.py:877:                self._try_record_usage(action, estimate_usage_from_chars(
core/distiller.py:882:            usage = estimate_usage_from_chars(
core/distiller.py:1550:    def _estimate_tokens(text: str) -> int:
core/distiller.py:1900:                usage = estimate_usage_from_chars(len(system) + len(user))
core/distiller.py:2064:        estimated = self._estimate_tokens(text)
core/distiller.py:2166:            compress_usage = estimate_usage_from_chars(len(compress_system) + len(compress_user))
core/distiller.py:2249:        estimated = self._estimate_tokens(text)
core/group_session.py:11:from core.context_engine import _count_tokens
core/group_session.py:118:            total = sum(_count_tokens(m["content"]) for m in messages)
core/utils.py:13:_CHARS_PER_TOKEN = 1.5
core/utils.py:16:def estimate_usage_from_chars(prompt_chars: int, completion_chars: int = 0) -> dict:
core/utils.py:25:        "prompt_tokens": int(prompt_chars / _CHARS_PER_TOKEN),
core/utils.py:26:        "completion_tokens": int(completion_chars / _CHARS_PER_TOKEN),
scripts/run_agent_eval.py:43:from core.context_engine import _count_tokens
scripts/run_agent_eval.py:102:            self.last_sp_tokens = _count_tokens(result)
scripts/run_agent_eval.py:116:        _count_tokens(m.get("content", ""))
scripts/run_agent_eval.py:418:                sp_tokens=_count_tokens(legacy_sp),
scripts/test_distill.py:21:    token_count = 0
scripts/test_distill.py:53:            token_count += len(data.get("token", ""))
scripts/test_distill.py:57:    print(f"Total: {elapsed:.1f}s, Chunks: {chunk_events}, Compressions: {compression_count}, Tokens: {token_count}, Errors: {len(err
tests/test_distill_usage_accounting.py:37:from core.utils import estimate_usage_from_chars, try_record_usage
tests/test_distill_usage_accounting.py:245:        assert payload == estimate_usage_from_chars(
tests/test_distill_usage_accounting.py:259:        变异：把非流式截断支那条 `estimate_usage_from_chars(...)` 退回 `usage=None`
tests/test_distill_usage_accounting.py:274:        assert payload == estimate_usage_from_chars(
tests/test_distill_usage_accounting.py:301:        assert payload == estimate_usage_from_chars(len(SYSTEM) + len(USER)), (
tests/test_distiller_routing.py:272:        """~4w token text → _estimate_tokens < 150000."""
tests/test_distiller_routing.py:274:        assert Distiller._estimate_tokens(text) < 150000
tests/test_distiller_routing.py:277:        """~20w token text → _estimate_tokens >= 150000."""
tests/test_distiller_routing.py:279:        assert Distiller._estimate_tokens(text) >= 150000
tests/test_distiller_routing.py:282:        assert Distiller._estimate_tokens("") == 0
tests/test_distiller_routing.py:287:        tokens = Distiller._estimate_tokens(text)
tests/test_distiller_routing.py:363:        with patch.object(Distiller, "_estimate_tokens", return_value=519_689):
tests/test_distiller_routing.py:367:        with patch.object(Distiller, "_estimate_tokens", return_value=950_000):
tests/test_distiller_routing.py:392:        with patch.object(Distiller, "_estimate_tokens", return_value=1_000):
tests/test_distiller_routing.py:416:        with patch.object(Distiller, "_estimate_tokens", return_value=1_000):
```
