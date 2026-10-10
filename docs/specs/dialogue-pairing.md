# 对话示例：找台词、配上一句、标说话人（蒸馏遗留①② 第 1 段，2026-10-10，Shiyu 已拍板）

> 放到 worktree 的 `docs/specs/dialogue-pairing.md`，补充写进本文件。
> **前置 spec**：`docs/specs/distill-verbatim-dialogue.md`（WP17 及补充 1、2）。本 spec 取代其中三处，见 §5。
> **基线**：main `51b214b`。**分支**：从最新 main 开 `fix/dialogue-pairing`。
> **参考实现**：随本 spec 交付的 `proto-dialogue-pairing.diff`（Claude 在沙箱 `51b214b` 上的原型，已跑通 §1 的目标检查）。只供对照，不要求照抄；判据以 §1 和 §4 为准。
> **本段范围**：只做「挑选对话示例」这一步内部的四件事（§4）。保底出卡、只重跑这一步的入口、长书候选上限是后面三段，**本段不做**。
> **执行方 skill**：`@tdd`。

## 1. 目标与目标检查

**目标**：角色在原文里有台词就找得到；配成的一对是真的一问一答；上一句的说话人标得对，标不出就不成组。

**目标检查**：`tests/test_dialogue_pairing_goal.py`（随本 spec 交付，第 1 个提交就带上，之后每个提交都跑）。K1–K12，走真实代码，原文和期望都是文件里的字面量。

| 状态 | 条目 |
|---|---|
| main `51b214b` 上红（10 条） | K1 署名在引号后、K2 引导语隔换行、K3 全角半角、K4 带引号的词不是台词、K5 上一句是带引号的词、K6 两句隔得远、K8 说话人选项、K9 本人／名单外／无法判断不成组、K10 赵太爷、K11 六个格子 |
| main 上就绿（回归守卫，2 条） | K7 署名接着下一个引号的不算前一句的、K12 只取前 3 组 |

以上红绿是 Claude 在沙箱 `51b214b` 上实跑的结果（10 failed, 2 passed）；原型上 12 passed。

## 2. 为什么改（证据，详见共享记录「蒸馏遗留：问题与调查记录」）

- 历史 34 次蒸馏任务 17 次报错，13 次出在这一步；7 个角色的 46 句真台词，现规则找到 0 句。
- 已存 12 张卡里 4 张、共 7 条示例把「上一句」标错人：上一句其实是角色本人或名单外的人说的，选项里没有正确答案。
- 2026-10-10 用 26 次 `deepseek-flash` 真实调用验证过本 spec 的做法：模型挑中的 56 句全是角色自己的台词；「上一句」标对的比例从 20 组里 15 组升到 19 组里 18 组；再加 §4.2 的间隔规则后，留下的 15 组全对。
- 同一次验证里，赵太爷在现有选项下连续 3 次返回非法 JSON（断在说话人的值上），加了「本人」「名单外的人」选项后正常；但 3 个格子全被不能成对的候选占掉，得了 0 组。

## 3. 已查实的约束（`文件:行 @ 51b214b`，每条都是本轮读过的行；S0 逐条复核，不成立即停）

1. 候选只认引号前同一句里的名字。`core/quotes.py:199-201`：
   `lead, _ = _lead_before(text, quotes, i, m.start())` / `if not any(name in lead for name in wanted): continue`。
2. 引导语在最后一个句读或换行处截断。`core/quotes.py:141`：`_LEAD_BREAK = re.compile(r"[。！？\n]")`；`_lead_before`（`:213-223`）循环 `_LEAD_BREAK.finditer(head)` 取最后一个断点之后的部分。
3. 「上一句」恒为紧挨着的前一个引号，不看它是不是台词、隔了多远。`core/quotes.py:202`：`prev = quotes[i - 1]`。
4. 片段到本句收尾引号为止。`core/quotes.py:209`：`context=text[start:m.end()]`。
5. 主引号样式每篇只取一对。`core/quotes.py:140`、`_quote_re`（`:144`）。**本段不动。**
6. 格子数与示例数是同一个常量。`core/quotes.py:243` `MAX_EXAMPLES = 3`；`pick_slot_properties`（`:266`）与 `valid_picks`（`:287`）都是 `for i in range(1, MAX_EXAMPLES + 1)`。
7. 合格的说话人 = enum 减去「无法判断」。`core/quotes.py:284`：`allowed = set(enum) - {UNDECIDED}`。
8. enum 只有名单里的其他人加「无法判断」。`core/distiller.py:1886-1888`：
   `enum = [*(dict.fromkeys(o.get("name") for o in others if o.get("name"))), UNDECIDED]` / `properties = pick_slot_properties(len(candidates), enum)`；校验用同一个 `enum`（`:1924`）。
