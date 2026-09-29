# 逐字核对把异体字视为同一字（2026-09-29 修订版，Shiyu 已定：先修再部署）

> 放到 worktree 的 `docs/specs/quote-variant-fold.md`，以后的补充写进本文件。
> **执行方**：C 窗口，从 origin/main 新开分支 `fix/quote-variant-fold`（`--unset-upstream`）。基线 `e0fd868d`，rebase 到 origin/main `ea005ad0`（其后为 PR #55–#57，与本次无文件重叠）。skill 见文末。
> **交接核对**：放入 worktree 后先 `Test-Path docs/specs/quote-variant-fold.md` 为 True 再开工。旧版：`e0fd868d` 的 `docs/specs/` 下无同主题文件，`git grep -in "opencc\|zhconv\|quote-variant-fold"` 全仓无命中，无需标「已取代」。

## 目标
逐字核对时，把版本间常见的异体字当作同一个字，避免原文里真实存在的引文（和口癖）被误处理。

## 问题
- **机制（沙箱已复现，见「对账表」的改前一行）**：原文写「著」、卡上写「着」，`normalize` 不把二者视为同一字，`verbatim_in` 判假，`retract_unverified` 撤回引文引号、整条删掉口癖。
- **真实案例**：宝玉卡 `values[3]`（第五十七回「活著……化灰化烟」）被误撤回，卡上「着」、本地原文「著」，已人工补回。**出处说明**：原记录文件 `baoyu-card-corrections.md` 已随 temp 清理不在了（仓库里也没有，38 个远端分支均无）。该案例的硬证据改用管道原产物 `baoyu_card_v4.json`（22:38 那一轮的输出，路径与 sha256 由 S0 第 2 步贴进报告）；机制本身由沙箱复现证明，不依赖它。

## 库与自研（自研须写明理由）
| 库 | 版本 | 许可证 | 「活著→活着」 | 「著作」 | 结论 |
|---|---|---|---|---|---|
| OpenCC `t2s` | 1.4.2 | Apache-2.0 | **不转**（原样「活著」） | 不动 | 不满足需求 |
| OpenCC `tw2s` | 1.4.2 | Apache-2.0 | 转成「活着」 | 不动 | 是繁转简配置，用在简体文本上属误用；顺带改「么→幺」 |
| zhconv | 1.4.3 | **GPLv2+** | 转成「活着」 | 不动 | 许可证不可用 |
| opencc-python-reimplemented | 0.1.7 | Apache | 未测 | — | 与官方 OpenCC 同名包冲突（上一轮结论，本轮未复跑；前三行已足以定案） |

以上前三行均在沙箱实测（`pip install` 后读取包元数据并逐条调用），原始输出：
```
zhconv 1.4.3 | License: GPLv2+ ; zh-cn: 活着 著作
opencc 1.4.2 | License: Apache License 2.0
t2s: 活著 著作 么      tw2s: 活着 著作 幺
```
**结论**：没有合适的库，按规矩自研，只放一组。自研写在 `normalize` 内（`str.translate`，零依赖）。

## 出处对照表
| 做法 | 出处 | 事实依据 | 成熟度 |
|---|---|---|---|
| 「著」「着」在不同版本里互为用字差异 | 本仓实测：本地原文「活著」、卡上「活着」 | 沙箱复现见对账表 | 实测 |
| 字典侧依据 | 教育部《異體字字典》A03506（「著」条）。**一手页面已拉取**：`A03506-003`「為『著』之異體」，是**字形级**异体（页面把该字形显示为代码，正文里看不到「着」二字）。二手：维基词典「着」条引用 `A03506-003#37`、`A03506-015#18`，同条注明大陆《通用规范汉字表》把「著」视为独立的字 | 字典支持「着」出现在「著」条下，但不是通用等价 | 字典权威，对「着」的直接表述属二手 |
| 只并有依据的字组，不并近义字 | 同上 | 「唬」与「吓」不是异体关系，不并 | — |

**口径**：并字表只是逐字核对的口径，不是通用等价。表内每行写字组、依据、本 spec 的链接。

## 扫描
- **代码调用点**（沙箱在 `e0fd868d` 上 `git grep -n "normalize\|_DROP_CHARS\|verbatim_in" -- core tests/perf`，路径均已确认存在；`core/distiller.py:1184/1214` 的 `_normalize_identify_items` 是同名无关函数，已排除）：
```
core/quotes.py:24   _DROP_CHARS
core/quotes.py:27-29 def normalize
core/quotes.py:35   def verbatim_in_normalized      core/quotes.py:42  内部对每个引文段调 normalize
core/quotes.py:46   def verbatim_in                  core/quotes.py:55  调 normalize(source)
core/quotes.py:79   quoted_spans 内 len(normalize(..)) 只判长度
core/card_quotes.py:20 import  :86 verbatim_in_normalized(引文)  :108 source_norm = normalize(content)  :122 verbatim_in_normalized(口癖)
tests/perf/longbook_acceptance.py:577 verbatim_in(对话示例)  :594 normalize(content)  :601 verbatim_in_normalized(引文)  :625 verbatim_in(口癖)
```
- **原文全量扫描**（数据在本地库，不在仓库，沙箱够不到）：**本地两张卡已于 09-28 人工修正，现在都是改后态，下表的「改前 4 条」在本地无法重放，仅作历史记录，不再作为重放预期。** 改前基线改由 S0 第 2 步在内存里重建。

