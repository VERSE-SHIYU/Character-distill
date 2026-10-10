# 保底出卡：对话示例配不上，卡照常生成

- 日期：2026-10-10
- 基线：`main` @ `57a1f88`
- 属于：共享记录「蒸馏遗留：问题与调查记录」方案第 5 项（Shiyu 10-10 已同意）
- 前一段：`docs/specs/dialogue-pairing.md`（找台词、配对、选项、格子，已合并）

## 1. 目标与目标检查

**目标**：给卡片配对话示例这一步挑不出来，卡照常生成，只是没有示例；没配上这件事要让用户知道，不能悄悄少一项。配得上时一切照旧。

**目标检查**：`tests/test_dialogue_fallback_goal.py`，F1–F10，走真实的三条出卡通道，蒸馏器是真 `Distiller`，只把模型换成假适配器。第 1 个提交就带上，之后每个提交都跑。

| 在 `57a1f88` 上 | 条目 |
|---|---|
| 红（8 条） | F1 bg 无对话句、F2 SSE 无对话句、F3 `/run` 无对话句、F4 模型没选出、F5 调用出错、F6 名单里没有别人、F7 完成文案点明、F10 阶段下的示例也算有 |
| 绿（2 条，回归守卫） | F8 配上时照旧、F9 保底只管对话示例这一步 |

实现后 10 条全绿。

## 2. 为什么改

- 蒸馏后补的字段有三个：标签、苏醒台词、对话示例。前两个失败都不影响出卡，只有对话示例失败会让整张卡作废（§3 第 4 条）。
- 作废的时机有两种：原文里找不到对话句的，在蒸馏前的预检就失败，用户拿不到卡；模型没选出或调用出错的，蒸馏的钱已经花了，卡照样作废。
- 前一段合并后，鲁迅 13 个测试角色里仍有 1 个（小尼姑）没有任何能成对的台词，每次都在预检失败。
- Shiyu 10-10 的决定：要有保底。找不到对话示例时照常出卡，缺的以后可以补全，不能整张卡作废。

## 3. 已查实的约束（`文件:行 @ 57a1f88`，每条都是本轮读过的行；S0 逐条复核，不成立即停）

1. **预检有 3 处**（共享记录的方案表只写了 2 处，漏了第三处）：`web/routers/distill.py:404`（bg）、`web/routers/distill.py:1100`（SSE，在识别那个 `try` 里）、`core/text_manager.py:475`（`get_or_distill`）。三处都是在蒸馏前调 `Distiller.dialogue_candidates`，返回值不保留，抽不出就抛。
2. `dialogue_candidates`（`core/distiller.py:1819`）有两种抽不出，都抛 `DistillError`：名单里除本角色外没有别人（`:1839`）；原文里找不到本角色的对话句（`:1843`）。
3. 三条通道落卡前都调 `Distiller.finalize_card`：`web/routers/distill.py:544`、`:1175`，`core/text_manager.py:488`。`finalize_card`（`core/distiller.py:1982`）依次做关系去重、核对引文、`attach_dialogue_examples`（`:2004`）。
4. `attach_dialogue_examples`（`core/distiller.py:1940`）先取候选（`:1967`）再挑选（`:1968`）。挑选没有一组可用时抛 `DistillError`（`:1936`）。调用模型出错时，`select_by_schema` 的异常原样冒出去（目标检查 F5 在 main 上的日志：任务以 `upstream exploded` 失败）。
5. 另两个后置字段失败不影响出卡：标签在 `web/routers/distill.py:548-554` 的 `try` 里，失败只打 warning；苏醒台词 `_generate_awakening`（`:312`）的说明写明不抛。
6. 卡上对话示例为空是合法的：`core/schema.py:308` 默认空列表；聊天时 `core/context_engine.py:450` 为空就跳过这一块。
7. 有起点的卡，示例不在顶层，在各阶段的 `overlay["dialogue_examples"]` 下（`core/distiller.py:1970-1977`）；读阶段下的字段用 `core/card_layers.py:158` 的 `get_path`，这个字段在登记表里（`core/card_layers.py:67`）。
8. bg 的完成文案写死在 `web/routers/distill.py:607`（`"蒸馏完成 ✓"`）；任务的 `message` 落库，并经 `_task_response` 原样返回（`:952`）。SSE 完成帧（`:1212`）没有文案字段。
9. `/run` 返回的是整张卡（`core/text_manager.py:572`），示例是否为空可以直接看出。
10. **前端完成态的文案是写死的，不读后端的 `message`**：`web/frontend/src/components/DistillTaskBar.jsx:14`、`web/frontend/src/components/DistillWorkbench.jsx:38`，工作台完成态 `showMessage: false`（`:46`）。前端只用 bg 这一条通道（`POST /api/distill/start` 加轮询）。
11. 取消靠 bg 循环逐帧查内存里的任务状态（`web/routers/distill.py:434`），不是抛异常。所以在 `finalize_card` 里接住异常不会吞掉取消。
12. 依赖旧行为的现有测试（§6 逐条说明怎么改）：
    - `tests/test_identify_failure_channels.py::TestNoCandidateFailsBeforeAnyPaidStep`（3 条）断言「没有候选在付费前失败」。
    - `tests/test_identify_failure_channels.py::TestAsyncChannelsDoNotBlockTheEventLoop::test_sse`：假蒸馏器没有 `fill_relationships`，SSE 流程在补关系那步就报错返回，根本走不到 `finalize_card`；它现在能过，靠的只是预检那 0.3 秒睡眠在线程里。预检一删它就红。
    - 4 个测试文件的假蒸馏器上有只为预检存在的 `dialogue_candidates` 方法：`tests/test_identify_failure_channels.py`、`tests/test_distill_task_api.py`（2 处）、`tests/test_domain_exception_exit.py`、`tests/test_phase_anchoring.py`。
    - `tests/test_distill_task_api.py::test_a_failed_pick_fails_the_task_instead_of_saving_a_card_without_examples`：假的 `finalize_card` 抛「挑选失败」，断言任务失败。断言仍然成立，但它讲的故事（挑选失败 → 任务失败）不再是真实行为。