9. 本人不在 `others` 里。`core/distiller.py:633` `_other_people`，条件在 `:646`：`if c.get("name") and c.get("name") != name and c.get("name") not in aliases`。**本段不动。**
10. 提示词里格子名和「最多几组」由 `MAX_EXAMPLES` 生成（`core/distiller.py:1891-1892`、`:1910`）；说话人那句是 `:1914-1915`「从名单里取标准名；判不准就填「无法判断」（那一格会被丢弃）」。
11. 调用点全量扫描（`git grep -n "extract_candidates\|valid_picks\|pick_slot_properties" 51b214b -- '*.py'`，去掉 `core/quotes.py` 自身和两个测试文件后）只有 `core/distiller.py:1838`、`:1888`、`:1924`（另有 `:40/:43/:45` 的 import）。**改动面因此限定在 `core/quotes.py` 与 `core/distiller.py::_pick_dialogue_examples`。**
12. `core/quotes.py` 是纯计算模块，不 import 项目模块（`:4` 文件头）。新增的规则保持这一点。
13. 会变红的现有测试，Claude 在沙箱原型上实跑得出，共 3 条（`tests/test_quotes.py` 与 `tests/test_distiller_dialogue_pick.py` 里其余 35 条不变；另跑了 12 个相关测试文件共 256 条，全部不变）：
    - `tests/test_quotes.py:231` `test_zero_out_of_range_duplicate_and_undecided_slots_are_dropped`
    - `tests/test_distiller_dialogue_pick.py:82` `test_the_schema_is_fixed_slots_not_an_unbounded_array`
    - `tests/test_distiller_dialogue_pick.py:235` `test_the_subject_and_its_aliases_are_kept_out_of_the_enum`
14. **未核实**：DeepSeek strict 模式对「12 个必填字段」的 schema 是否接受。官方文档未写字段数上限（前置 spec 约束 6）；现在是 6 个字段。验收回放的第一次调用即可见，被拒就停下报告，不自行改 schema。

## 4. 改动

### 4.1 一条规则只写一处

| 规则 | 写在哪（`core/quotes.py`） | 谁用 |
|---|---|---|
| 什么算一句台词：引号内最后一个字不是字、字母、数字 | `is_spoken(inner)`，一条判别，不列标点清单 | 判本句，也判上一句 |
| 署名写在引号前：引导语到哪为止 | 现有的 `_lead_before`，在它**里面**改（见 4.2 ①），不另加分支 | `extract_candidates`（判署名、显示引导语、算片段起点，三处仍共用它） |
| 署名写在引号后：哪半句算、片段截到哪 | 与 `_lead_before` 对称的一个私有函数（见 4.2 ②） | `extract_candidates` |
| 全角与半角视为相同：比名字、比引导语的收尾标点 | 一个私有函数（`_same_width`），名字和署名切片两边都过它，引导语的收尾也过它；用标准库 `unicodedata.normalize("NFKC", …)`，不自造对照表 | 同上 |
| 能不能成对：上一句是台词，且两句之间的叙述不超过 `MAX_NARRATION_BETWEEN = 2` 句 | 一个私有函数加一个常量 | 同上 |
| 「上一句是谁说的」有哪些选项、哪些算合格 | `SpeakerOptions(others, subject)`：`.enum` 给 schema，`.accepts(speaker)` 给校验 | `pick_slot_properties` 与 `valid_picks` 收同一个对象 |
| 给模型几个格子 | `PICK_SLOTS = 2 * MAX_EXAMPLES` | `pick_slot_properties`、`valid_picks`、提示词里的格子名 |
| 最多留几组 | `MAX_EXAMPLES`（不变） | `valid_picks` 凑满即停 |

