# 逐字核对先繁转简（2026-09-29，Shiyu 已定：官方 OpenCC t2s）

> 放到 worktree 的 `docs/specs/quote-script-fold.md`，以后的补充写进本文件。
> **执行方**：distill-mainline 窗口，从 origin/main（基线 `be4ad239`）新开分支 `fix/quote-script-fold`（`--unset-upstream`）。skill 见文末。

## 目标
逐字核对时原文与引文两侧先经 OpenCC `t2s` 转成简体，繁体原文里的真引文不再被误撤回、真口癖不再被误删。

## 问题（实测证据）
2026-09-29 端到端真跑（《红楼梦》1–3 回，zh.wikisource，21747 字）撤回 6 条：
`personality_traits[0]`「天上一轮才捧出，人间万姓仰头看」「飞騰之兆已見」；`key_memories[2]`「生情狡猾，擅纂礼仪，且沽清正之名，而暗结虎狼之属」；`catchphrases`「只刚念了《四书》」「不曾读，只上了一年学，些须认得几个字」「我自来是如此，从会吃饮食时便吃药」。
留下的贾雨村口癖是繁体（「若論時尚之學……」，代码从原文逐字复制），被撤回的全是模型用简体转述的句子 → 原文是繁体、核对只比字形。**原文是繁体这一点由 S0 第 2 步直接核实，不成立即停。**

## 出处对照表
| 做法 | 出处 | 事实依据 | 本 spec 行为 |
|---|---|---|---|
| 繁转简用 OpenCC `t2s` | github.com/BYVoid/OpenCC（Apache-2.0；Releases 页 2026-07-12 发 1.4.1）；PyPI `opencc` 最新 1.4.2 | 官方项目，`t2s` 是其标准繁转简配置 | 行为 1 |
| 不用 zhconv | PyPI zhconv 1.4.3 元数据 | GPLv2+ | — |
| 不用 opencc-purepy | PyPI opencc-purepy 1.4.3（2026-08-29，MIT） | 沙箱实测常驻 +24MB、90 万字 0.55s，官方版 +8MB、0.04s；SZ 内存紧 | — |
| 加依赖走 `requirements.in` + uv 重锁 | 本仓 `requirements.in` 文件头 | 重锁命令带 `--constraint requirements.txt`，清理 `# via -c` 边后其余逐字节不变 | 步骤 1 |
| `t2s` 的转换链 | OpenCC 官方 `data/config/t2s.json`（master） | 先查 `TSPhrases`（短语），再 `TSCharactersExt`、`TSCharacters`（单字），`short_circuit`；预处理 `CJK_Compatibility_Ideographs` | 约束 2（先转后去标点） |
| 「著」不在 t2s 单字表；「髮/發」都转「发」 | OpenCC 官方 `data/dictionary/TSCharacters.txt`：`grep -P "^(著\|髮\|發)\t"` 只命中 `發→发`、`髮→发` 两行 | 并字表仍需「著→着」；不同繁体字会被并成同一简体字 | 行为 3、约束 4 |
| 「著→着」并字表保留 | `docs/specs/quote-variant-fold.md` | `t2s` 不把「著」转「着」（实测「活著」原样） | 行为 3 |

