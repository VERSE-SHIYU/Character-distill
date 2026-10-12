# CSS 死选择器检查进 CI（基线模式）—— CSS 死选择器 PR 2

基线：main `4af4cb06`。作者：Claude（spec）。共享记录：「CSS 死选择器：计划与调查记录」。

## 0. 目标

以后谁写出「匹配不到任何元素」的样式，CI 就变红，提交不了。这里的「匹配不到」是指选择器里的类名在前端代码里已经找不到了。不管是新写的 CSS，还是在 JSX 里删掉或改掉了类名、却留下了 CSS，都算。现有的 393 条死样式记成基线，不动也不报，但基线只减不增。

这次群聊出的事，就是第二种：0ac7149d 换外壳时，JSX 里的 `group-chat-layout` 没了，CSS 里那 16 条留了下来，没有任何东西报错。

不在本 PR 范围：重复定义检查（PR 3）、类名都在但嵌套关系变了的情况（PR 4，运行时检查）。

## 1. 已查实的事实（main `4af4cb06`）

1. CI 的「lint+test / 前端」那一项（`.github/workflows/build.yml:239` 起）先跑 `npx eslint . -c eslint.ci.config.js --quiet`，再跑 `npm test`（即 `vitest run`）。vitest 的配置在 `vite.config.js:78–84`，只排除 `node_modules`、`*.mjs`、`e2e`，所以 `scripts/` 下的 `*.test.js` 会被跑到。
2. 现有测试都显式写 `import { describe, it, expect } from 'vitest'`（例如 `src/utils/card.test.js:1`）。不写的话，CI 的 eslint（`no-undef`）会报错，本轮已实测。
3. 第三方库自己渲染的类：CSS 里只有 antd-mobile 的 `.adm-*`（`global.css:18157–18191` 一带；`MobileTabBar.jsx`、`ArchiveListModal.jsx`、`common/ThemeDrawer.jsx` 引入了 antd-mobile）。CSS 里没有 `recharts-` 的规则。
4. 调研时比较过的工具（详见共享记录）：
   - Biome `noUnusedClasses` 2.5.15：还在实验阶段，有 216 个类误报；
   - PurgeCSS 8.0.0：218 条误报，还漏了 3 个；
   - stylelint-no-unused-selectors：要求每个 CSS 文件旁有同名 JSX，不适用。
   所以用自己写的检查。

## 2. 参考实现（作者已在沙箱跑通）

`docs/specs/artifacts/css-guard-dead-selectors.patch`（sha256 前 16 位 `d4d343d09f32e960`），新增 4 个文件，都在 `web/frontend/scripts/css-guard/`：

| 文件 | 职责 |
|---|---|
| `deadSelectors.js` | 纯函数，不读文件：收集源码里用到的类名和动态前缀（`collectUsage`）、解析 CSS 规则（`parseRules`）、算死类（`deadClasses`）、汇总死选择器（`findDeadSelectors`） |
| `deadSelectors.test.js` | 读文件、调用上面的函数，并和基线比对：扫描器自身 3 条单测 + 全仓 2 条检查（没有新增的死样式；基线没有过期行） |
| `allowlist.json` | 第三方库类名前缀，每条写明是哪个库、在哪用；目前只有 `adm-` |
| `dead-selectors.baseline.json` | 现有的 393 条，格式是 `文件 :: 选择器`，不记行号，因为行号常变；重复的条目按次数保留 |

规则口径：
- 「用到」= 前端源码（`src/` 下的 js/jsx/ts/tsx/mjs、`index.html`，不含测试和 e2e）里任何字符串或模板字面量中出现的词。模板 `${...}` 里面会再递归扫一遍。紧贴 `${}` 且以 `-` 或 `_` 结尾的静态片段（如 `` `theme-${t}` ``）记为动态前缀，以它开头的类都算用到。
- 「死」= 选择器要求的类里有一个没用到。`:not / :is / :where / :has(...)` 和 `[...]` 里的类不算要求；`@keyframes` 里的帧不算规则。
- 已知限制（不处理，写在这里）：
  - 类名如果整个存在变量里（例如 `view.cls`），而这个字符串又没有作为字面量出现在源码里，会被误判为死。出现时把类名写成字面量即可，不要加进基线。
  - 反过来也会漏：类名从 `className` 删掉了，但这个词仍以字符串出现在源码别的字符串里（`data-testid`、文案、日志），检查会判为「用到」而不报错。这类「类名在、但没落到元素上」的情形由 PR 4 的运行时检查管，本检查不管。

本轮实测（沙箱，main `4af4cb06`）：
- 扫描结果和调研时的 Python 脚本逐条一致：385 个不同的选择器，加上重复的一共 393 条；
- `npx vitest run scripts/css-guard`：5 passed，1.4s；
- CI 用的 eslint 配置（`-c eslint.ci.config.js --quiet`）和默认配置都没有报错；
- 变异（每条做完都还原）：
  - 复现原 bug：在 JSX 里把 `group-row-info` 改名 → 红，指向 `global.css:13778 .group-row-info`；
  - 新写一条 `.never-used-xyz` → 红；
  - 删掉一条基线里的死样式 `.admin-error`、基线不改 → 「基线过期」那条红。

## 3. 目标检查

就是 `deadSelectors.test.js` 里的两条全仓检查：
- 在 main 上：没有这个文件，所以没有检查。这也正是目标要补上的缺口。
- 本 PR 上：全绿。
- 复核必须验的变异：上面那 3 条。复核方还要自己再补，放宽、过严两个方向都要有。

## 4. 步骤（一个 commit）

1. **[检查]** 套上参考补丁。在当前 main 上重新跑一次扫描，确认基线条数仍是 393。如果 main 已经前进导致条数不同，停下报告差异，不要直接改基线。然后放入本 spec 和补丁副本。

## 5. 验证

- `npx eslint . -c eslint.ci.config.js --quiet`；`npm test`（全部前端单测，包括新的 5 条）。
- 本地跑第 2 节那 3 条变异，贴输出，跑完还原。
- `git diff --stat origin/main` 只应有：`web/frontend/scripts/css-guard/` 下的 4 个文件、本 spec、补丁副本。
- 开 PR，等分支 CI 的「lint+test / 前端」变绿。

## 6. 补充（独立复核处理，2026-10-12）

复核结论：可合并。复核提出的 4 条及处理：

- **#1（已修）**：`deadSelectors.test.js` 里「没有新写出的死样式」的提示行与 `added` 原非同一次配对——`added` 走多重集差分，提示行又 `found.filter(d => added.includes(key(d)))`，同一 key 有重复时会多列。改为逐条按剩余计数配对，未配上的对象本身即 `added`，key 列表与提示行同源；配对函数合并为一处（`unmatched`），两条检查共用。
- **#2（已补）**：§2 已知限制补第二条（类名仍以字符串出现在别处会被判为「用到」），见该节。
- **#3（移交 PR 4）**：`e2e/navigation.spec.js:130` 断言的 `.chat-topbar-back` 已无组件渲染（现被本检查判死、列入基线）。既存问题，非本 PR 引入；留给 PR 4 接 e2e 时处理。
- **#4（保留）**：`docs/specs/artifacts/css-guard-dead-selectors.patch` 与入库 4 文件内容重复，系 §4 要求的溯源快照；本 spec 已记其 sha256 前缀，漂移可检出，保留。
