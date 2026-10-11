# 对话示例：片段带上一句之前 10 句，候选上限 40 条（dialogue-context-window）

- 日期：2026-10-11。作者：Claude（蒸馏对话框）。方案由 Tracy 2026-10-11 13:35 同意（「那我们就开始做吧」）。
- 版本：第 2 版（2026-10-11 下午）。Tracy 14:10 要求按「不打补丁、隔离、复用、高内聚低耦合」核对代码，核对出 8 处，全部改掉，见 §10。第 1 版（sha256 前 16 位 `591424a2ea68ecfc`）和它配的两份改动文件、目标检查作废。
- 前置 spec：`docs/specs/dialogue-pairing.md`（#129）、`docs/specs/dialogue-fallback.md`（#130）、`docs/specs/examples-pending.md`（#131、#132）。
- 取代：`docs/specs/distill-verbatim-dialogue.md` 的「补充 1-第 2 步」和「补充 1 · S0 裁决」里片段起点的规则（往前 60 字、不越过再上一句结尾）；`docs/specs/dialogue-pairing.md` §4.2「片段起点的算法不变」所指的同一条规则。两份旧 spec 末尾各加一行指向本文件（在段 2 的改动文件里）。
- 基线：`main` @ `6a10ab7`。下面所有坐标都是这个提交上的。
- 参考实现：两份改动文件 `proto-seg1-candidate-cap.diff`、`proto-seg2-context-window.diff`，Claude 在沙箱里从 `6a10ab7` 做出来的，没有推送。目标检查 `tests/test_dialogue_context_goal.py` 随本 spec 一起交付。
- 分支：`fix/dialogue-context-window`。一个 PR，两段，按 段 1 → 段 2 的顺序做，每段过了目标检查再做下一段。

## 0. 要解决什么

卡上的对话示例是「对方一句 + 角色一句」。对方那一句是谁说的，由模型读一段原文片段后填。实测里它会填错：主角重蒸 10 组错 2 组，都是把角色自己说的话标成「我」。

根因：交给模型的片段太短。上一句的引导语写的是「他说」「自己摇头说」时，「他」是谁写在更前面，而片段从上一句往前最多 60 字、且不越过再前一个引号，模型看不到。

修法：片段改成从上一句往前带 10 句连续原文。片段变长后，长书主角的候选全交出去会超过模型一次能读的量，所以同时给候选加上限 40 条。

不改：怎么找候选、怎么配对、说话人选项、6 个格子、提示词、保底出卡、卡上其他字段、前端、数据库。

## 1. 目标检查

文件 `tests/test_dialogue_context_goal.py`，11 条，走真实代码（`Distiller.dialogue_candidates`、`pick_dialogue_examples`、`core.quotes.extract_candidates`）。模型用 `tests/test_dialogue_pairing_goal.py` 里已有的假适配器 `_PickLLM` 和 `_pick`，不另抄一份。W1–W3、W6、W7 的原文是鲁迅公版原文，一字未改。

| 编号 | 检查什么 | `main` 上 | 段 1 后 | 段 2 后 |
|---|---|---|---|---|
| C1 | 100 条候选直接递给挑选那一步（不经 `dialogue_candidates`），交给模型 40 条；编号上限 40；首尾两条都在 | 红 | 绿 | 绿 |
| C2 | 40 条是全书均匀取的，相邻隔 2–3 条 | 红 | 绿 | 绿 |
| C3 | 模型按编号挑中的，取回来的就是它看到的那一条 | 绿 | 绿 | 绿 |
| C4 | 不多于 40 条的原样交出去 | 绿 | 绿 | 绿 |
| W1 | 《孔乙己》上一句「他说，」：片段里它之前有「孔乙己」，且这段片段进了发给模型的提示 | 红 | 红 | 绿 |
| W2 | 《孔乙己》上一句「自己摇头说，」：片段里它之前有「孔乙己」 | 红 | 红 | 绿 |
| W3 | 《故乡》上一句「他的态度终于恭敬起来了，分明的叫道：」：片段里它之前有「闰土」 | 红 | 红 | 绿 |
| W4 | 上一句之前恰好带 10 句 | 红 | 红 | 绿 |
| W5 | 往前数到的位置落在一句台词中间时，退到那句台词的引导语开头 | 红 | 红 | 绿 |
| W6 | 候选本身没变（本句、上一句） | 绿 | 绿 | 绿 |
| W7 | 片段仍是原文的连续子串，仍到本句署名为止 | 绿 | 绿 | 绿 |
| W8 | 片段有界：不用 `。！？` 断句的长文本，上一句之前最多带 2000 字（§12 加的） | 绿 | 绿 | 绿 |

合计：`main` 上 7 红 4 绿；段 1 后 5 红 6 绿；段 2 后 11 绿。Claude 在干净的 `6a10ab7` 上套第 2 版的两份改动文件实跑过，数字如上。