以后加一种署名位置、加一种「不合格」的说话人标签、调格子数，都只改上表里的一处。

### 4.2 `extract_candidates`（签名、`Candidate` 字段、编号方式都不变）

一个引号成为候选，要同时满足：

1. 它是台词（`is_spoken`）。
2. 它和紧挨着的前一个引号能成对：前一个引号是台词，且两个引号之间的叙述里句读（`。！？`）不超过 2 个。
3. 它的署名里出现本角色的名字或别名。署名在引号前的引导语里，或引号后的那半句里：
   - ① 引号前的引导语（`_lead_before`）：`凤姐笑道：“……”`。**改 `_lead_before` 本身的断句规则**：换行仍算断点，但换行前那半句以冒号或逗号收尾（话没说完，全角半角都算）时不算，于是 `闰土又对我说：⏎“……”` 的引导语就是「闰土又对我说：」。根因是它把所有换行都当成句子结束；不在外面另写一个「隔了换行」的分支。
   - ② 引号后的那半句：收尾引号之后、下一个引号之前，以句读或换行收住的部分：`“……”四叔说。`
     没有被收住、直接接着下一个引号的（`“……”孔乙己答道，“……”`）是下一句的引导语，不算这一句的署名。

片段（`context`）终点从「本句收尾引号」延到「② 那半句的句读」（以换行收住的，到换行之前）；没有 ② 时终点不变。模型要看见引号后的署名才判得出是谁说的。片段起点的算法不变，仍由 `_lead_before` 给出。

`_lead_before` 改了之后，`Candidate.lead`、`prev_lead` 和 `render_candidates` 的「本句／上一句」那两行会跟着带上分行的引导语，这是想要的效果。`render_candidates`、`build_example`、`_quote_re` 不动。

### 4.3 说话人选项与格子

- `SpeakerOptions.enum` = 名单里其他人的标准名 + 本角色标准名 + `OUTSIDER = "名单外的人"` + `UNDECIDED`（顺序如此）。
- `SpeakerOptions.accepts` 只认名单里其他人的标准名。本人、名单外的人、无法判断都不成组。
- `pick_slot_properties(total, options)` 生成 `PICK_SLOTS` 个格子；`valid_picks(raw, total, options)` 按格子顺序校验，凑满 `MAX_EXAMPLES` 组即停。其余校验（0、越界、重复、非整数、缺字段、非对象）不变。

### 4.4 `core/distiller.py::_pick_dialogue_examples`

只改三样：用 `SpeakerOptions` 生成 enum 并交给 `valid_picks`；格子名按 `PICK_SLOTS` 生成；提示词两句换成下面的原文。函数其余部分、`_other_people`、`_roster_hint`、`dialogue_candidates`、`attach_dialogue_examples`、`finalize_card` 不动。

提示词第一处，`:1909-1910` 换成（`{…}` 是变量）：

> 请只挑同时满足以下三条的候选，按想要的顺序把编号填进 {slot_names}（共 {PICK_SLOTS} 格，代码按顺序取前 {MAX_EXAMPLES} 组合格的；没有合适的候选时，多出的格子编号填 0）：

提示词第二处，`:1914-1915` 换成（10-10 验证时用的那句，删掉了「上一句根本不是对话」这半句：这种候选现在由代码在 4.2 第 2 条挡掉，到不了模型，同一条规则不写两处）：

> 每格对应的上一句说话人填进 {speaker_names}：上一句的说话人可能不在名单里。是名单里的人，填他的标准名；是「{name}」本人说的，填「{name}」；是名单以外的人说的，填「{OUTSIDER}」；判不准填「{UNDECIDED}」。后三种那一格会被丢弃，这是正常结果，请如实填，不要为了凑数从名单里挑一个最像的。

①②③ 三条和前面的说明不动。

### 4.5 不动的

`web/`、`core/text_manager.py`、`core/schema.py`、`adapters/`、前端。预检照旧：没有候选仍在花钱前失败（小尼姑按新规则是 0 条候选，本段之后仍会预检失败，这是预期；保底出卡在下一段做）。

## 5. 取代前置 spec 的三处（在 `distill-verbatim-dialogue.md` 末尾加一行指向本文件）