| 卡 | 字段 | 卡上 → 原文 | 差异类型 | 处理 | 记录出处 |
|---|---|---|---|---|---|
| 宝玉 | `values[3]` | 活**着** → 活**著** | 单字 | **并字** | 审计记录（不在仓库） |
| 刘姥姥 | `key_memories[2]` | **唬**的不敢作声 → 本地原字待 S0 确认 | 单字 | 不并，照旧撤回 | `docs/specs/card-quote-verification.md`：措辞不逐字 |
| 刘姥姥 | `emotional_patterns[1]` | 已念了几千声佛 → 改写 | 多字改写 | 不并，照旧撤回 | 审计记录 |
| 刘姥姥 | `personality_traits[2]` | 「你就说醉话……」 | 拼接 | 不并，照旧撤回 | 同上文档（醉话一句） |

「唬」一行不需要先知道本地原字：它被撤回，说明本地原文里没有「唬的不敢作声」这一句，而并字表里没有「唬」，处理结论与原字无关。

## 规模表
| 量 | 数值 |
|---|---|
| 归一化一次整本书 | 约 0.13 秒（审计方实测）；`str.translate` 只增毫秒级 |
| 映射前后字符数 | 不变（一字换一字），`CITATION_MIN_CHARS` 等按长度的判定不受影响 |

## 已查实的约束（基线 `e0fd868d`；S0 逐条复核，不成立即停）
1. 坐标见「扫描」的调用点清单。`normalize` 是唯一归一化出口，改这一处，产品与验收同时生效。
2. **现有探针锁**：`tests/test_card_quotes.py::test_the_source_is_normalized_exactly_once` 给 `core.card_quotes.normalize` 与 `core.quotes.normalize` 挂了计数探针。并字必须写在 `normalize` 内部，不许另开函数在外面调（会绕开探针或改变计数）。
3. **本地测试环境**（`docker-compose.test.yml`）：测试库 `postgres:16-alpine`，端口 **55432**，库名 `charsim_test`，数据目录 tmpfs（每次起来都是空库），compose 项目名 `character-distill-test`，不读 `.env`，与本地栈（开发库 5432、docker local 后端 7861、压测 rig 7862 / 容器 `cdload-app-1`）互不冲突。`tests/conftest.py` 在 session 开始就连这个库，连不上整个 pytest 拒绝运行——所以三个纯函数测试文件也**必须先起测试库**。起之前只需确认 55432 没被别的进程占用。
4. 不引入新依赖；不改任何已有函数签名。

## 调用点矩阵（按调用点逐格填）
| 调用点 | 走到的场景 | 本改动后的可观测变化 | 守它的测试 |
|---|---|---|---|
| `card_quotes.py:86`（经 `:108` 归一化的原文） | 卡片引文核对 | 「著/着」之差不再撤回引文引号 | T2 |
| `card_quotes.py:122` | 卡片口癖核对 | 「著/着」之差不再整条删口癖（**行为变化，含连带**） | T2b |
| `quotes.py:42/:55`（`verbatim_in`） | 对话示例、口癖等所有逐字判定 | 同上，判据统一 | T1a、T1c |
| `quotes.py:79` | 引文长度过滤 | 无变化（一字换一字） | 现有 `test_quotes.py` |
| `longbook_acceptance.py:594/:601`（`quote_misses`） | 验收引文核对 | 与产品同口径，不报 | T3 |
| `longbook_acceptance.py:625`（`catchphrase_misses`）、`:577`（`dialogue_hits`） | 验收口癖、对话示例 | 同口径，不报 | T3b |

## 步骤（每步独立 commit）
1. **[core/quotes] 加并字表**：
   - `_VARIANT_FOLD = str.maketrans({"著": "着"})`，放在 `_DROP_CHARS` 之后，注释写字组、依据（《異體字字典》A03506）与本 spec 路径；
   - `normalize` 在去掉标点和空白之后，对结果 `.translate(_VARIANT_FOLD)`；
   - 沙箱实施的改动就是这一处，`git diff --stat`：`core/quotes.py | 5 ++++-`，4 增 1 删。