§12 加了 W8 之后共 12 条：`main` 上 7 红 5 绿；最终 12 绿。W8 在 `main` 上是绿的（那时最多带 60 字），在没有封顶的 `bd6a553` 上是红的。

W1–W3 对应的是实测里出过错的三条候选（第四条「闰土·他说：」在 `main` 上片段里已经有名字，不设检查）。

## 2. 已查实的约束

每条都是这一轮读过的行，`@ 6a10ab7`。

| # | 事实 | 坐标和原文 |
|---|---|---|
| 1 | 片段起点现在是「上一句引导语往前 60 字，且不越过再前一个引号」 | `core/quotes.py:166`：`_CONTEXT_BACK = 60`；`:246-247`：`start = max(prev_start - _CONTEXT_BACK, quotes[i - 2].end() if i >= 2 else 0)` |
| 2 | 句末的判定已经有一份 | `core/quotes.py:144`：`_NARRATION_SENTENCE = re.compile(f"[{_SENTENCE_END}]")`，`_SENTENCE_END = "。！？"`。段 2 复用它，不另写 |
| 3 | 某个引号的引导语从哪开始，已经有函数给出 | `core/quotes.py:254`：`_lead_before(text, quotes, i, start)` 返回 `(引导语, 它在原文里的起点)`。段 2 复用它 |
| 4 | 抽候选的函数在生产代码里只有一个调用点 | `core/distiller.py:1862`：`candidates = extract_candidates(content, [name, *aliases])`，在 `dialogue_candidates` 里 |
| 4a | 候选交给模型有两个入口，都经过 `_pick_dialogue_examples` | 对外的 `pick_dialogue_examples`，`core/distiller.py:1877`：`return [text for text, _ in self._pick_dialogue_examples(candidates, name, others)]`；蒸馏用的 `attach_dialogue_examples`，`:1993`：`picked = self._pick_dialogue_examples(candidates, name, others)`。前一个入口的候选是调用方递进来的，不经过 `dialogue_candidates`。所以上限放在 `_pick_dialogue_examples` 里才是两个入口都管得到的唯一一处 |
| 5 | `dialogue_candidates` 只被贴示例那一步调用；「重新找一次」走的也是这一步 | `core/distiller.py:1992`；`core/examples_pending.py:85`：`distiller.attach_dialogue_examples` |
| 6 | 候选现在没有数量上限，抽到多少交多少 | `core/distiller.py:1867`：`return candidates`；`:1946`：`render_candidates(candidates)` 整批进提示 |
| 6a | 片段只有一个读它的地方 | 全量扫描 `grep -rn "\.context\b" --include=*.py core web adapters storage scripts`，除 `core/telemetry.py` 里 3 处 `opentelemetry.context`（无关）外只有 `core/quotes.py:296`。片段变长只影响交给模型的那段文字，不进卡、不进库 |
| 7 | 给模型看的编号、schema 里编号的上限、按编号取回原文，用的是同一个 `n` | `core/quotes.py:296`：`f"[{c.n}] 片段：…"`；`core/distiller.py:1915`：`pick_slot_properties(len(candidates), options)`；`:1951`：`by_n = {c.n: c for c in candidates}`。所以抽样后必须从 1 重新编号 |
| 7a | 编号现在是抽候选时顺手写的 | `core/quotes.py:248`：`out.append(Candidate(n=len(out) + 1, …`。抽样后还要再编一次，所以段 1 把「怎么编号」收成一个函数，两处都调它 |
| 8 | 这次调用单次时限 45 秒、总时限 60 秒 | `adapters/llm_adapter.py:1316`（说明）、`:134`、`:145` |
| 9 | DeepSeek 的调用关着思考 | `adapters/llm_adapter.py:414`：`_DIALECT_DEEPSEEK: {"thinking": {"type": "disabled"}}` |
| 10 | 有一条现有测试钉着旧规则 | `tests/test_quotes.py:145` 起的 `test_the_fragment_keeps_the_previous_line_and_caps_narration_before_it_at_sixty`，断言「往前 60 字」和「再上一句不进片段」。规则变了，这条要改写（见 §4 段 2） |
| 10a | 两份旧 spec 写着旧规则 | `docs/specs/distill-verbatim-dialogue.md:158`：「从『上一句开头往前 60 字』与『再上一句结尾』两者中较后的位置起」；`:247`：「片段定义按第 2 步字面执行，含『上一句开头往前 60 字』，不改」；`docs/specs/dialogue-pairing.md:80`：「片段起点的算法不变」 |
| 10b | 运行环境是 Python 3.12 | `Dockerfile:14`：`FROM python:3.12-slim`；`.github/workflows/build.yml:78`：`python-version: "3.12"`。段 2 用的 `bisect.bisect_right(…, key=…)` 需要 3.10 以上 |
| 11 | 测试库端口 | `docker-compose.test.yml:33`：`55432:5432`。起之前先看端口有没有被占 |

