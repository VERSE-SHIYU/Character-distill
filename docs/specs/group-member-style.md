# 群聊成员行样式合成一份 + 清掉 `.group-chat-layout` 死样式（CSS 死选择器 PR 1）

基线：main `dc7cd3c`。作者：Claude（spec）。共享记录：「CSS 死选择器：计划与调查记录」。

## 0. 目标

1. 群聊页右侧「成员」标签里的成员条目，长成 Tracy 10-12 11:11 选定的样子：截图 `1-restored.png` 右栏，成员之间有间距和分隔线，名字 15px 加粗，鼠标移上去有淡底色。圆角按 Tracy 11:21 定的统一用 6px。
2. 群聊里「头像 + 文字 + 右侧控件」的行布局和文字列，只写一处。现在有三处在用：建群弹窗的角色卡片、选时期的行、成员条目。以后改行布局只改一处。
3. 群聊页其余部分一个像素都不变：消息区、成员面板宽度、建群弹窗外观。

范围（Tracy 11:21 定）：只合 CSS，三处的 JSX 结构不动（不抽共用组件）。

## 1. 已查实的事实（main `dc7cd3c`，本轮读过的行）

1. 行布局已有一份，但名字带着「create」：
   - `global.css:13812–13820`：
     ```
     /* 「头像 + 文字 + 右侧控件」的行布局。可选卡片与时期行共用；交互另见 .group-create-card */
     .group-create-row { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 6px; font-size: 14px; }
     ```
   - `global.css:13869–13874`：`.group-create-card-info { display: flex; flex-direction: column; min-width: 0; flex: 1; }`
   - 用到的地方：`GroupChatPage.jsx:1505`（`group-create-row group-create-card…`）、`:1511`（`group-create-card-info`）；`GroupPhasePicker.jsx:10`（`group-create-row`）、`:12`（`group-create-card-info`）；`e2e/group-arc-phase.spec.js:105、121、143、170、217` 用 `.group-create-row` 做定位。全仓没有别处。
2. 成员条目现在的样式分在三个地方，互相冲突：
   - `global.css:12782–12785`（生效）：
     ```
     .group-member-item { display: flex; align-items: center; gap: 8px; }
     .group-member-name { font-size: 13px; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-family: var(--font-display); }
     .group-member-identity { display: block; font-size: 11px; color: var(--text-dim); margin-top: 1px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
     .group-member-affinity { display: block; font-size: 11px; color: var(--text-dim); margin-top: 1px; }
     ```
   - `global.css:13561–13590`（从不生效）：`.group-chat-layout .group-member-item / :hover / -info / -name / -identity`。
   - `GroupChatPage.jsx:1400`：`style={{ cursor: 'pointer' }}`，条目可点（`onClick={handleMemberClick}`）。
   - 结构：`GroupChatPage.jsx:1400–1416`：`.group-member-item` > `Avatar` + `.group-member-info`（name、identity×2、affinity）+ `.group-member-filter-btn`。`group-member-*` 全仓只在这里用。e2e 用 `.group-member-item`、`.group-member-name`、`.group-member-identity` 定位（`group-arc-phase.spec.js:159、175、194、202`），这些类名都保留。
3. `group-chat-layout` 自 `0ac7149d`（07-04 群聊换外壳）起不在任何 JSX 里。群聊页根节点是 `GroupChatPage.jsx:864` 的 `"chat-view panel group-chat-page"`。
4. `global.css` 里含 `.group-chat-layout` 的规则共 16 条（`:13290` 是 `.private-chat.group-chat-layout`，不以 `.group-chat-layout` 开头，按「含」计）：`:13290`、`:13306`、`:13314`、`:13324–13325`（一条规则两个选择器）、`:13500`、`:13547`、`:13553`、`:13561`、`:13569`、`:13572`、`:13576`、`:13581`、`:13594`（单独一个 `@media (max-width: 900px)` 块）、`:13597`（单独一个 `@media (max-width: 768px)` 块）、`:18713`（在一个大的 `@media (max-width: 768px)` 块里，块内还有别的规则）。
5. 截图（本地 10-12，脚本在仓库外 `temp/css-dead/`）：把 `.group-chat-layout` 换成 `.group-chat-page` 注入后，成员标签变好（Tracy 选定）；消息区变差（手机上头像贴左边缘，桌面多一层底色）。所以成员相关的那 5 条取值保留，其余 11 条删掉。
6. `Avatar` 的字号由自身 `size` 算（`common/Avatar.jsx:20` `fontSize: size * 0.42`），不受行的 `font-size` 影响。