## 全量扫描（沙箱，基线 `be4ad239` = 当前 origin/main 顶端；原始输出）
```
$ git grep -n "normalize(\|verbatim_in(\|verbatim_in_normalized(\|finalize_card(" -- '*.py' ':!tests'   （已去掉同名无关的 _normalize_identify_items 与 core/moderation）
core/card_quotes.py:86:        if verbatim_in_normalized(source_norm, inner):
core/card_quotes.py:108:    source_norm = normalize(content)
core/card_quotes.py:122:            if verbatim_in_normalized(source_norm, cp):
core/distiller.py:1808:    def finalize_card(
core/quotes.py:30:def normalize(s) -> str:
core/quotes.py:38:def verbatim_in_normalized(source_norm: str, quote: str) -> bool:
core/quotes.py:45:    segs = [s for s in (normalize(p) for p in _ELLIPSIS.split(str(quote))) if s]
core/quotes.py:49:def verbatim_in(source: str, quote: str) -> bool:
core/quotes.py:58:    return verbatim_in_normalized(normalize(source), quote)
core/quotes.py:82:            if len(normalize(m.group(1))) < CITATION_MIN_CHARS:
web/routers/distill.py:514:        card = distiller.finalize_card(card, content, name, aliases, chars)

$ git grep -n "finalize_card" -- core web | grep -v "def "
core/distiller.py:1829:        card, _ = retract_unverified(card, content)        （finalize_card 内部）
core/text_manager.py:473:                    self._distiller.finalize_card,
web/routers/distill.py:514:        card = distiller.finalize_card(card, content, name, aliases, chars)
web/routers/distill.py:1135:                distiller.finalize_card,

$ git grep -n "normalize\|verbatim_in\|quote_misses\|catchphrase_misses\|dialogue_hits" -- tests/perf/longbook_acceptance.py
tests/perf/longbook_acceptance.py:59:    normalize,
tests/perf/longbook_acceptance.py:61:    verbatim_in,
tests/perf/longbook_acceptance.py:62:    verbatim_in_normalized,
tests/perf/longbook_acceptance.py:542:            "dialogue": dialogue_hits(dumped.get("dialogue_examples"), content),
tests/perf/longbook_acceptance.py:543:            "catchphrase_miss": catchphrase_misses(
tests/perf/longbook_acceptance.py:545:            "quote_miss": quote_misses(dumped, content),
tests/perf/longbook_acceptance.py:571:def dialogue_hits(groups, content: str) -> dict:
tests/perf/longbook_acceptance.py:577:        if not (lines and all(verbatim_in(content, x) for x in lines)):
tests/perf/longbook_acceptance.py:582:def quote_misses(card, content: str) -> list[dict]:
tests/perf/longbook_acceptance.py:594:    source_norm = normalize(content)
tests/perf/longbook_acceptance.py:601:            if (field, q) in seen or verbatim_in_normalized(source_norm, q):
tests/perf/longbook_acceptance.py:618:def catchphrase_misses(catchphrases, content: str) -> list[str]:
tests/perf/longbook_acceptance.py:625:    return [str(c) for c in (catchphrases or []) if not verbatim_in(content, c)]

$ grep -n "MAX_FILE_SIZE =" web/routers/text.py
108:MAX_FILE_SIZE = 30 * 1024 * 1024  # 30MB

$ uv pip compile requirements.in -o requirements.txt --universal --python-version 3.12 --constraint requirements.txt   （requirements.in 加 opencc>=1.4.2 后）
$ diff <(grep "==" 旧锁) <(grep "==" 新锁)
66a67
> opencc==1.4.2
（pin 行 132 → 133）

$ pip download opencc==1.4.2 --no-deps --only-binary=:all: --platform <p> --python-version 3.12
opencc-1.4.2-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl
opencc-1.4.2-cp312-cp312-win_amd64.whl
opencc-1.4.2-cp312-cp312-macosx_11_0_arm64.whl
opencc-1.4.2-cp312-cp312-manylinux2014_aarch64.manylinux_2_17_aarch64.whl
```
短语边界样本（**8 组**，引文单独转 vs 在原文里转）：乾隆年間/隆年間、頭髮乾了/髮乾了、乾坤大挪移/坤大挪、一隻手錶/隻手錶、瞭解了一下/解了一下、於是他說/是他說、他後來著書立說/來著書、面麪條/麪條 —— 全部命中。简体样本「凤姐笑道……干净、乾隆、著作、后来、面条」过 `t2s` 原样不变。**样本之外**的行为见约束 4。