第 4、4a、5 条的全量扫描（在 `main` @ `6a10ab7` 的仓库根目录跑，排除 `tests/`；原始输出）：

```
$ grep -rn "extract_candidates\|dialogue_candidates\|attach_dialogue_examples\|pick_dialogue_examples" --include=*.py core web adapters storage scripts
core/examples_pending.py:17:- `refind`：重新找一次，用的是蒸馏里贴示例的同一步（`Distiller.attach_dialogue_examples`）。
core/examples_pending.py:70:    （后三种是 `attach_dialogue_examples` 抛的 `DistillError`）。调用模型出错不是「找不到」，
core/examples_pending.py:85:            distiller.attach_dialogue_examples,
core/quotes.py:215:def extract_candidates(text: str, names: Sequence[str]) -> list[Candidate]:
core/distiller.py:43:    extract_candidates,
core/distiller.py:665:    取候选（`dialogue_candidates` 判「有没有别人可当对方」）与挑选（enum 的可选项、提示词里
core/distiller.py:1843:    def dialogue_candidates(
core/distiller.py:1852:        只由 `attach_dialogue_examples` 调用。
core/distiller.py:1862:        candidates = extract_candidates(content, [name, *aliases])
core/distiller.py:1869:    def pick_dialogue_examples(
core/distiller.py:1875:        """挑出的示例文本（对外形态，原样保留）。位置归阶段由 `attach_dialogue_examples` 用
core/distiller.py:1876:        `_pick_dialogue_examples` 的起点做，本方法只是 `list[str]` 那个旧口径。"""
core/distiller.py:1877:        return [text for text, _ in self._pick_dialogue_examples(candidates, name, others)]
core/distiller.py:1879:    def _pick_dialogue_examples(
core/distiller.py:1905:        候选由调用方从 `dialogue_candidates` 取（本方法不自己抽取）；
core/distiller.py:1921:            "name": "pick_dialogue_examples",
core/distiller.py:1961:    def attach_dialogue_examples(
core/distiller.py:1983:        候选只从 `dialogue_candidates` 取，不在这里另抽一遍。
core/distiller.py:1992:        candidates = self.dialogue_candidates(content, name, aliases, roster)
core/distiller.py:1993:        picked = self._pick_dialogue_examples(candidates, name, others)
core/distiller.py:2023:        （`attach_dialogue_examples`）。
core/distiller.py:2036:            return self.attach_dialogue_examples(card, content, name, aliases, roster)
```

## 3. 规模表

（§12 加了 2000 字封顶之后重量过：《红楼梦》贾宝玉这两行变了，下表已是新数字；鲁迅 5 篇和刘姥姥的数字没有变。）

都是 Claude 在沙箱里用仓库的 `extract_candidates`、`render_candidates`、`core.tokens.count_tokens` 量的，不调模型。短篇用公版鲁迅 5 篇（13 个角色、65 条候选）；长书用公版《红楼梦》（`tennessine/corpus`，85.5 万字），名字和别名是 Claude 自己取的（贾宝玉 + 宝玉；刘姥姥），不是识别出来的名单。

| | `main` | 只做段 2（不加上限） | 段 1 + 段 2 |
|---|---|---|---|
| 鲁迅：片段平均 / 最长（字） | 106 / 337 | 372 / 773 | 同左 |
| 鲁迅：13 个角色的候选块合计（token） | 8,469 | 20,612 | 同左（最多的阿Q 27 条，不到 40） |
| 其中阿Q（27 条） | 3,175 | 8,846 | 同左 |
| 《红楼梦》：片段平均 / 最长（字） | 220 / 1,894 | 912 / 3,894 | 同左 |
| 《红楼梦》贾宝玉（1,730 条候选）的候选块 | 474,162 token | 1,346,554 token | 35,372 token（40 条） |
| 《红楼梦》刘姥姥（174 条候选）的候选块 | 47,957 token | 137,028 token | 30,677 token（40 条） |

- 只做段 2 的话，贾宝玉这次调用的输入是 135 万 token，超过模型的 100 万窗口，会失败并走保底（出卡、没有示例）；`main` 上是 47 万，能跑。所以上限必须先做，这就是段 1 排在前面、两段放进同一个 PR 的原因。
- 两段都做完，长书主角的输入从 47 万降到 3.5 万 token。
- 短篇每个角色多几千 token。按记录里的闲时输入价每百万 token 1 元算（上一个窗口 10-11 读的官方定价页，本窗口未复核），阿Q 多 0.006 元，13 个角色合计多 0.012 元。
- 13 个角色的 65 条候选，除片段外逐条相同（本句、上一句、引导语、起点、编号）。
- 上一句没写名字、说话人在名单里的 24 条候选：片段里上一句之前看得到这个人名字的，`main` 上 9 条，段 2 后 24 条。
- 这张表是在第 2 版代码上重量的。第 1 版写的 20,606、8,842、1,352,514、137,010、35,581、32,166 作废：前四个是在删掉一小段代码（跳过片段开头空白的循环）之前量的、删掉之后没有重量，差 4 到 170 个 token（把那段循环加回去重量，正好是这四个旧数字）；后两个随「均匀取」的算式换了而变（见 §10 第 5、8 条）。

