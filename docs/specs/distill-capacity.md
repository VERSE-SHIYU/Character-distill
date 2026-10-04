# distill-capacity：小说上传上限由一次读完阈值推导（单一来源）

> 基线：main @ `ad3bc7e1`（PR #110 合并后）。分支：`feat/distill-capacity`。
> 本文件先于实现提交，供 Shiyu 审阅；实现、测试、变异预跑完成后追加到 §8，补充写进 §9。

## 0. 目的

1. 小说类文本上传上限从 100 万字扩到 **1,249,999 字**，覆盖 100–125 万字的长篇（例：120.1 万字的网文）。
2. 上限内的小说**一定**走一次读完路径、**一定**不超出模型窗口。
3. 「上限」「阈值」「估算系数」「文件体积上限」各只有一个定义处，前后端共用，以后改一处即全改。
4. 万一超窗，用户看到「文本过长」而不是「服务暂时不可用，请稍后重试」（重试必败）。

## 1. 已拍板（2026-10-04）

| # | 决定 |
|---|---|
| D1 | 方案 A：新建独立模块 `core/length_budget.py` 集中定义系数、阈值、估算函数、各文本类型的上限与文件体积上限；`Distiller`、`TextManager`、上传路由、前端都只从它取 |
| D2 | 去掉配置文件对 `longctx_threshold` 的覆盖（生产两台均无 `config.yaml`，`config.example.yaml` 无此键 —— 该能力无人使用，却是上限与阈值可能对不上的唯一入口） |
| D3 | 一次读完阈值仍为 90 万 tokens，本段不动（降不降放到证据注入那一步，V4.1 无 1M 档长上下文评测可依） |
| D4 | 聊天记录上限仍为 200 万字（已有角色过滤，不动） |
| D5 | 估算系数 0.6 → 0.72（依据见 §2.3） |

## 2. 已查实约束（坐标均为 `ad3bc7e1`；S0 由执行方逐条复核，任一不成立即停下报告）

C1. 阈值：`core/distiller.py:597` 从配置 `distill.longctx_threshold` 读，默认 900000；比较为严格小于（`:2066`、`:2251`）。
C2. 估算：`core/distiller.py:1550-1552` `_estimate_tokens = int(len(text) * 0.6)`；只被 `:2064`、`:2249` 两处用来选路径，不进提示词。
C3. 上传上限两处重复：`core/text_manager.py:274-277`（文件上传）与 `:399-402`（文本上传），`chat` 200 万、其余 100 万；文案模板 `core/text_failure.py:27` `"文本超过 {limit_text} 字上限，请分卷上传"`。
C4. 文件体积：后端 `web/routers/text.py:108` `MAX_FILE_SIZE = 30MB`，413 文案 `:156` 写死「100 万字」；前端写成 100MB，且有两处各自定义：`web/frontend/src/components/TextPanel.jsx:47`（`MAX_BYTES`）、`web/frontend/src/store/useAppStore.js:686-688`（`MAX_SIZE`）。前端放行 30–100MB 的文件后由后端 413 拒绝。
C5. 前端字数分档写死到 100 万（`TextPanel.jsx:59-62`），上传提示写死「小说上限 100 万字 · 聊天记录上限 200 万字 · 单文件最大 100MB」（`:268`）。前端目前没有从后端取任何上限的接口。
C6. 生产配置：两台服务器（SZ 47.107.42.111、SG 43.134.55.201）容器内均**无** `/app/config.yaml`，只有 `config.example.yaml`（2271B），两者 `grep longctx` 零命中 → 生产生效值为代码默认 900000（Shiyu 2026-10-04 实查）。
C7. 思考：`adapters/llm_adapter.py:414` DeepSeek 方言统一传 `thinking: {type: disabled}`，推理不占窗口。
C8. 一次读完的输出上限 `LONG_OUTPUT_MAX_TOKENS = 16384`（`core/distiller.py:534`）；除原文外的提示词（`_longcontext_prompt` 的 system + user 结构）用官方离线 tokenizer 实测 **4,807 tokens**。
C9. 超窗现状：400 判为确定性失败、不重试（`llm_adapter.py:242-246`）；上屏文案唯一取值处 `_upstream_user_message`（`:520-531`）只按状态码查 `_UPSTREAM_USER_MESSAGES`（`:486`，无 400）→ 落通用文案「服务暂时不可用，请稍后重试」。
C10. 人物识别不受系数影响：按段落切片逐片调用，调用数 = 片数 + 5；红楼梦 866,149 字切 242 片、识别一次约 4 元 38 秒（`docs/specs/distill-longbook-blueprint.md:56`、`:306`）；按片数线性外推，125 万字约 354 次、约 5.8 元。每本书只识别一次（按文本与版本缓存）。