| 前置 spec 的原规定 | 现在 |
|---|---|
| 补充 1-第 2 步：`extract_candidates` 按本角色名召回，只看引号前的引导语；片段到本句结尾止 | §4.2：署名在引号前或引号后、台词判定、成对条件；片段带上引号后的署名 |
| 补充 1-第 2 步：enum 不含本角色（防「自己接自己」） | enum 含本人，但选到本人的格子被丢弃。「卡里不出现自己接自己」这条不变，由 K9 守 |
| 补充 2：`MAX_EXAMPLES` 个固定槽位 | `PICK_SLOTS` 个固定槽位，留前 `MAX_EXAMPLES` 组。「输出有界」这条不变 |

## 6. 现有测试怎么改（§3 第 13 条的 3 条；不为迁就测试放宽判据）

| 测试 | 原来锁的 | 改成锁 |
|---|---|---|
| `test_zero_out_of_range_…_slots_are_dropped` | 第三个参数是 enum，「无法判断」靠减法排除 | 第三个参数是 `SpeakerOptions`；本人、名单外的人、无法判断、不在选项里的名字都丢 |
| `test_the_schema_is_fixed_slots_not_an_unbounded_array` | 3 个格子、enum = 其他人 + 无法判断 | `PICK_SLOTS` 个格子（仍是固定字段、全部必填、`additionalProperties: false`），enum = `SpeakerOptions.enum` |
| `test_the_subject_and_its_aliases_are_kept_out_of_the_enum` | 本角色不进 enum | 本角色的**别名**不进 enum；标准名进 enum 但选到即丢，产出里没有自己接自己 |

## 7. 对账表（独立复核方必须验的清单；变异由复核方跑，并自己再补，放宽和过严两个方向都要有）

| 行为 | 守它的测试 | 让它变红的变异 |
|---|---|---|
| 署名在引号后也找得到，片段带上署名 | K1 | 不看引号后的半句；或片段终点改回本句收尾引号 |
| 引导语隔换行也找得到 | K2 | `_lead_before` 改回把所有换行都当断点 |
| 换行是断点：下一段不是这一句的署名 | K13 | `_LEAD_BREAK` 不认换行（过宽） |
| 换行是断点：上一行已收住时，那一行不是引导语 | `tests/test_quotes.py::test_a_finished_line_before_the_quote_is_not_its_lead` | `_lead_before` 不把换行当断点（过宽） |
| 引导语以冒号、逗号收尾算没说完，全角半角都算 | `tests/test_quotes.py::test_a_lead_left_unfinished_before_a_newline_still_leads_its_quote` | `_UNFINISHED` 去掉一种；或收尾不过 `_same_width` 就比（都是过严） |
| 全角半角视为同名 | K3 | 名字不折叠 |
| 带引号的词不是台词 | K4 | `is_spoken` 恒真 |
| 以标点或符号收尾的都是台词，不靠清单 | `tests/test_quotes.py::test_a_quote_is_a_spoken_line_unless_it_ends_in_a_word_character` | 改回只认列出的几种标点（过严）；恒真（过宽） |
| 上一句必须是台词 | K5 | 成对条件不查上一句 |
| 两句隔得远不成对 | K6 | 阈值改 3（放宽，far 那条红）；改 1（过严，near 那条红） |
| 接着下一个引号的署名不算前一句的 | K7 | 引号后的半句不要求被句读或换行收住 |
| 选项含本人与名单外的人 | K8 | enum 改回其他人 + 无法判断 |
| 这三类标签不成组 | K9、K10 | `accepts` 接受本人或名单外的人 |
| 格子比示例多，后面的格子能补上 | K11 | `PICK_SLOTS` 改回 `MAX_EXAMPLES` |
| 最多留 3 组 | K12 | 去掉凑满即停 |
| enum 与校验同源 | §6 第 1 条 | `valid_picks` 改收一个与 schema 无关的名单 |