## 4. 改动

### 段 1：候选上限（先做）

| 改哪里 | 改什么 |
|---|---|
| `core/quotes.py` | 新增私有函数 `_numbered(candidates)`：按给出的顺序从 1 起编号，编号只在这里定。`extract_candidates` 不再边抽边写编号（`:248` 的 `n=len(out) + 1`），改成返回前调一次 `_numbered`。新增常量 `MAX_CANDIDATES = 40` 和纯函数 `spread_evenly(candidates)`：不多于上限原样返回；多于时按出场顺序均匀取 40 条（首尾都取），再调 `_numbered` |
| `core/distiller.py` | `_pick_dialogue_examples` 开头加一行 `candidates = spread_evenly(candidates)`，它下面的 schema 编号上限、渲染、按编号取回用的都是这同一份；import 加 `spread_evenly`；docstring 补一句。`dialogue_candidates` 不动 |
| `tests/test_quotes.py` | 加一条单测，守上限两侧：恰好 40 条原样返回；41 条取成 40 条，首尾在、顺序不变、编号 1–40 |

为什么这样放：

- 「取哪几条」「怎么编号」是不认识模型的纯计算，放在 `core/quotes.py`（它的模块说明：「纯计算，不 import 任何项目模块」）。
- 上限要管的是「交给模型多少」。候选交给模型有两个入口（§2 第 4a 条），都经过 `_pick_dialogue_examples`，所以放在这里是唯一一处；放在 `dialogue_candidates` 的话，对外的 `pick_dialogue_examples` 那个入口管不到。以后再多一个入口，也不用再加。
- 编号规则一处：抽候选和抽样都调 `_numbered`。以后编号规则要变（比如从 0 起），改一处。

### 段 2：片段起点（后做）

| 改哪里 | 改什么 |
|---|---|
| `core/quotes.py` | 删 `_CONTEXT_BACK = 60`；新增私有常量 `_CONTEXT_SENTENCES_BACK = 10`；新增私有函数 `_context_start(text, quotes, sentence_ends, prev_start)`，「片段从哪开始」整条规则只在这个函数里；`extract_candidates` 里全文的句末位置算一次，片段起点改成调它；两处 docstring 里提到「往前 60 字」的话跟着改 |
| `tests/test_quotes.py` | `:145` 那条测试改写成新规则（改名为 `test_the_fragment_keeps_the_previous_line_and_ten_sentences_before_it`）。这是契约变了，不是为了让测试通过 |
| `docs/specs/distill-verbatim-dialogue.md`、`docs/specs/dialogue-pairing.md` | 末尾各加一行「取代（2026-10-11）」，指向本文件（§2 第 10a 条） |

`_context_start` 的规则只有两条：

1. 从上一句的引导语起点往前数 10 个句末（`。！？`，叙述里的和台词里的都算），片段从那里开始；不够 10 句就从全文开头。
2. 数到的位置如果落在某句台词里面，退到那句台词的引导语开头。片段不从半句话开始，那句话是谁说的也一并带上。
3. 上面算出的起点离上一句的引导语超过 2000 字时，按 2000 字截（§12 加的）。这一条排在最后：超了就截，这时片段可以从半句话开始。

复用的现成东西：句末的判定用 `_NARRATION_SENTENCE`（§2 第 2 条），引导语从哪开始用 `_lead_before`（§2 第 3 条），都不另写。句末位置由 `extract_candidates` 算一次，各条候选用标准库的二分查找（`bisect`）共用；引号列表本身就是按位置排好的，直接在它上面二分，不另建一份「引号起点」的表。逐条重扫全文在长书上是平方级的（《红楼梦》1,730 条候选，原型抽取用时 0.1 秒）。

片段的终点不变（到本句署名那半句，#129 定的）。

以后要改的话各改一处：往前带几句改 `_CONTEXT_SENTENCES_BACK`；起点规则改 `_context_start`；上限改 `MAX_CANDIDATES`；怎么取改 `spread_evenly`；怎么编号改 `_numbered`。

### 每条设计的依据