13. Claude 在沙箱 `57a1f88` 上做过原型（随提示词附 `proto-dialogue-fallback.diff`）：目标检查 10 条全绿；§8 的 10 个文件 229 条全过；后端全量除两条在未改动的 main 上同样失败的用例外全过（`test_evidence_integrity::test_code_sha_resolves`、`test_arc_phase_fields_unit::test_relationship_batch_splits_and_merges`，都是沙箱环境的问题）。没有别的测试依赖旧行为。

## 4. 改动

### 4.1 一条规则只写一处

| 规则 | 写在哪 | 谁用 |
|---|---|---|
| 对话示例配不上，要不要作废整张卡 | `Distiller.finalize_card` 里调 `attach_dialogue_examples` 的那一处 | 三条通道都经它，口径因此相同 |
| 卡上有没有对话示例 | `core/schema.py` 的 `CharacterCard.has_dialogue_examples()`：顶层或任一阶段下有一组就算有；读阶段用 `get_path`。放在卡自己身上，与 `CharacterArc.has_positions()` 同样的放法 | 完成文案；下一段的界面提示和重跑入口 |
| 完成时对用户说什么 | `web/routers/distill.py` 新增 `_done_message(card)` 和两句文案常量 | bg 终态的 `message`、SSE 完成帧的 `message` |
| 照实抛、带原因的那一步 | `attach_dialogue_examples`，不改 | `finalize_card`；下一段「只重跑这一步」的入口要把原因告诉用户，直接调它 |

以后再加一个后置步骤、改完成文案、改「有没有示例」的判法，都只改上表里的一处。

### 4.2 `finalize_card`：只对对话示例这一步保底

去重、核对引文之后调 `attach_dialogue_examples`：

- 抛 `DistillError`（预期内的挑不出：没有候选、名单里没有别人、模型没选出可用的编号）→ 打 warning，写明角色名和异常的运维口径；返回去重、核对之后的那张卡。
- 抛其他 `Exception`（调用模型出错，或这一步自己的缺陷）→ 打 error，带堆栈；同样返回那张卡。
- 不接 `BaseException`。
- 去重、核对引文出错仍然抛，不在保底范围内。

签名和返回类型不变（仍返回 `CharacterCard`）。不改返回类型的原因：有没有配上示例可以从卡上直接读出（`has_dialogue_examples`），不必多传一个值；4 个测试文件里的假 `finalize_card` 也因此不用动。

### 4.3 删掉 3 处预检

