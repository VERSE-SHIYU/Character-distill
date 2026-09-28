# WP17 对话示例改为「按编号从原文选取」（2026-09-27，Shiyu 已拍板）

> 放到 worktree 的 `docs/specs/distill-verbatim-dialogue.md`。以后的补充写进本文件。
> **执行时机**：C 线合并 main 的 PR 合入之后再开工；在分支 `fix/acceptance-card-checks`（`b965f0c`，含验收脚本的逐字核对逻辑）之上继续做，先 `git merge origin/main`。S0 在**当时最新的 main** 上逐条复核下面的坐标（本稿基线 main `ae89f7d`，C 线合入会移动行号）。
> **执行方 skill**：`@search-first @tdd`。

## 目标
角色卡的对话示例一字不差地来自原文：代码抽取带编号的候选对话句，模型只按编号挑选，文字由代码从原文复制。

## 问题（实测证据）
刘姥姥验收（Flash、一次读完、`b965f0c`）：对话示例 3 组只命中 1 组；未命中的两组都是把**被他人插话打断的两段话**拼成一句，吞掉了中间说话人的台词。提示词已要求逐字引用，模型仍然拼接——提示词约束不能保证逐字。

## 已查实的约束（基线本分支 `9c02c2d`，比 main `ae89f7d` 多 10 行，来自 `e1ac099`/`b965f0c`；执行方 S0 已逐条复核，全部成立）
1. 对话示例由格式化片段生成：维度 I 在 `core/distiller.py:161`（归 G3），模板行 `:210`；字段 `core/schema.py:81`；`FORMAT_GROUPS["G3"]` 在 `core/schema.py:100`；后置字段 `POST_FORMAT_FIELDS = ("tags", "awakening_message")` 在 `core/schema.py:109`。
2. 一次读完与分组格式化共用同一批片段：`format_prompt_after`（`core/distiller.py:252`），一次读完经 `_longcontext_prompt`（`:1529`），分组格式化在 `distill_incremental_stream`（`:2041` 起）。
3. 路由的后置步骤：`_auto_tag`（`web/routers/distill.py:511`）→ 保存卡片（`:520-541`）→ 苏醒台词（`:552`，保存后 `update_card`）。
4. 适配器：一个实例一个客户端，按构造时的 `base_url` 建（`adapters/llm_adapter.py:696`）；关闭思考的参数统一由 `_request_options`（`:748`）注入；出站守卫 `check_outbound_guard`（`:315`）；方言识别 `_detect_dialect`（`:383`）。
5. **现有 `chat_with_tools`（`:1011`）用的是「决策轮」预算：2 次、总时限 6 秒**，前提是「短小的路由决策」。本步骤输入最多约 7 万 token，读入就要数秒，**不能复用它**（见路径机制清单）。
6. **DeepSeek 官方文档（2026-09-28 核对）**——「模型 & 价格」页：`deepseek-flash`（V4.1-Flash）与 `deepseek-v4-pro`（0813）都支持 Tool Calls 与 JSON Output；思考模式为**默认**，须显式关闭；上下文 1M、最大输出 384K；Flash 输入（未命中缓存）空闲时段 1 元 / 百万 token、输出 4 元 / 百万 token，空闲时段为北京时间工作日 9–12、14–18 以外及周末节假日全天。「更新日志」最新一条为 2026-09-10（V4.1-Flash 发布；V4-Pro 在 9 月 14 日后继续提供）。「Tool Calls」页（strict 模式，Beta）：须用 `base_url=https://api.deepseek.com/beta`、每个 function 设 `strict: true`，服务端校验 Schema；支持 object / string / number / integer / boolean / array / enum / anyOf；**integer 支持 `minimum` / `maximum`**；array 不支持 `minItems` / `maxItems`；enum 未写数量上限；思考与非思考模式均可用。Chat Completions API 页：非 strict 时模型可能生成不合法 JSON 或编造参数，须在代码里校验。「思考模式」页（2026-09-28 补核）：OpenAI 格式下关闭思考**只能**用 `extra_body={"thinking":{"type":"disabled"}}`；思考模式下带 tools 的请求必须回传 `reasoning_content`，否则 400。故 `/beta` 客户端必须与现客户端一样注入 `_request_options` 的关闭思考参数——「strict 与关闭思考」那条测试就是锁它，不能省。
7. 逐字核对的现成逻辑在验收脚本里（分支 `fix/acceptance-card-checks` @ `b965f0c` 的 `tests/perf/longbook_acceptance.py`：拆开带说话人标签的多行对话、去掉（）动作说明、在原文里逐字查找）。产品代码不能 import 测试目录，所以要把它**搬进 `core/`**，由验收脚本反过来 import，只留一份。
8. 原文格式（公开的 120 回红楼梦文本实测，版本与本地不同、格式一致）：对话一律用 `“……”`，全书 11,742 处；86% 前面紧跟「某某（笑）道：」类引导语；引导语含「刘姥姥」142 句、含「宝玉」1,431 句；约 15% 的引导语里有两个人名（如「凤姐忙和刘姥姥摆手道：」，说话的是凤姐）。