### 2.1 路径机制表（通道 × 执行上下文 × 守它的测试）

本段只改「数字从哪里来」与「超窗时说什么」，不改任何线程、协程、重试、计时；新模块全是纯函数和常量。

| 通道 | 用到的量 | 执行上下文 | 守它的测试（§4） |
|---|---|---|---|
| `POST /api/text/upload`（文件） | 体积上限、按类型的字数上限 | 请求协程 → `TextManager.upload_text_from_file` | T3、T4 |
| `POST /api/text/upload`（文本） | 按类型的字数上限 | 请求协程 → `TextManager.upload_text` | T3 |
| 蒸馏选路径 | 估算函数、阈值 | 后台任务线程 / `/run_stream` 生成器（`distill_incremental_stream`）与同步 `distill_incremental` | T2 |
| 上屏文案 | 超窗判定 | 任意调用线程（纯函数） | T5 |
| 前端校验与提示 | 三个上限 | 浏览器 | T6 |

### 2.2 规模表

| 量 | 值 | 来源 / 推导 |
|---|---|---|
| 窗口（输入 + 输出） | 1,048,576 | NVIDIA 官方 V4.1-Flash 模型卡；V4.1 技术报告：最长一百万 tokens |
| 阈值 | 900,000 | 代码默认（C1、C6），本段不动 |
| 系数 | 0.72 | 官方离线 tokenizer 实测：小说 0.680 / 0.699 / 0.701，聊天行格式 0.704–0.717；取上沿 |
| 小说上限 | 1,249,999 字 | `ceil(阈值 ÷ 系数) − 1`，保证 `estimate(上限) < 阈值` 且 `estimate(上限 + 1) ≥ 阈值` |
| 最坏总量 | ≈ 921,191 | 900,000 + 4,807（C8）+ 16,384 → 余量约 12% |
| 聊天上限 | 2,000,000 字 | 产品上限，不由阈值推导（D4）；估算发生在角色过滤后 |
| 文件体积 | 30MB | 后端现值（C4）；125 万汉字 UTF-8 约 3.75MB |
| 人物识别 | 片数 + 5 次调用 | C10 |

### 2.3 出处对照表

| 依据 | 条目 | 对应 |
|---|---|---|
| DeepSeek-V4.1-Flash 技术报告（arXiv 2609.19969） | 支持最长一百万 tokens 上下文；长上下文评测只有 LongBench-V2 | §2.2 窗口、D3 |
| NVIDIA 官方模型卡（build.nvidia.com/deepseek-ai/deepseek-v4.1-flash；docs.api.nvidia.com NIM 参考） | 输入 + 输出合计 1,048,576；词表大小 129,280 | §2.2 窗口、D5 |
| DeepSeek-V4 技术报告（arXiv 2606.19348）§4.1 | 在 V3 tokenizer 上只加少量特殊 token，词表仍 128K | D5（与 V4.1 词表大小一致 → 同一 tokenizer 的间接证据） |
| DeepSeek API 文档：Chat Completions、Thinking Mode | `deepseek-flash` 的 `thinking.type` 可为 `disabled`（不思考） | C7 |
| DeepSeek 官方离线 tokenizer（API 文档「Token 用量」所附） | 实测比例 | §2.2 系数 |
| deepseek-harness 讨论 #3399（官方仓库） | 生产超窗报文为 HTTP 400、`"Input token exceed the limit"`、`code: quota_limit_reached`；另有 OpenAI 兼容报文 `"maximum context length"` | §3.4 超窗判定 |