## 规模表
| 量 | 数值 | 来源 |
|---|---|---|
| 依赖体积 | 安装包 2.4MB，装后 7.1MB；镜像 pip 层（压缩 174.6MB）增约 1% | 沙箱实测 |
| 常驻内存 | +8MB / 进程（模块级单例，只载入一次） | 沙箱实测 |
| 整本归一化 | 90 万字 `t2s` 0.04s（重复合成文本；真实全书在验收里实测） | 沙箱实测 |
| 原文上限 | 上传上限 30MB（`web/routers/text.py:108`），UTF-8 中文约 1000 万字；按合成文本速度外推约 0.5s，在线程里跑（见约束 6），不占事件循环 | 代码 + 外推（验收实测真实全书校准） |
| 每卡核对量 | 整本只归一化一次（`card_quotes.py:108`），逐条引文只转自身几十字 | 现有计数探针锁 |
| 部署一次性代价 | 改锁 → pip 层重建，合并后首次 main build 重传约 175MB 到阿里云、SZ 首次重拉；之后缓存命中 | PR #66 缓存机制 |

## 已查实的约束（S0 逐条复核，不成立即停）
1. `normalize` 是唯一归一化出口（`core/quotes.py:30`），产品（`card_quotes`）与验收（`longbook_acceptance`）都经它；只改这一处。
2. 转换在去标点**之前**做：`t2s` 按短语转换，先去标点会把跨标点的字粘成新短语。顺序：`t2s` → 去空白标点 → 并字。
3. 转换器为模块级单例 `_T2S`；`tests/test_card_quotes.py::test_the_source_is_normalized_exactly_once` 的计数探针不受影响（沙箱实测仍绿）。
4. **已知取舍（照实写进注释）**：`t2s` 会把不同的繁体字并成同一简体字（如「髮/發」→「发」），这类差异在逐字核对里不再算差异——卡片本身是简体，语义不变。反方向的边界（引文恰好切在一个整体转换的短语中间）只会退回今天的行为（判查不到），不会误判为命中。
5. 不改函数签名，不改 Dockerfile。
6. **路径机制（通道 × 执行上下文 × 守它的测试）**：三条产卡通道都经 `Distiller.finalize_card`（`core/distiller.py:1808`）→ `retract_unverified`（`:1829`）→ `normalize`。转换器是**跨线程共享的单例**。

   | 通道 | 调用点 | 执行上下文 | 守接线的现有测试 |
   |---|---|---|---|
   | 后台任务 | `web/routers/distill.py:514`（`_run_distill_task`） | 同步，工作线程 | `tests/test_identify_failure_channels.py::TestQuoteRetractionOnEveryChannel::test_bg_task` |
   | SSE | `web/routers/distill.py:1135` | `asyncio.to_thread` | 同类 `::test_sse`；不阻塞事件循环：`::TestAsyncChannelsDoNotBlockTheEventLoop::test_sse` |
   | TextManager | `core/text_manager.py:473` | `asyncio.to_thread` | 同类 `::test_text_manager`；`::TestAsyncChannelsDoNotBlockTheEventLoop::test_text_manager` |

   线程安全：沙箱 **8 线程 × 50 次**并发共用一个 `OpenCC("t2s")` 转同一段 6.4 万字，结果不一致 0 次（样本范围即此）；官方 `t2s.json` 是纯词典查表链，无可变状态。本改动不动接线，上表测试沿用。
7. **测试环境冲突**：`docker-compose.test.yml` 的项目名固定为 `character-distill-test`、端口 55432，各条线共用。起之前 `docker ps --filter name=character-distill-test` 看是否已在跑：在跑且不是本线起的，**不要 down / 重启**，直接复用（tmpfs，库是测试专用）；55432 被别的进程占用就停下报告。