## 方案对比与拍板（Shiyu 已选 A）
| 方案 | 做法 | 依据 | 取舍 |
|---|---|---|---|
| **A（已选）按编号选取** | 代码抽取候选句并编号；模型用 strict 工具调用只返回编号（integer，`minimum`=1、`maximum`=N）；代码按编号从原文复制 | DeepSeek 官方 strict 模式（约束 6）；Anthropic 官方 Citations 文档：被引文字由系统从文档提取而非模型生成，故保证指向真实原文，且比纯提示词做法更准 | 文字逐字由构造保证；说话人归属靠模型结合上下文判断（约束 7 的 15%） |
| B 生成后核对 + 让模型重写 | 核对不符的交回重写 | 多层校验 | 只能减少，不能保证 |

拍板的两条取舍：① 非 DeepSeek 接口不用 strict，退回普通工具调用，由代码校验编号；② 说话人是否正确，由模型在挑选时结合上下文确认。

## 路径机制清单（新增的「挑选对话」调用）
| 机制 | 计时/计数起点、前提 | 本步骤怎么处理 |
|---|---|---|
| 决策轮预算 2 次 / 6 秒（`chat_with_tools`） | 前提：输入短 | **不用**：新方法用生成轮预算（单次 45 秒、总 60 秒，`:105`、`:116`） |
| 关闭思考（`_request_options`） | 按方言注入 | 新客户端同样注入，写测试锁住 |
| 出站守卫（`:315`） | 按 `base_url` 判定 | `/beta` 与正式地址同主机；S0 核对守卫对 `/beta` 放行 |
| 自适应并发闸（WP14） | 只接 Map | 本步骤单次调用，不接闸 |
| 截断检测（WP11） | 看 `finish_reason` | 照常生效；本步骤输出只有编号，远低于上限 |
| 记账（`_try_record_usage`） | 每次调用一行 | 新增 action `distill_dialogue`，复用现有记账出口 |
| 一次读完无断点 | 失败即整任务报错 | 挑选失败 → 任务报错（与其他步骤同口径）；重跑时一次读完的前缀命中缓存 |

## 真实规模核算
- 候选文本（每句带上一句作为对方台词和引导语）：刘姥姥约 1.16 万字（约 0.7 万 token），**宝玉约 11.4 万字（约 6.8 万 token）**，按实测 1.67 字 / token。
- 读入 6.8 万 token 约数秒（LMSYS 实测 100 万 token 读入 43.7 秒）；输出只有几个编号，约 100 token 以内。**远低于单次 45 秒**；按决策轮 6 秒则**必然超时**——这就是不能复用 `chat_with_tools` 的原因，测试要按比例复刻这一点。
- 花费（Flash，按官方页 1 元 / 百万输入 token）：宝玉约 0.07 元，刘姥姥约 0.01 元。

