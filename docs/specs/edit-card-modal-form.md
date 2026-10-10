# 编辑弹窗：示例按组保存，每次打开都是这张卡

- 日期：2026-10-10（本文件取代 18:38 发出的同名文件；那一版作废）
- 基线：`main` @ `87aca0d`
- 属于：共享记录「蒸馏遗留：问题与调查记录」方案第 6 项的第 1 段（已证实的问题 P4、P5）
- 分支：从最新 main 开 `fix/edit-card-modal-form`
- 参考实现：随本 spec 交付的改动文件 `proto-edit-card-modal-form.diff`（Claude 在沙箱 `87aca0d` 上的原型，已跑通 §1 的目标检查）。只供对照，判据以 §1、§4 为准
- 执行方 skill：`@tdd`

## 1. 目标与目标检查

**定好的目标（方案第 6 项，Shiyu 10-10 定）**：蒸馏出来的卡缺对话示例时，打开这张卡自动弹出编辑页，让用户自己填；填不出来可以让系统重新找一次。

**本段守住它的两个前提**，别的都不做：

1. 用户在编辑页填的示例，保存后还是一组一组的（对方一句 + 角色一句），不被拆散。
2. 编辑页每次打开，显示和保存的都是这张卡。

**目标检查**：`web/frontend/src/components/__tests__/EditCardModalGoal.test.jsx`（随本 spec 交付，第 1 个提交就带上，之后每个提交都跑）。E1–E8，走真实的 `EditCardModal`，只把 `onSave` 换成记录函数，期望值都是字面量。

| 在 `87aca0d` 上 | 条目 |
|---|---|
| 红（5 条） | E1 两组示例不改直接保存、E2 手填两组、E4 换卡再开、E5 卡变了再开、E6 取消再开 |
| 绿（3 条，回归守卫） | E3 清空得空列表、E7 开着时重渲染不冲掉已填内容、E8 保存失败内容还在 |

以上是 Claude 在沙箱 `87aca0d` 上实跑的结果（5 failed, 3 passed）；原型上 8 passed。

## 2. 为什么改

两处都是 `main` 上现成的故障，Claude 用真实组件复现过：

- **P4 示例被拆散**。卡上两组示例（每组两行），什么都不改直接保存，写回的是 4 条单行。用户照输入框的提示手填两组，结果一样。聊天时只取前 3 条（§3 第 2 条），于是变成一组半。
- **P5 编辑页显示的不是这张卡**。角色页上同一个弹窗先开甲卡、关掉、换成乙卡再开：标题是乙，保存写回的身份和背景是甲的。第 3 段要在新蒸馏的卡上自动弹出这个编辑页，不先修，弹出来的可能是上一张卡的内容。

## 3. 已查实的约束（`文件:行 @ 87aca0d`，每条都是本轮读过的行；S0 逐条复核，不成立即停）

路径省略前缀 `web/frontend/src/`。

1. **示例按行拆、按组拼**。拆：`components/EditCardModal.jsx:162` `dialogue_examples: joinLines(form.dialogue_examples.replace(/\n\n+/g, '\n\n'))`，而 `joinLines`（`:10-11`）是 `return val.split('\n').map((s) => s.trim()).filter(Boolean)`，每一行成了一个元素。拼：`:82-84` `data.dialogue_examples.join('\n\n')`。
2. **存卡的形态是一个元素一组、组内两行**。`core/quotes.py:393`：`return f"{prev_speaker}：{candidate.prev_line}\n{name}：{candidate.line}"`。聊天时取前 3 个元素：`core/context_engine.py:451` `exs = "\n---\n".join(c.dialogue_examples[:3])`。
3. **表单只取一次卡**。`components/EditCardModal.jsx:64`：`if (isOpen && Object.keys(form).length === 0) {`。全文件 `setForm(` 只有两处（`:86` 初始化、`:92` 改单个字段），没有任何地方把它置回空。
4. **关掉只是不渲染，状态还在**。`components/EditCardModal.jsx:176` `if (!isOpen) return null`，排在所有 `useState` 之后。
5. **三个宿主**（`grep -rn "<EditCardModal" src`，去掉测试）。`components/CharCard.jsx:949-955` 一直挂着弹窗（`isOpen={showEditModal}`）；`components/TextPanel.jsx:785-786`（`{editCard && (`）、`components/MarketCardDetail.jsx:1147-1148`（`{showEditModal && (`）是打开才挂。所以 P5 只在角色页出现。
6. **角色页换卡时弹窗所在的组件是同一个实例**。`components/CharCard.jsx:130`、`:144` 的 `<CardDetail card={currentCard} …>` 没有 `key`；换卡只是换 `currentCard`（`store/useAppStore.js:1138-1144` 的 `viewCard`）。
7. **CI 的前端门**：`.github/workflows/build.yml:260` `npx eslint . -c eslint.ci.config.js --quiet`、`:266` `npm test`。
8. **现有测试**：Claude 在沙箱实跑，`npm test` 在 `87aca0d` 上是 92 个文件 481 条，原型上是 93 个文件 489 条，全过；CI 的 lint 命令退出码 0。没有现有测试需要改。

## 4. 改动

### 4.1 一条规则只写一处

| 规则 | 写在哪 | 谁用 |
|---|---|---|
| 示例在表单里的文本 ⇄ 存卡的列表 | 新文件 `utils/dialogueExamples.js`：`examplesToText` / `textToExamples` | 弹窗的初值和保存 |
| 表单什么时候从卡上取值 | `EditCardModal` 自己：没打开时不挂表单；表单的初值只在挂载时取一次 | 三个宿主都经它，宿主不用改 |

### 4.2 `utils/dialogueExamples.js`