## 调用点矩阵
| 调用点 | 可观测输出 | 守它的测试 |
|---|---|---|
| `card_quotes.py:86`（引文） | 繁体原文的简体真引文不撤回 | `test_card_quotes.py::test_a_simplified_quote_of_a_traditional_source_keeps_its_quotes` |
| `card_quotes.py:122`（口癖） | 繁体原文的简体真口癖不删 | `test_card_quotes.py::test_a_simplified_catchphrase_of_a_traditional_source_is_kept` |
| `quotes.py:45/:58`（`verbatim_in`） | 简体、繁简夹杂引文都命中 | `test_quotes.py::test_a_simplified_quote_matches_a_traditional_source`、`::test_a_mixed_script_quote_matches_a_traditional_source` |
| `quotes.py` 模块载入 | 转换器只构造一次 | `test_quotes.py::test_the_converter_is_built_once_not_per_call` |
| `quotes.py:82`（长度过滤） | 无变化 | `test_quotes.py::test_quotes_shorter_than_the_minimum_after_normalizing_are_skipped`（现有） |
| `longbook_acceptance.py:577`（对话示例） | 无变化（示例由代码从原文复制） | `test_longbook_acceptance_card.py::test_a_labelled_multiline_group_hits_and_a_one_char_change_misses`、`::test_a_dialogue_excerpt_spliced_with_ellipsis_still_hits_the_source`（现有） |
| `longbook_acceptance.py:601`（引文） | 验收不报 | `test_longbook_acceptance_card.py::test_a_simplified_quote_of_a_traditional_source_is_not_reported` |
| `longbook_acceptance.py:625`（口癖） | 验收不报 | `test_longbook_acceptance_card.py::test_a_simplified_catchphrase_of_a_traditional_source_is_not_reported` |
| 三条产卡通道 | 接线不变、不阻塞事件循环 | 约束 6 表内现有测试 |

## 步骤（每步独立 commit）
1. **[deps]** `requirements.in` 加 `opencc>=1.4.2`（放在 `slowapi` 之后），按文件头命令重锁并清理 `# via -c requirements.txt` 边。验收：`requirements.txt` 的 pin 行只多 `opencc==1.4.2` 一行，其余与旧锁逐字节相同；`requirements-dev.txt` 不动。
2. **[core/quotes]** `import opencc`；模块级 `_T2S = opencc.OpenCC("t2s")`（注释：为何单例、约束 4 的取舍）；`normalize` 先 `_T2S.convert(str(s))`，再去空白标点，再 `translate(_VARIANT_FOLD)`。docstring 改为「繁转简后去空白标点，再并异体字」。
3. **[tests]** 与步骤 2 同 commit：上面矩阵里 7 条新用例（沙箱已写好的形状：繁体夹具取公版《红楼梦》原句）。

## 测试（按约束 7 起或复用测试库；本地只跑受影响的文件加 `npm test`；合并门是分支 CI；合并只做 git 操作，不跑测试、不等 CI）
受影响文件：`tests/test_identify_failure_channels.py`（约束 6 的通道测试）、`tests/test_quotes.py`、`tests/test_card_quotes.py`、`tests/test_longbook_acceptance_card.py`、`tests/test_distiller_dialogue_pick.py`、`tests/test_longbook_acceptance_identify.py`、`tests/test_longbook_acceptance_log.py`。

## 对账表（沙箱预跑；红源集合按名点出，全命中才算）
沙箱：`be4ad239` + 本改动，`pytest --noconftest` 跑纯函数的 6 个文件共 77 条（`test_identify_failure_channels.py` 需测试 PG，沙箱没有，由执行方本地跑）。改前（新测试、旧代码）：7 failed / 70 passed，红源为 7 条新用例；改后 77 passed。

| 行为 | 变异 | 预跑红源集合 |
|---|---|---|
| 1 繁转简 | 去掉 `t2s` | 7 条新用例全红 |
| 2 单例 | 在 `normalize` 里每次新建 `OpenCC("t2s")` | 仅 `test_the_converter_is_built_once_not_per_call` |
| 产品与验收同口径 | 不改 `normalize`、只在 `card_quotes` 的 `source_norm` 转原文 | `test_quotes` 3 条 + 验收 2 条红；`card_quotes` 2 条仍绿（此变异恰在产品侧，由前 5 条拦） |
| 3 并字表保留 | `_VARIANT_FOLD = {}` | 「著/着」5 条原有用例红 |