| 设计 | 依据类别 | 出处 |
|---|---|---|
| 给模型看上一句之前的连续原文，而不是紧贴的一小段 | 文献 | Michel 等，NAACL 2025 短文：整章原文（每块 4096 token）、引号编号、带角色表，没写名字的句子 Llama-3 8B 做到 85.7–89.1%，模型没见过的小说上 98.4%，70B 在 1442 句里错 3 句。Chen 等，Findings of EMNLP 2023（中文网文、《平凡的世界》、金庸）：前后各 10 句；上下文短的对照模型在说话人隔得远时掉到约 74% |
| 往前带 10 句 | 文献 | Chen 等，Findings of EMNLP 2023 的窗口。我们只改往前的部分 |
| 数到台词中间时退到引导语开头 | 自研（工程规则） | 没有文献。理由：片段从半句台词开始，模型分不清那半句是叙述还是台词 |
| 上一句之前最多带 2000 字 | 自研（工程上限），依据是实测 | 没有文献。不封顶时，不用 `。！？` 断句的文本会把全文开头到上一句整段带上（§12 的实测）。2000 取自公版四大名著里五个角色的实测：10 句超过 2000 字的，《红楼梦》贾宝玉 1,730 条里 23 条，刘姥姥 174 条、《西游记》行者 3,184 条、《三国演义》曹操 728 条、《水浒传》宋江 798 条里都是 0 条 |
| 候选上限 40 条、全书均匀取 | Shiyu 2026-10-10 同意的方案第 7 项 | 蒸馏遗留记录「方案」表第 7 行。必要性见 §3 |
| 抽样后从 1 重新编号 | 代码事实 | §2 第 7 条 |

读到哪：两篇文献这个窗口都只读了读取工具的摘述，没有逐页读原文；NAACL 2025 的正文上一个窗口读过，数字一致。

## 5. 对账表

独立复核方必须验的清单；变异由复核方自己跑、自己再补，放宽和过严两个方向都要有。下表是 Claude 在第 2 版原型的最终提交上各跑一次的结果（跑的是目标检查、`test_quotes.py`、`test_distiller_dialogue_pick.py`、`test_dialogue_pairing_goal.py`）：14 条里 13 条变红，1 条存活且是等价改动。作者自己跑只说明表里写的属实，不算独立复核。

| 变异 | 变红的测试 |
|---|---|
| 往前带 9 句 | W4；`test_quotes.py` 里改写的那条 |
| 往前带 11 句 | W4；同上 |
| 多数一句的边界（`bisect_right` 写成 `bisect_left`） | W4；同上 |
| 片段从全文开头带起（不设 10 句） | W4；同上 |
| 落在台词里不退到引导语 | W5 |
| 落在台词里时只退到开引号（不带引导语） | W5 |
| 起点仍不越过再前一个引号（旧下限） | W1、W2、W3、W5 |
| 抽样后不重新编号 | C1、C3；`test_quotes.py` 上限那条 |
| 只取开头 40 条 | C1、C2；同上 |
| 上限改成 41 | C1 |
| 挑选那一步不过上限 | C1、C2 |
| 上限挪到 `dialogue_candidates`（不守在交给模型处） | C1 |
| 抽候选时不编号 | 16 条（C4 和三个现有测试文件里的） |
| 台词里的句末不算（只数叙述里的） | `test_quotes.py` 里 §11 补的那条 |
| 换行也算句末 | 同上 |
| 刚好数到全文第一个句末时从头带起（`k >= 0` 写成 `k > 0`） | 同上 |
| 从上一句的开引号往前数（不从引导语起点） | **存活，等价**：引导语里没有句末符号，两种数法结果相同（鲁迅 5 篇加公版《红楼梦》1,969 条候选，片段 0 条不同） |
| 不封顶；封顶 2001；封顶 1999；先封顶再退到引导语；半角 `.!?` 也算句末；落在台词里时退到再前一句台词；不足 10 句时从第一个句末之后起；不到上限时不重新编号（§12 补的 8 条） | 都红：`test_the_fragment_start_at_its_edges`、W8、上限那条单测 |
| 恰好 40 条也重取（`<=` 写成 `<`） | **存活，等价**：40 条里均匀取 40 条取到的就是原来那 40 条，编号也相同，行为没有差别 |

## 6. 测试

本地只跑受影响的文件，库用 Docker 起的 PG（`docker compose -f docker-compose.test.yml up -d --wait`）：

```
python -m pytest -q tests/test_dialogue_context_goal.py tests/test_quotes.py tests/test_dialogue_pairing_goal.py tests/test_distiller_dialogue_pick.py tests/test_distill_task_api.py tests/test_identify_failure_channels.py tests/test_usage_identity_context.py tests/test_longbook_acceptance_card.py tests/test_dialogue_fallback_goal.py tests/test_examples_pending_goal.py tests/test_card_quotes.py
```

沙箱里这 11 个文件在第 2 版原型上是 224 条全过（比第 1 版多的 1 条是上限两侧的单测）。§11 补了一条单测之后是 225 条；§12 之后是 227 条。本段不改前端，不跑 `npm test`。合并门是分支 CI；合并只做 git 操作。报告里不写本地全量的数字。

## 7. 没有验证的