## 2. 改法

1. **行布局改成不带场景的名字**（一处定义，三处用）：
   - `.group-create-row` → `.group-row`；`.group-create-card-info` → `.group-row-info`。声明不变（圆角 6px）。注释改成「群聊里『头像 + 文字 + 右侧控件』的行布局：建群卡片、时期行、成员条目共用；各自的交互另写」。
   - JSX 的 4 处、e2e 的 5 处跟着改名。`.group-create-card`（建群卡片的交互）、`.group-create-card-name` 不动。
2. **成员条目只写自己特有的部分**。在 `global.css:12782` 那里，删掉原来的 `.group-member-item / -name / -identity` 三行，换成下面这组；`.group-member-affinity` 原样保留：
   ```
   .group-member-item { border-bottom: 1px solid var(--glass-border); transition: background 0.15s; cursor: pointer; }
   .group-member-item:hover { background: rgba(var(--accent-rgb), 0.04); }
   .group-member-name { display: block; font-size: 15px; font-weight: 600; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-family: var(--font-display); }
   .group-member-identity { display: block; font-size: 13px; color: var(--text-dim); margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
   ```
   - 取值来源：Tracy 选定截图的实际层叠结果，即基础规则加上 `.group-chat-layout` 那 5 条覆盖后的值。圆角改用 6px（行布局里的）。
   - 身份行：原规则写了两行截断，但基础规则的 `nowrap` 让它实际只显示一行，截图里也是一行，所以按实际效果写成单行。
   - `cursor: pointer`：条目可点。原死规则里写的是 `default`，和实际行为不符。
3. **JSX**：`GroupChatPage.jsx:1400` 改成 `className="group-row group-member-item"`，删掉 `style={{ cursor: 'pointer' }}`；`:1402` 改成 `className="group-row-info"`（不再有 `.group-member-info`）。
4. **删掉全部 16 条 `.group-chat-layout` 规则**。`:13594`、`:13597` 那两个 `@media` 块删完就空了，连块一起删；`:18713` 只删这一条，块里其余规则不动。

## 3. 目标检查（第 1 个 commit 先放，在 main 上必须红）

新建 `web/frontend/e2e/group-member-style.spec.js`，复用 `e2e/group-arc-phase.spec.js` 的 `page.route` 模拟方式（不碰真后端、不用账号）。

- G1 成员条目：打开一个有 2 个 AI 成员的群，切到「成员」标签。`.group-member-item` 的计算样式：`padding` = `8px 10px`，`border-bottom-width` = `1px`，`gap` = `10px`，`border-radius` = `6px`，`cursor` = `pointer`；元素上没有内联 `style` 属性。
- G2 名字：`.group-member-name` 的 `font-size` = `15px`，`font-weight` = `600`。
- G3 身份：`.group-member-identity` 的 `font-size` = `13px`，`margin-top` = `2px`，`white-space` = `nowrap`。
- G4 悬停：`hover()` 后条目的 `background-color` 不是透明。
- G5 一处定义：读 `src/styles/global.css` 文本：
  - `group-chat-layout`、`group-create-row`、`group-create-card-info` 都出现 0 次；
  - 选择器 `.group-row {`、`.group-row-info {` 各出现 1 次；
  - 在任何以 `.group-member-item` 结尾的选择器里，`display`、`padding`、`gap`、`align-items` 都不出现，也就是布局只由 `.group-row` 管。