§3 第 1 条的三处调用连同它们的注释删掉。`dialogue_candidates` 本身不改，之后只由 `attach_dialogue_examples` 调用。SSE 那处在识别的 `try` 里，只删预检那一句，`try` 和 `aliases_for` 留着。

### 4.4 完成文案

- 配上了：`蒸馏完成 ✓`（与现在相同）。
- 没配上：`蒸馏完成，未配上对话示例（可在编辑角色卡时手动填写）`。

bg 终态的 `message`（`:607`）和 SSE 完成帧新增的 `message` 字段都取 `_done_message(card)`。`/run` 不加字段（§3 第 9 条）。

原因（找不到对话句、模型没选出、调用出错）只进日志，不上屏：这一段用户能做的只有手填，原因对他没有用处；下一段的重跑入口会把原因直接告诉用户。

### 4.5 不动的

`attach_dialogue_examples`、`dialogue_candidates`、`_pick_dialogue_examples` 的行为；`core/quotes.py`；前端；`/run` 的返回形状；任务表结构；`web/server.py` 的统一出口。

### 4.6 注释

代码里所有描述旧行为的注释和说明改成与新行为一致，只描述现状，不写「原先……现在……」。用 `预检`、`fail-open`、`补充 1-第` 三个词搜 `core/distiller.py`、`core/text_manager.py`、`web/routers/distill.py`，改完后这三个词在这三个文件里应搜不到。测试文件里描述旧行为的说明同样要改（见 §13）。

## 5. 取代前置 spec

在 `docs/specs/distill-verbatim-dialogue.md` 末尾加一行，指向本文件，说明被取代的两处：「挑不出对话示例按任务失败处理（不落没有示例的卡）」和「补充 1-第 4 步：三条通道在长步骤之前做预检」。「补充 1-第 3 步」里取候选只走 `dialogue_candidates` 这一条仍然有效。

## 6. 现有测试怎么改（不为迁就测试放宽判据）

1. `TestNoCandidateFailsBeforeAnyPaidStep`（3 条）删掉，连同只有它用的 `_RecordingLLM`、`_no_quote_body`、`NO_CANDIDATE_TEXT`、`_recording_distiller`、`_assert_no_paid_step_ran`。它守的行为被 F1–F3 的相反行为取代。原处留一行注释指向目标检查。
2. `_SleepyDistiller`：删掉 `dialogue_candidates`，补一个空的 `fill_relationships`，让 SSE 那条真的走到 `finalize_card`。说明和断言文案里的「预检／挑选」「0.6 秒」改成「后置步骤」「0.3 秒」。两条断言的阈值不动。
3. 4 个测试文件里假蒸馏器上的 `dialogue_candidates` 方法删掉（§3 第 12 条）。
4. `test_a_failed_pick_fails_the_task_instead_of_saving_a_card_without_examples` 改名为 `test_an_error_out_of_finalize_card_fails_the_task`，说明改成「`finalize_card` 抛出来的按任务失败处理，路由自己不多吞一层」，假错误的文案换成与挑选无关的。断言逻辑不动。
5. `tests/test_distiller_dialogue_pick.py::test_attach_passes_the_failure_through_instead_of_saving_an_empty_field` 只改说明（本方法照实抛，保底是 `finalize_card` 的事）。断言不动。

## 7. 对账表（独立复核方必须验的清单；变异由复核方跑，并自己再补，放宽和过严两个方向都要有）

| 行为 | 守它的测试 | 让它变红的变异 |
|---|---|---|
| 没有对话句也出卡（三条通道） | F1、F2、F3 | 恢复任一条通道的预检（对应那一条红）；去掉 `finalize_card` 的保底（三条都红） |
| 模型没选出也出卡 | F4 | 保底只在「没有候选」时生效 |
| 调用出错也出卡 | F5 | 保底只接 `DistillError` |
| 名单里没有别人也出卡 | F6 | 「名单里没有别人」单独放行抛出 |
| 没配上要说，两通道同一句 | F7 | bg 完成文案改回写死；SSE 完成帧不带 `message`；两处各写一句 |
| 配上时照旧 | F8 | `has_dialogue_examples` 恒假 |
| 阶段下的示例也算有 | F8、F10 | `has_dialogue_examples` 只看顶层 |
| 保底只管对话示例这一步 | F9 | 把 `finalize_card` 整个包进 `try` |
| 照实抛的那一步仍然抛 | `test_distiller_dialogue_pick.py::test_attach_passes_the_failure_through_instead_of_saving_an_empty_field` | 在 `attach_dialogue_examples` 里面吞掉异常 |
| 路由自己不多吞一层 | `test_distill_task_api.py::test_an_error_out_of_finalize_card_fails_the_task` | 路由把 `finalize_card` 的调用 `try` 掉 |
| 后置步骤不阻塞事件循环 | `TestAsyncChannelsDoNotBlockTheEventLoop`（2 条） | SSE 或 `get_or_distill` 里把 `finalize_card` 改回同步调用 |