**已知局限**：V4.1 沿用 V3 tokenizer 只有间接证据（词表大小一致 + Engram 沿用原设计），官方未逐字写明；对冲见 §7。

## 3. 设计

### 3.1 新模块 `core/length_budget.py`（纯常量与纯函数，无 IO）

```python
TOKENS_PER_CHAR = 0.72                 # D5
LONGCTX_THRESHOLD_TOKENS = 900_000     # D2/D3：唯一定义处
CHAT_MAX_CHARS = 2_000_000             # D4
MAX_FILE_BYTES = 30 * 1024 * 1024      # C4

def estimate_tokens(text: str) -> int: ...          # int(len(text) * TOKENS_PER_CHAR)
def one_pass_max_chars() -> int: ...                 # 由阈值与系数推导：满足 estimate(n) < 阈值 的最大 n
def max_chars(text_type: str) -> int: ...            # chat → CHAT_MAX_CHARS；其余 → one_pass_max_chars()
def limit_label(text_type: str) -> str: ...          # 「125 万」「200 万」，由 max_chars 生成，不另写
def public_limits() -> dict: ...                     # 给前端：{story_max_chars, chat_max_chars, max_file_bytes}
```

`one_pass_max_chars` 用整数运算推导并在模块导入时自检一次（`estimate(n) < 阈值 ≤ estimate(n+1)`），不依赖浮点除法的边界。

### 3.2 后端接线（去重，不留旧值）

- `Distiller`：`_estimate_tokens` 删除，两处调用改用 `length_budget.estimate_tokens`；`self._longctx_threshold` 改为取 `LONGCTX_THRESHOLD_TOKENS`（保留属性名，现有测试会调小它来走分片）；删除读配置的那一行及过时注释（D2）。
- `TextManager`：`:274-277` 与 `:399-402` 两段重复的判断收成一个私有方法 `_check_length(parsed, text_type)`，两条上传路径都调它；数值与文案取自 `length_budget`。
- `web/routers/text.py`：`MAX_FILE_SIZE` 改为引用 `MAX_FILE_BYTES`；413 文案由 `MAX_FILE_BYTES` 与 `limit_label` 生成，不再写死数字。
- 新增 `GET /api/text/limits` → `public_limits()`，挂在现有 `/api/text` 路由下，沿用该路由的登录依赖。

### 3.3 前端（单一来源 = 后端接口）

- 新增 `web/frontend/src/lib/textLimits.js`：取 `/api/text/limits` 并缓存；接口失败时**不放行、不猜数**，显示「暂时无法获取上传限制」并禁用上传按钮（不在前端再写一份数字作兜底，否则又是两个来源）。
- `TextPanel.jsx`：`validateFile` 的体积上限、字数分档（绿/蓝/橙到小说上限，超出为红）、上传提示文案都从 `textLimits` 取；删除 `MAX_BYTES`。
- `useAppStore.js`：`uploadText` 删除自带的 `MAX_SIZE`，体积校验只在 `TextPanel` 的 `validateFile` 做一次（同一件事一个地方）。

选这个而不是「前端常量 + 跨语言对照测试」：后者仍是两份数，只是被测试盯着；接口方案只有一份。

### 3.4 超窗上屏（`adapters/llm_adapter.py`）

- 新增纯函数 `_is_context_overflow(exc) -> bool`：状态码 400，且报文命中 §2.3 所列已知措辞（`Input token exceed the limit`、`maximum context length`、`exceeds model context limit`）之一。措辞表集中一处、附出处。
- `_upstream_user_message` 先判它，命中则返回「文本过长，超出模型一次能处理的长度，请缩短后再试」；其余维持原逻辑。重试行为不变（400 本就确定性失败）。
- 正常情况下这条走不到（§2.2 余量 12%）；它是系数估低时的兜底，保证用户不被引导去重试。