- DeepSeek 关着思考、看到大片段之后，上一句标得多准，没有实测。Tracy 2026-10-11 定：不为这个单独做实验，等下次正常蒸卡时由 Claude 对照原文判对话示例。
- Claude 用自己这边的模型做过一次对比（Claude Haiku，同样 56 条候选，每条都标）：小片段判对 45、46 条，前 10 句的大片段判对 54、56 条，四次都没有填成名单里另一个人。它不是 DeepSeek，任务也不是「从候选里挑」，只说明大片段让说话人判得出来。
- 长书主角均匀取 40 条之后够不够挑 3 组：上一个窗口在公版《红楼梦》上读过 40 条（21 条是贾宝玉的台词，其中至少 9 组的上一句是别的有名字的人说的），本窗口没有复核；第 2 版「均匀取」的算式换了，取到的 40 条和那次读的不完全是同一批。
- 片段的长度：上一句之前最多 2000 字（§12）；上一句和本句自己多长就多长，这一点和 `main` 一样。公版《红楼梦》贾宝玉最长的一条片段是 3,894 字。
- 连续的句末符号各算一句（「？！」算两句），沿用现成的 `_NARRATION_SENTENCE`，没有专门的测试守它。
- 「10 句」只在鲁迅 5 篇上看过效果（出过错的 4 条，说话人的名字分别在上一句之前 45、34、35、289 字，都在 10 句以内）。

## 8. 上线和回滚

不涉及数据库、配置、前端。合并进 `main` 不会自动上线，部署是手动触发的（`.github/workflows/deploy.yml:20-21`）。已存的卡不变，只影响之后的蒸馏和「重新找一次」。回滚就是回退这一个合并提交。

## 9. S0（开工前逐条复核，有一条不成立就停下报告）

1. `git rev-parse --short origin/main` 是 `6a10ab7`。不是的话报出新的提交，并报 `git diff 6a10ab7 origin/main --stat -- core/quotes.py core/distiller.py tests/test_quotes.py`。
2. §2 表里第 1–10b 条（含 4a、6a、7a、10a、10b），逐条打开对应的行核对原文。
3. §2 的两条全量扫描重跑一遍：生产代码里 `extract_candidates` 的调用点仍然只有 `core/distiller.py:1862` 一处；调 `_pick_dialogue_examples` 的仍然只有 `:1877`、`:1993` 两处；读 `.context` 的仍然只有 `core/quotes.py:296` 一处。
4. 端口 55432 没有被别的容器占用；被占就另起一次性的 PG（端口换一个，配置对齐 `docker-compose.test.yml`，跑完删掉），不动别人的容器。

## 10. 补充：第 2 版改了什么（2026-10-11，Tracy 要求核对代码之后）

对照 `rework-lessons.md` 第 6、25、26 条（一条规则只写一处、职责隔离、以后新增只改一处）和第 7、8 条（事实现查、扫描贴原始输出），把第 1 版的代码和 spec 逐处过了一遍。行为没有变（抽到的候选、片段的规则、上限 40 都和第 1 版一样），变的是规则放在哪、写了几处。

| # | 第 1 版的问题 | 根因 | 第 2 版怎么改 |
|---|---|---|---|
| 1 | 上限放在 `dialogue_candidates` 里 | 职责放错了地方：上限管的是「交给模型多少」，却守在「抽候选」那一步。候选交给模型有两个入口（§2 第 4a 条），对外的 `pick_dialogue_examples` 那个不经过 `dialogue_candidates`，管不到 | 挪到两个入口都经过的 `_pick_dialogue_examples`；C1 改成直接递候选进去，守住这一点 |
| 2 | 编号写了两处：抽候选时 `n=len(out) + 1`，抽样后又 `enumerate` 一遍 | 一条规则写两处 | 收成 `_numbered`，两处都调它 |
| 3 | `_context_start` 多收一个参数 `quote_starts`，它是从 `quotes` 算出来的另一份表 | 同一份数据传了两个形态，调用方要记得两个一起传、一起更新 | 去掉，直接在 `quotes` 上二分（标准库 `bisect` 的 `key`） |
| 4 | `spread_evenly` 有个 `limit` 参数，没有任何调用方传它 | 用不上的口子 | 去掉，只认 `MAX_CANDIDATES` |
| 5 | 均匀取的算式带四舍五入：`(i * last + (limit - 1) // 2) // (limit - 1)` | 读不出意图，也没有哪条要求需要它 | 换成 `i * last // (MAX_CANDIDATES - 1)`：首尾都取、顺序不变，C1、C2 照旧 |
| 6 | 常量 `CONTEXT_SENTENCES_BACK` 是公开的，模块外没人用 | 多露了一个名字，别处一旦引用，这里就不好改 | 改成私有 `_CONTEXT_SENTENCES_BACK`（原来的 `_CONTEXT_BACK` 也是私有的） |
| 7 | 目标检查里自己抄了一份假适配器，仓库里这是第三份 | 没先找现成的 | 改成从 `tests/test_dialogue_pairing_goal.py` 引 `_PickLLM`、`_pick`（仓库里测试之间这样互引是现成做法，如 `tests/test_card_draft.py:26`） |
| 8 | spec 自己的三处：§2 的扫描输出少了 `pick_dialogue_examples` 那几行，贴的不是原始输出；§3 有 4 个数字是删掉一小段代码之前量的，之后没重量；没写取代了旧 spec 的哪一句 | 第 7、8、17 条没照做：改完代码没有把 spec 里的每个数字和每段输出重跑一遍 | 扫描在 `main` 上重跑、原样贴；§3 全表在第 2 版代码上重量；头部和段 2 写明取代的条款，旧 spec 末尾加指向 |