## 步骤（按依赖顺序，每步独立 commit）
1. **[schema + 提示词] 对话示例改为后置字段**：从格式化片段中删去维度 I 与模板里的 `dialogue_examples`（一次读完与分组共用片段，改一处两边生效）；`FORMAT_GROUPS["G3"]` 去掉它，`POST_FORMAT_FIELDS` 加上它。
2. **[core] 新建 `core/quotes.py`（纯函数，不 import 项目模块，同 `roster_aggregate` 的做法），只放「原文引语」这一件事**：
   - `extract_candidates(text, names)`：输入原文与角色名 ∪ 别名（复用 `aliases_for`），输出候选列表，每项含编号、引导语、原句、上一句（引导语 + 原句）；
   - `verbatim_in(source, quote)`：从验收脚本搬来的逐字核对（约束 7），并在这里加上「按『……』分段、逐段核对」；验收脚本改为 import 它。
3. **[adapter] 新方法 `select_by_schema`**：DeepSeek 方言走单独的 `/beta` 客户端（按需创建、缓存在实例上）+ `strict: true` + 强制调用该工具；其他方言走现有客户端、同一工具不带 strict。用生成轮预算、注入关闭思考、经出站守卫；返回解析后的参数。**重试循环与 `chat_with_tools` 共用**：把两者相同的「发请求 → 取工具参数 → 按预算重试」抽成一个私有函数，预算作为参数传入，不写第二份循环。
4. **[distiller + 路由] 后置步骤**：在 `_auto_tag`（`web/routers/distill.py:511`）之前调用 distiller 的一个方法完成「抽取候选 → 挑选 → 按编号复制成 `对方名：…\n角色名：…`」。Schema 只有一个字段：整数数组（`minimum` 1、`maximum` N）。**代码校验编号在 1..N 内、去重、最多取 3 组**（array 不支持 `maxItems`）。对方名由代码从上一句的引导语里取：恰好含一个名单里的名字就用它，否则写「对方」——不让模型生成任何文字。挑选失败按任务失败处理。
5. **[验收脚本]** 删掉自带的逐字核对，改为 import `core/quotes.py` 的 `verbatim_in`。

## 测试
本地只跑受影响的测试文件，再加一次 `npm test`；库用 Docker 起的 PG；合并门是分支 CI；**合并只做 git 操作，不跑测试、不等 CI**。

需要的测试（都用假服务，不花钱）：
- `core/quotes.py`：真实形态段落（含「凤姐忙和刘姥姥摆手道：」这种两个人名的引导语、带（）动作说明、带「……」的节选）→ 候选与逐字核对结果正确；**变异：不按省略号分段 → 变红**。
- 挑选预算（按比例复刻「输入大、决策轮 6 秒必超时」）：假服务延迟大于缩放后的决策时限、小于生成轮单次时限 → `select_by_schema` 成功；**变异：改用决策轮预算 → 超时变红**。
- strict 与关闭思考：DeepSeek 方言时请求发往 `/beta` 且带 `strict: true`，非 DeepSeek 不带；两种都带关闭思考的参数。
- 编号校验与逐字：模型返回越界或重复编号 → 被丢弃；拼进卡片的每句都能在原文逐字找到；**变异：去掉编号校验 / 改用模型返回的文字 → 变红**。

## 范围规矩
执行中新发现的问题，属于本段改动面的直接修；只有会撞车或需要 Shiyu 拍板时才停下报告，不自行记账。

## S0（执行方先做，只读）
1. 在当时最新的 main 上逐条复核「已查实的约束」1–5 的坐标；有一条不成立就停下报告。
2. 核对出站守卫对 `https://api.deepseek.com/beta` 是否放行（报守卫逻辑坐标）。
3. 报后置步骤里能否拿到原文与名单（`text_id` → 原文、`aliases_for`）的坐标。
报完停下等审计。

## 验收（之后一次真跑）
重跑刘姥姥（一次读完 + 挑选，约 0.7 元，前缀命中缓存则更少）；门槛：对话示例 3/3 逐字命中、找不到的引文 = 0，其余 5 项维持通过。