## 测试（本地先起测试库，只跑受影响的文件，不跑 `npm test`；合并门是分支 CI；合并只做 git 操作，不跑测试、不等 CI）
- **T1a**（`tests/test_quotes.py`）：原文只有「著」，`verbatim_in("你放心，活著咱们一处", "活着咱们一处")` 为真。（原稿夹具里原文已含「活着」，改前也为真，是空断言，已换掉。）
- **T1c**（`tests/test_quotes.py`）：`verbatim_in("吓的不敢作声", "唬的不敢作声")` 为假，防止并字过度。
- **T2**（`tests/test_card_quotes.py`）：原文「著」、卡上引文「着」，`retract_unverified` 的撤回清单为空、引号保留。
- **T2b**（同上）：原文「著」、卡上口癖「着」，口癖不被删、撤回清单为空。
- **T3**（`tests/test_longbook_acceptance_card.py`）：同样差异，`quote_misses` 返回空。
- **T3b**（同上）：`catchphrase_misses` 返回空。

## 对账表（变异已在沙箱预跑；红源集合按名点出，全命中才算符合）
沙箱口径：`e0fd868d`，`pytest --noconftest`（沙箱没有测试 PG），临时文件承载 T1a/T1c/T2/T2b/T3/T3b，另含现有三个测试文件共 42 条，未入库。

| 行为变化（含连带效果） | 让它变红的变异 | 预跑实测的红源集合 | 改后能触发的具体状态 |
|---|---|---|---|
| 著、着视为同一字 | 删掉并字表 | **T1a、T2、T2b、T3、T3b 五条全红**（现有 37 条不受影响） | 宝玉「活著……化灰化烟」这类引文与口癖不再被误处理 |
| 非异体字不并 | 把「唬→吓」加进并字表 | **仅 T1c 红**（41 条绿） | 措辞不逐字的引文仍会被撤回 |
| 产品与验收同口径（连带） | 只在 `card_quotes` 里映射、不改 `normalize` | **T1a、T3、T3b 红；T2、T2b 仍绿**（39 条绿） | 验收与产品判定一致。T2/T2b 拦不住这个变异（映射恰在产品侧），靠 T1a、T3、T3b 拦 |

**改前状态（基线，未改代码）**：同一组测试 **T1a、T2、T2b、T3、T3b 五条红**，T1c 绿，共 5 failed / 36 passed；改后 41 passed（共 41 条）。
**发出前已确认**：三个变异均被杀，无存活变异。**C 的落位后复跑**：在真实测试文件里按上表同样打三个变异，红源集合必须与上表一致，不一致停下报告。

## 以后加新字组的规程
同时满足三条才加：① 本地原文与卡片有实测差异（命令加原始输出贴进对应 spec）；② 有权威来源依据（拉正文，写明链接与条目号）；③ 配一条同型反例（T1c 那样的「不该并」）和一个变异。表内每行注释写这三样的出处。

## skill
| 步骤 | skill | 用途 |
|---|---|---|
| S0 | `@search-first` | 复核坐标 |
| 1 | `@tdd` | 先写 T1a–T3b 让它们变红（本地确认即可，不单独提交），再改代码 |

## 范围规矩
新发现的问题属于本段改动面的直接修；只有会撞车或需要 Shiyu 拍板时才停下报告，不许自行记账。交付报告不写本地全量测试数字。

## S0（只读；全部成立就直接进入编码，不用停下等审计；有一条不成立才停下报告）
1. `Test-Path docs/specs/quote-variant-fold.md`；`git fetch origin` 后确认 `e0fd868d` 是 `origin/main` 的祖先；复核「扫描」调用点清单的路径与行号。
2. **重建改前态（只读；不改库、不 PATCH、不调真实模型）**：
   - 从库读出宝玉、刘姥姥两张卡的现态与 `text_id 068692e5b6f7` 原文；
   - 宝玉：改前原文取自 `baoyu_card_v4.json`（**指定记录文件缺失，改用管道原产物**）。先报 `Get-FileHash` 与路径，再看 `values[3]`：① 若句子含引号且写「着」，它就是撤回前的原始输出，直接作内存副本；② 若引号已被撤回（只剩文字），只对**引号外的原句范围**在内存副本里补回引号，报告里标注「引号为补回，文字取自原产物」。两种情况都不改文件、不改库。找不到该文件或里面没有「着」那句，就停下报告，不许自己编；
   - 刘姥姥：三条改前引文在本地已不存在，报告里注明。「不过度并字」由 T1c 守，另在内存副本的 `key_memories` 里放一条「唬的不敢作声」的引文（**合成，报告里标注为合成**）；
   - 用**基线代码**对这些内存副本跑 `retract_unverified` 与 `quote_misses`，命令与原始输出贴进报告。预期：宝玉重建卡撤回 **1** 条；合成的「唬」引文撤回 1 条；两张现态真卡撤回 0 条。不一致就停下报告。
3. 审对账表。

## 验收（自动，改后对同一批内存副本重放）
改后对 S0 第 2 步的同一批内存副本、同一命令重放：宝玉重建卡撤回 **0** 条；合成的「唬的不敢作声」引文仍撤回 **1** 条（证明没并过度）；两张现态真卡撤回 **0** 条（回归）。整个过程不写库。视觉与交互不涉及，无需手动验收。合入后部署。