另外补了一条单测守上限两侧（恰好 40 条、41 条），第 1 版没有。

保留没改、说明一下的两处：

- 「数到台词中间时退到引导语开头」仍然是自研的工程规则（§4 依据表），没有文献。它和「往前带 10 句」是同一条规则的两半（都在回答「片段从哪开始」），所以放在同一个函数里，没有拆成两处。
- 抽候选时先用 `n=0` 占位、返回前统一编号。占位的 0 不出这个函数；把它去掉要改 `Candidate` 的形状，会牵动 `render_candidates`、`build_example` 和所有用到它的测试，超出本段范围。

## 11. 补充：审计 `d134dd0`（2026-10-11，Claude 拉分支审）

审的是 PR #134，分支头 `d134dd0`，三个提交 `eb903ca`、`47bd62e`、`d134dd0`。审的人是这份 spec 和参考实现的作者，不算独立复核；独立复核另做。

先跑目标检查（在分支上逐个提交实跑）：`eb903ca` 7 红 4 绿（C1、C2、W1–W5）；`47bd62e` 5 红 6 绿（W1–W5）；`d134dd0` 11 绿。§6 那条命令 224 条全过。分支 CI：Build run 38109168827，提交 `d134dd0`，结论 success。

逐文件：

| 文件 | 看过，结论 |
|---|---|
| `core/quotes.py` | 与参考实现逐字相同。`_numbered`、`_context_start`、`spread_evenly` 各一处，规则没有写第二遍 |
| `core/distiller.py` | 与参考实现逐字相同。上限只在 `_pick_dialogue_examples` 开头一行，`dialogue_candidates` 没动 |
| `tests/test_quotes.py` | 与参考实现逐字相同。**有缺口，见下** |
| `tests/test_dialogue_context_goal.py` | sha256 前 16 位 `dc024263c8a3e7c4`，与交付的一致 |
| `docs/specs/dialogue-context-window.md` | sha256 前 16 位 `30a7bcb0f892b634`，与交付的一致 |
| `docs/specs/dialogue-pairing.md`、`docs/specs/distill-verbatim-dialogue.md` | 末尾各多一行「取代（2026-10-11）」，别处没动 |

分支相对 `main` 只改了这 7 个文件。实现没有偏离 spec。

问题一条，属于 spec 和测试设计的缺口，不是实现偏离：

| 问题 | 根因 | 处理 |
|---|---|---|
| §4 段 2 第 1 条写了「一句」怎么数（按 `。！？`，台词里的也算；换行不算），但没有测试守它。审计自补的变异里有 3 条存活：台词里的句末不算、换行也算句末、刚好数到全文第一个句末时从头带起 | W4 和改写的那条单测，用的文本只有叙述句、只有一行，而且离全文开头很远。这三种数法在这样的文本上结果相同，测试分不出来 | 在 `tests/test_quotes.py` 补一条 `test_a_sentence_is_counted_by_its_end_mark_spoken_or_not_and_a_line_break_is_not_one`：文本里有台词里的句末、有不带句号的换行，并且刚好数到全文第一个句末。补上后这 3 条都变红。只加测试，不改实现 |

审计自补的变异（在 `d134dd0` 上跑，不在 §5 第 2 版原来那 14 条里）：

| 方向 | 变异 | 补测试前 | 补测试后 |
|---|---|---|---|
| 放宽 | 台词里的句末不算 | 存活 | 红 |
| 放宽 | 刚好数到全文第一个句末时从头带起 | 存活 | 红 |
| 放宽 | 从上一句的开引号往前数 | 存活（等价） | 存活（等价） |
| 放宽 | 上限只管渲染，schema 的编号上限仍按全部候选 | C1、C3 红 | 同左 |
| 过严 | 换行也算句末 | 存活 | 红 |
| 过严 | 落在台词里时往后跳到台词结束 | W5 红 | 同左 |
| 过严 | 均匀取不含最后一条 | C1、上限那条单测红 | 同左 |
| 过严 | 上限 39 | C1、C3 红 | 同左 |
| 过严 | 不足 10 句时一句都不带 | W1、W2、W3 红 | 同左 |