C 落位后在真实测试文件里按同样四个变异复跑，红源集合与上表不一致就停下报告。

## skill
| 步骤 | skill | 用途 |
|---|---|---|
| S0 | `@search-first` | 复核坐标 |
| 2–3 | `@tdd` | 先写 7 条用例看红，再改 |
| 验收 | `@verification-before-completion` | 贴原始输出 |

## 范围规矩
新发现的问题属于本段改动面的直接修；会撞车或需 Shiyu 拍板才停。交付报告不写本地全量测试数字。

## S0（只读；全部成立直接编码，一条不成立才停）
1. 本文件 `Test-Path docs/specs/quote-script-fold.md` 为真，`Get-FileHash` 与下载件一致；`git fetch origin`，确认基线是 `origin/main` 顶端，复核约束 1 的坐标。
2. **核实原文是繁体**：取 `zh.wikisource`《紅樓夢》第一至三回（若 `D:\Temp\cdm-e2e\` 里还有本次语料就用它，已删则重新抓同一来源），贴其中黛玉「不曾讀，只上了一年學」一句的原始字形。不是繁体就停下报告。
## 验收（本地，改完代码后、推送前；贴原始输出）
**真实数据复核（自动）**：用改后的 `normalize`
   - 对上面 6 条撤回句逐条跑 `verbatim_in(原文, 句子)`，贴每条结果（预期全真；个别不真就贴原文对应段，说明差在哪，不调代码去凑）；
   - 对本地简体《红楼梦》（`text_id 068692e5b6f7`，866149 字）计时一次整本 `normalize`，贴耗时；
   - 对两张演示卡（宝玉、刘姥姥现态）跑 `retract_unverified`，撤回清单必须仍为空（回归）。


合并后看第一次 main build：gate 绿即可；阿里云推送因 pip 层重建可能较慢，属预期（见规模表）。不需要真实模型调用。

## 补充（2026-09-29 审计自查，发出前）
1. 坐标按当前 main 重读：`quotes.py` 行号因 PR #61 并字改动整体下移 3 行（原稿写的 `:27/:42/:55/:79` 作废，现为 `:30/:45/:58/:82`）。
2. 补约束 6（通道 × 执行上下文 × 测试）与线程安全样本；补约束 7（共用测试库不 down）。
3. 补出处：官方 `t2s.json` 转换链、`TSCharacters.txt` 的「著」「髮/發」条目；补规模：上传上限 30MB 与外推耗时。
4. 调用点矩阵补 `:577` 与 `:82` 两行现有测试，三通道一行。
5. 测试一节按固定写法加 `npm test`。

## 补充（2026-09-29 审计，执行方交付后）
1. **规模表的耗时要按实测改读**：本地简体《红楼梦》866149 字整本 `normalize` 实测 0.50–0.57 秒（三次），原稿引用的「90 万字 0.04 秒」是重复合成文本，低估约 10 倍。按实测外推，上传上限 30MB（约 1000 万字）约 6 秒；每张卡只整本归一化一次，且在工作线程里跑（约束 6），相对一次蒸馏的分钟级耗时可接受，不改实现。
2. **验收第 1 句「天上一轮才捧出…」改前改后都判查不到，原因在语料不在代码**：端到端那次抓维基文库用的 `prop=extracts&explaintext` 会丢掉 `<poem>` 标签里的诗，这句诗不在上传的语料里。改用 `action=parse` 重抓第 001–003 回后 6 句全部命中。以后抓公版语料做验收，用 `action=parse`。
3. 审计复核：分支 `fix/quote-script-fold`（`77a4ecc1`、`62d20991`）改动 6 个文件，与步骤一致；锁文件只多 `opencc==1.4.2` 一条；沙箱在分支上重跑纯函数 6 文件 77 条全绿；执行方 4 个变异的红源集合与对账表逐条一致。