- `examplesToText(examples)`：数组用空一行（`\n\n`）连接；不是数组的原样返回，空值给空串（与 `:82-84` 现在的行为相同）。
- `textToExamples(text)`：按空行分组（空行里只有空白也算）；组内每行去掉首尾空白、丢掉空行，用 `\n` 连回去；空组丢掉。

弹窗的初值用前者，保存用后者。`joinLines` 留给其余「每行一条」的字段，不再碰对话示例。

### 4.3 `EditCardModal`：每次打开都重新挂载表单

- `EditCardModal` 变成外壳：`isOpen` 为假返回 `null`，为真渲染里面的表单组件。
- 表单组件的各个状态用 `useState` 的初始化函数从 `data` 取初值，只在挂载时执行一次。

这样三件事同时成立：开着的这一次，父组件重渲染不会冲掉用户已填的内容（E7）；关掉再开、或换了一张卡，拿到的是现在这张卡（E4、E5）；取消就是丢弃没保存的修改（E6）。

不采用的写法：在 `CharCard.jsx` 里把弹窗改成打开才挂。那样修的是一个宿主，下一个一直挂着弹窗的宿主会再踩一次。

### 4.4 改动面

只动这三个文件，外加执行方补的单测：

- `web/frontend/src/components/EditCardModal.jsx`
- `web/frontend/src/utils/dialogueExamples.js`（新增）
- `web/frontend/src/components/__tests__/EditCardModalGoal.test.jsx`（新增，随本 spec 交付，不改）

不动：三个宿主文件、`utils/objectRows.js`、`ObjectListField.jsx`、store、后端。

## 5. 测试

- 现有测试不用改（§3 第 8 条）。
- 执行方按 `@tdd` 给 `dialogueExamples.js` 的两个函数补单测。名字自定。
- 本地只跑受影响的文件加 `npm test`，再跑一次 §3 第 7 条的 lint 命令。本段不涉及后端和数据库。合并门是分支 CI；合并只做 git 操作。报告里不写本地全量的数字。

## 6. 对账表（独立复核方必须验的清单；变异由复核方跑，并自己再补，放宽和过严两个方向都要有）

| 行为 | 守它的测试 | 让它变红的变异 |
|---|---|---|
| 示例按组拆 | E1、E2 | `textToExamples` 改回按行拆 |
| 组内去空白、丢空行 | E2、E3 | 不 trim 行 |
| 空组丢掉 | E2、E3 | 去掉最后的过滤 |
| 关掉再开重新取卡，取消即丢弃 | E4、E5、E6 | 关掉时不卸载表单（只是隐藏） |
| 开着时不被重渲染冲掉 | E7 | 表单每次渲染都重新挂载 |
| 保存失败内容还在 | E8、`TextPanelEditSave.test.jsx` 的「保存失败（409）」 | 保存失败后重置表单 |

这张表作者没有在这一版上跑过变异，对应关系由复核方验。

## 7. 已知的后果和不在本段的事

- **取消的行为变了**：在角色页的编辑弹窗里改了没保存就关掉，再打开时修改不在了。以前在这个页面上是留着的（因为状态没清），另外两个宿主本来就不留。现在三处一致。
- **已经被拆散的旧示例不会自动合回去**。代码分不出哪两行原本是一组。它们在表单里每行显示成一组，用户可以手动删掉中间的空行合起来。
- **示例本身含空行的，会被当成两组**。真实的卡里有没有这种示例：未核实。
- **保存途中关掉弹窗，失败提示看不到了**。角色页和文本页的关闭没有拦保存中的状态（`components/CharCard.jsx:954`、`components/TextPanel.jsx:805`；`components/MarketCardDetail.jsx:1154` 拦了）。以前在角色页，这条提示会留到下次打开。本段不改。
- **不在本段**：弹窗开着时这张卡在别处变了或页面换了卡，保存写回的是打开时的内容。`main` 上就是这样，本段没有改变它，也不修它；已记进共享记录。
- 阶段下的对话示例在弹窗里仍然看不到、改不了，本段不动，放在第 3 段。

## 8. S0（执行方先做，只读）

1. 逐条复核 §3 的 1–8，报「成立」或新坐标；有一条不成立就停下报告。
2. 把目标检查放到 §1 的路径，在没改代码的分支上跑一次，应为 5 红 3 绿（§1 的表）。对不上就停下报告。
3. 都成立就直接往下做，不必等回复。

## 9. 范围规矩

本段改动面内新发现的问题直接修；会撞车或需要 Shiyu 拍板的才停下报告；不自行记账。本段不做弹出提醒、重新找的接口和按钮、版本冲突的处理、后端改动，发现它们的问题只记在报告里。

## 10. 补充（独立复核，2026-10-10）

复核对象 `c19a28c5`，结论：可以合并。§6 对账表 6 行逐行实测，对应关系全对；§3 的 1–8 成立。

- **两条变异活了下来，都是测试没守住，实现是对的**，已各补一条单测（`utils/dialogueExamples.test.js`）：
  - 分隔符 `/\n\s*\n/` 换成 `/\n\n/`，原有测试全绿。原因：「空行里只有空白也算分组」那条的数据里仍含 `\n\n`。补：`'A\nB\n \nC\nD'` → `['A\nB', 'C\nD']`。
  - 末尾的过滤换成「只留含换行的组」，原有测试全绿。原因：所有数据都是两行一组或空。补：`'只填了一行'` → `['只填了一行']`（只填一行也保留）。
- §7「保存途中关掉弹窗，失败提示看不到了」：复核方实测成立（`87aca0d` 上提示留到下次打开，本分支上不再出现）。
- §3 第 8 条的「原型上 93 个文件 489 条」不含执行方按 §5 补的 `dialogueExamples.test.js`；`c19a28c5` 上是 94 个文件 498 条。