10-10 独立复核后的更正：原表写「换行仍是断点」由 `tests/test_quotes.py` 现有的引导语用例守，实际没有这样的用例（去掉换行断点后全绿）；「没说完的收尾」只有「：」有判据。现补 K13 和两条单测，上表已按补后的写。同一轮核对里还改了两处手列的标点清单（都来自 Claude 的原型，属于逐个补的写法）：① `_SPOKEN_END` 列了 19 种收尾标点，全角半角列得不齐，全量扫描 313 个公版文件（鲁迅全集 309 篇、四大名著）的 47490 个引号，有 26 句台词以「．」收尾（公版《水浒传》的「．．．」）不在清单里，会被当成不是台词；改成一条判别「最后一个字不是字、字母、数字」，在 5 篇逐句标注上与原清单结果完全相同（321 句对话全认出，139 个非对话误收 3 个），65 条候选逐条相同。② `_UNFINISHED` 把冒号、逗号的全角半角各列一遍，改成只写一遍、比之前过 `_same_width`；同一次扫描「一行收尾 → 换行 → 开引号」共 2545 处，以「：」收尾 794 处、「，」28 处、半角两种 0 处（半角没有实例，只是不再单独列）。另更正两处点名：`PICK_SLOTS` 那行只有 K11 会红，同源那行只有 §6 第 1 条会红。

## 8. 测试

本地只跑受影响的文件：`tests/test_dialogue_pairing_goal.py`、`tests/test_quotes.py`、`tests/test_distiller_dialogue_pick.py`、`tests/test_distill_task_api.py`、`tests/test_identify_failure_channels.py`、`tests/test_usage_identity_context.py`、`tests/test_longbook_acceptance_card.py`。库用 Docker 起的 PG。本段不改前端，不跑 `npm test`。合并门是分支 CI；合并只做 git 操作。报告里不写本地全量的数字。

## 9. 验收（实现和独立复核都通过后，由 Shiyu 触发；另给提示词）

不蒸馏的回放，约 1 美分：13 个角色各调一次真实的 `dialogue_candidates` + `_pick_dialogue_examples`。

- 候选数应为：孔乙己 7、掌柜 7、我 4、阿Q 27、赵太爷 6、小尼姑 0、秀才 2、邹七嫂 1、小D 3、双喜 1、祥林嫂 1、魯四老爺 1、闰土 5（Claude 在公版原文上用原型量的；10-10 的回放里，同一套署名规则在 Shiyu 的版本上候选数与公版逐一相同）。
- 达标线：挑中的全是角色自己的台词；除小尼姑外 12 个角色都有示例；标错人不超过 1 组；没有调用失败。对错由 Claude 对照逐句标注判。

## 10. 样本之外

- 所有数字只来自鲁迅 5 篇（460 个引号，Claude 逐句标了说话人）。`MAX_NARRATION_BETWEEN = 2` 在别的书上没有验证。「不以字收尾才算台词」另在 313 个公版文件上扫过：会多收带注释号或括号收尾的引用词（「重见汉官威仪〔10〕」这类，47490 个引号里约 26 个），多收的仍由模型读片段后判，和 main 的行为一样；不会漏台词。
- 引导语隔换行只认以「：」「，」收尾的。同一次扫描里以「——」收尾的有 73 处（如「还听得有人说——⏎“……”」）、「……」15 处，都不认，结果是少找到，不会找错；5 篇样本的正文里只出现「：」41 处、「，」2 处。
- 句读只数 `。！？`。用别的符号断句的版本（如全角句点 `．`）会数少，结果是少挡，不会多挡。
- 只写「他／她」或不写说话人的台词仍然找不到（46 句里 23 句）。它们不影响能否出示例：一张卡最多用 3 组。
- 上一句是本人说的候选，代码挡不干净（试过三种按署名判断的规则，误伤 6～11 组能成对的），仍交给模型认；10-10 实测 6 处认出 5 处。
- 长书：候选数量没有上限、英文直引号的版本配不成对，都不在本段。

## 11. S0（执行方先做，只读）

1. 在最新 main 上逐条复核 §3 的 1–13；行号有移动就报新坐标，有一条不成立就停下报告。
2. 把目标检查放进 `tests/`，在未改代码的分支上跑一次，应为 10 红 2 绿（§1 的表）。不一致就停下报告。
3. 都成立就直接往下做，不必等回复。

## 12. 范围规矩

本段改动面内新发现的问题直接修；会撞车或需要 Shiyu 拍板的才停下报告；不自行记账。本段不做保底出卡、重跑入口、候选上限，发现它们的问题只记在报告里。