## 8. 测试

本地只跑受影响的文件：`tests/test_dialogue_fallback_goal.py`、`tests/test_identify_failure_channels.py`、`tests/test_distill_task_api.py`、`tests/test_distiller_dialogue_pick.py`、`tests/test_domain_exception_exit.py`、`tests/test_phase_anchoring.py`、`tests/test_dialogue_pairing_goal.py`、`tests/test_quotes.py`、`tests/test_usage_identity_context.py`、`tests/test_longbook_acceptance_card.py`。库用 Docker 起的 PG。本段不改前端，不跑 `npm test`。合并门是分支 CI；合并只做 git 操作。报告里不写本地全量的数字。

## 9. 验收

本段不需要调模型的验收。目标检查走的就是真实的三条通道，模型是假适配器；保底改的是「出不出卡」，不依赖模型答得对不对。

真实模型下的验证并进最后那次付费重蒸：13 个角色全部出卡，小尼姑的卡没有示例、任务的完成文案是「未配上对话示例」那一句。

## 10. 已知的缺口和后果

- **界面上看不到这句提示**。前端完成态的文案是写死的（§3 第 10 条）。本段只改后端：接口返回的 `message` 和日志里有，任务栏和工作台上那一行不变。界面提示放在下一段，和「只重跑这一步／手填」的入口一起做，那时提示和补救办法在同一处。本段合并后、下一段合并前，没配上示例的卡在界面上和别的卡看不出区别。
- **没有对话句的角色不再在付费前失败**。蒸馏的钱照花，得到一张没有示例的卡。这是保底这个决定本身的后果。
- 原因只进日志（§4.4）。
- 已经存下的卡不受影响。

## 11. S0（执行方先做，只读）

1. 逐条复核 §3 的事实，报「成立」或新坐标；有一条不成立就停下报告。
2. 在没改代码的分支上跑目标检查，应为 8 红 2 绿（§1 的表）。对不上就停下报告。

## 12. 范围规矩

本段改动面内新发现的问题直接修；会撞车或需要 Shiyu 拍板的才停下报告；不自行记账。本段不做界面提示、重跑入口、候选上限、「挑中的那句是不是本人说的」的核对（U12），发现它们的问题只记在报告里。

## 13. 补充（2026-10-10，独立复核之前作者自查）

对照返工经验第 6、25 条重看了一遍本段，改两处，都不改行为：

1. **「卡上有没有对话示例」放错了模块。** 原先写成 `core/distiller.py` 里的一个函数，路由为了问卡上有没有示例要去 import 蒸馏器。根因：它是卡的性质，不是蒸馏的步骤。本仓同类判定的放法是挂在数据模型上（`CharacterArc.has_positions()`，`core/schema.py:267`，被 `core/distiller.py:1977`、`core/arc_view.py:73`、`core/text_manager.py:624` 直接调用）。改成 `CharacterCard.has_dialogue_examples()`，路由不再为此多 import。下一段的重跑入口和界面提示也从这里取。目标检查 F10 跟着改成调卡上的方法。
2. **§4.6 只点了三个源码文件，漏了测试文件。** `tests/test_distiller_dialogue_pick.py`、`tests/test_identify_failure_channels.py`、`tests/test_distill_task_api.py` 里有 6 处说明还在讲「预检」「挑不出即任务失败」，已改成只描述现状，断言不动。执行方在报告里指出了这一点。

另记一条观察，本段不处理：后置字段（标签、苏醒台词、对话示例）失败都不影响出卡，但三个字段各有各的写法，分别在路由的 `try`、`_generate_awakening`、`finalize_card` 里。它们发生的时机不同（存卡前、存卡后单独补），统一成一套是另一件事，不在本段范围。