没有核实的：CI 页面上 gate、sentinel 两个 job 各有 9 条「Event loop is closed」的标注，run 的结论是 success；`main` 上有没有同样的标注没有查。

## 12. 补充：独立复核 `bd6a553` 和处理（2026-10-11）

独立复核由另开的、不带本对话上下文的复核方做（Claude 的子代理，只读分支，自补 15 条变异）。结论是不可以合并，有一条阻塞问题。Claude 复现属实后在同一分支上改掉。Tracy 2026-10-11 15:04 说 Claude 可以自己改、自己推，这一节的改动是 Claude 直接推到分支上的。

| # | 严重程度 | 问题 | 属于 | 根因 | 处理 |
|---|---|---|---|---|---|
| 1 | 阻塞 | 片段长度没有上限。不用 `。！？` 断句的文本数不到句末，片段从全文开头带到上一句。Claude 复现：半角标点的 14.6 万字文本、2,000 条候选，`main` 上抽取峰值 1.8 MB、片段最长 73 字；`bd6a553` 上峰值 294 MB、片段最长 14.6 万字、取 40 条后交给模型 292 万字。线上 app 容器内存限额 768 MB（`docker-compose.prod.yml:137`：`mem_limit: 768m`） | spec 设计错 | 目标里写了「片段变长之后，交给模型的东西仍在它一次能读的量以内」，但只用候选条数去守，片段本身多长没有守。§7 第 1 版写过「没有按字数封顶，别的书没有量」，把一个没有上界的量当成了「没验证」，没当成缺陷；规模表里也没有它的上限 | `_context_start` 最后加一步按字数截：`return max(start, prev_start - _CONTEXT_CHARS_BACK)`，`_CONTEXT_CHARS_BACK = 2000`。仍然只在这一个函数里。改后同一份文本：峰值 10.1 MB、片段最长 2,073 字、交给模型 8.5 万字 |
| 2 | 不阻塞 | 三条规则没有测试守：落在台词里时退到哪一句；不足 10 句时从全文开头带起；半角标点算不算句末 | 测试缺口 | 现有用例里那句台词前面没有别的台词，文本也都够 10 句 | 加一条 `test_the_fragment_start_at_its_edges`，四个边界各一组断言 |
| 3 | 不阻塞 | `spread_evenly` 只在超过上限时重新编号，不到上限原样返回；而 docstring 写的是「谁传候选进来都一样」。递进来编号是 5、9 的两条候选，模型会看到 `[5]`、`[9]`，schema 的上限却是 2 | 实现和自己的说明不符 | 「取哪几条」和「编号」绑在一个分支里，只有超限那一支编号 | `spread_evenly` 改成不管取没取都调 `_numbered`。生产路径的编号本来就是 1 到 N，行为不变 |
| 4 | 不阻塞 | W1 里 `except Exception: pass` 吞得太宽 | 测试写法 | 图省事 | 改成 `pytest.raises(DistillError)` |

复核方另外确认的：三条目标都成立（12,042 条候选逐条验过片段起点；`spread_evenly` 在 0 到 10 万条上验过；`main` 和分支的候选除片段外逐字段相同）；§2 抽查的事实全部成立。

处理之后重跑（提交见分支）：

- 目标检查 12 条全绿；同一份目标检查在 `main` 上 7 红 5 绿，在 `bd6a553` 上只有 W8 红。
- §6 那条命令 227 条全过。
- 复核方对 `34769e9` 复审：阻塞问题已修（它的三份复现文本，抽取峰值从 1,382 MB 降到 20 MB），三条目标仍成立，结论是可以合并。它另指出边界测试第 ④ 组的文本没走到「数够 10 句后超字数」那条路（两条变异存活：退到引导语之后不再封顶、只在不足 10 句时封顶），已把 ④ 的文本换成台词里带句末的，并加一组长叙述，这两条现在都红。
- 12 条变异全红：§5 新加的那 8 条，加上回归的 4 条（台词里的句末不算、换行也算句末、往前带 9 句、上限 41）。
- 没有被封顶改变的：鲁迅 5 篇 65 条候选与 `main` 除片段外逐条相同，片段平均 372 字、最长 773 字，候选块合计 20,612 token，都和封顶前一样；上一句没写名字的 24 条候选，片段里看得到说话人名字的仍是 24 条。
- 被封顶改变的：《红楼梦》贾宝玉 1,730 条里 23 条（10 句超过 2000 字的那些），这 23 条现在只带 2000 字；其中 14 条截后不足 10 句，另 9 条截后仍有 10 个以上句末（原来是退到台词引导语多带出来的）。复核方另量到同一本书的凤姐、黛玉也有被截的，其余三部书的六个角色都是 0 条：这是《红楼梦》长台词多带来的。

没有做的：连续句末符号怎么数（「？！」现在算两句）没有加测试，写在 §7。