## 4. 测试计划（先红后绿）

| 编号 | 断言 | 文件 |
|---|---|---|
| T1 | `one_pass_max_chars()` == 1,249,999；`estimate(n) < 阈值 ≤ estimate(n+1)`；`max_chars("chat")` == 2,000,000；`limit_label` 由 `max_chars` 生成 | `tests/test_length_budget.py`（新） |
| T2 | 选路径：长度 = 上限 → 一次读完；= 上限 + 1 → 分片；两条入口（同步 `distill_incremental`、流式 `distill_incremental_stream`）都用同一估算 | 同上，打桩照 `tests/test_distiller_routing.py` |
| T3 | 上传：两条路径各在「上限」通过、「上限 + 1」拒绝，文案含 `limit_label`；chat 同理 | 同上（TextManager，PG 库） |
| T4 | 413 文案由常量生成：不含写死的「100 万」，含 30MB 与当前小说上限 | 同上（路由） |
| T5 | `_upstream_user_message`：三种已知超窗报文 → 「文本过长」；其他 400 仍为 ""；401/402/429 不变 | `tests/test_error_user_facing.py`（追加） |
| T6 | 前端：`textLimits` 取接口；分档以接口给的上限为界；接口失败时禁用上传；`useAppStore` 不再自带体积常量 | `web/frontend/src/components/__tests__/`（vitest，追加） |
| S1 | 结构：`0.6`、`1_000_000`、`900000`、`30 * 1024 * 1024`、「100 万」在 `core/ web/routers/ web/frontend/src/` 中不再出现；`longctx_threshold` 不再被读取 | `tests/test_length_budget.py` |

## 5. 变异清单（实现后在 PG 上预跑，全部打红才推）

| 编号 | 变异 | 应红 |
|---|---|---|
| M1 | 系数改回 0.6 | T1、T2 |
| M2 | 上限推导少减 1（= 1,250,000） | T1、T2 |
| M3 | `TextManager._check_length` 用 `>=` 代替 `>` | T3 |
| M4 | 一条上传路径绕过 `_check_length` | T3 |
| M5 | `_upstream_user_message` 不判超窗 | T5 |
| M6 | 超窗判定不看状态码（任何含措辞的错误都命中） | T5 |
| M7 | 413 文案改回写死 | T4、S1 |
| M8 | 前端接口失败时放行上传 | T6 |

驱动照 `tests/perf/mutation_framework.py`，产物 `tests/perf/distill_capacity_red_lines.json`（`tests/test_lock_coverage.py` 要求）。

## 6. 本地命令（只跑受影响的文件；合并门是分支 CI）

```powershell
docker compose -f docker-compose.test.yml up -d --wait
python -m pytest -q tests/test_length_budget.py tests/test_error_user_facing.py tests/test_distiller_routing.py tests/test_text_failure_messages.py tests/test_card_draft.py tests/test_distill_resume.py tests/test_distill_usage_accounting.py tests/test_identify_failure_channels.py tests/test_usage_identity_context.py tests/test_lock_coverage.py
python tests/perf/distill_capacity_mutations.py
cd web/frontend; npm test
```

## 7. 上线后核对（线上监测，不是实验）

上线后第一次蒸馏 100 万字以上的书时，取接口返回的 `prompt_tokens` 与原文字数之比，记入 §9。比例 ≤ 0.72 即与本 spec 一致；超出则停下报告（余量按 §2.2 重算）。

## 8. 进度

- [ ] 实现（Claude，沙箱 PG）
- [ ] §4 测试先红后绿、§5 变异全红（Claude 预跑，贴结果）
- [ ] 执行方 S0 复核 C1–C10、复跑 §6
- [ ] 分支 CI 绿 → PR → Shiyu 合并

## 9. 补充

本段改动面内新发现的问题直接修并写进这里；需要拍板的停下报告，不自行记账。

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