- G6 其余不变：在 main 上先跑一次，读出实际值写成测试常量（不用截图基准，基准图分平台，CI 上对不上）。
  - 桌面 1280×900、手机 390×844 下，`.private-chat-body`、`.group-chat-messages-area`、`.group-right-member-list` 的 `padding`、`gap`、`background-color`、`width`、`display`，等于常量；
  - 建群弹窗第 1 步的角色卡片、第 2 步的时期行，它们的 `padding`、`gap`、`border-radius`、`font-size`，以及文字列的 `flex-grow`、`min-width`，等于常量。

## 4. 步骤（每步一个 commit）

1. **[测试]** 放入本 spec 和目标检查（G6 的常量在 main 上读出）。在 main 上：G1–G5 红，G6 绿。
2. **[样式]** 按第 2 节改 `global.css`、`GroupChatPage.jsx`、`GroupPhasePicker.jsx`，以及 `e2e/group-arc-phase.spec.js` 的定位类名。G1–G6 全绿。

## 5. 验证

- 目标检查：main 上 G1–G5 红；PR 上全绿。
- 受影响的测试：`npm test`；`npx playwright test e2e/group-member-style.spec.js e2e/group-arc-phase.spec.js`。
- `git diff --stat origin/main` 只应有：`global.css`、`GroupChatPage.jsx`、`GroupPhasePicker.jsx`、`e2e/group-arc-phase.spec.js`、新测试、本 spec。
- 人工截图（不进仓库，放 `E:/Study/ANU/ai coding/temp/css-dead/pr1/`），改前、改后各一组，给 Tracy 看：
  - 群聊页桌面：成员标签开、关各一张；
  - 群聊页手机一张；
  - 建群弹窗第 1、2 步各一张。

## 6. 补充：独立复核（R1）处理

独立复核给出 S1–S5、T1，处理如下（只动测试与 spec，不动 CSS/JSX）：

- **S1（spec 错）**：G6 原来点名的 `.group-members-panel` 在 JSX 里不存在（全仓 0 命中；被删的死样式打的是同名死选择器）。断言改用真实渲染的容器 **`.group-right-member-list`**（`GroupChatPage.jsx:1384`），量取成员列表而非幽灵元素。§1.4 的计数口径同步改为「含 `.group-chat-layout`」。
- **S2（判据不全，M7 漏网）**：G5 从「禁止 `display/padding/gap/align-items` 四词」改成**白名单**——以 `.group-member-item` 结尾的选择器只允许 `border-bottom`、`transition`、`cursor`、`background`，出现别的属性即红。补上 `border-radius`/`font-size` 等被漏掉的布局属性。
- **S3（判据不全，M8 全绿）**：G1 加断言：成员条目、建群第 1 步卡片、第 2 步时期行三处的计算样式都是 `display: flex`、`align-items: center`。原判据只查「成员条目没重声明布局」，没查「共享行布局确实提供了布局」，把 `.group-row` 改成 `display: block` 也不红。
- **S4（判据过宽）**：G4 不再只判「背景非透明」，改为断言悬停底色的**具体计算值** `rgba(123, 97, 255, 0.04)`（= head 默认主题 `--accent-rgb: 123, 97, 255` 下 `rgba(var(--accent-rgb), 0.04)` 的计算值，读出后写死）。
- **S5（记录，不改）**：G6 的宽度用硬编码计算值 + ±1px 容差。**e2e 不进 CI**（CI `build.yml` 只跑 `npm test`/vitest），故宽度常量只在本地有效；若日后把 e2e 纳入 CI，需放宽宽度容差或改断相对关系。
- **T1（测试写法）**：删掉测试里为旧类名留的后备选择器（`.group-create-row`、`.group-create-card-info`），只认 `.group-row` / `.group-row-info`。代价是失去「同一份测试也能在 main 上跑」的能力（G5 的读 CSS 文本断言本就只在 head 上有意义）。
