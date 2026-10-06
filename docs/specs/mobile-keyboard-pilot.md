# Spec：手机键盘适配重构 · 第一段（键盘层最小集 + 角色聊天）

## 状态（2026-10-06）

- **Shiyu 已过目并同意（2026-10-06）。由本地实现，作者审计。**
- 本文件取代同名的上一版（那一版把三个模块、17 条测试、12 个变异压在一次交付里，且没有目标检查；按 `rework-lessons.md` 第 2、9、10、7 条重写）。
- 计划见 `mobile-keyboard-plan.md`。后续段落见文末。

## 头部

- 前置 spec：无。基线：main `15b0b7c`，全部坐标读自该提交。
- 实现分支：`feat/keyboard-native-s1`（本地在其上提交）。
- 已拍板（Shiyu，2026-10-06）：顺着 Safari；ChatUI 留白式；试点 = 角色聊天；默认关闭的开关；部署到服务器后真机验。

## 一、目标检查（先于一切实现）

**目标**：角色聊天页点输入框、键盘弹起后，最后一条消息贴着输入栏，输入栏在键盘上方，消息区不是空白。

**检查方式**：网址带 `?kbdcheck=1` 时，页面在输入框聚焦后 800ms 和 2000ms 各量一次，把结果显示在屏幕顶部。Shiyu 截图即为结果。

| 项 | 判据 |
|---|---|
| C1 输入栏在键盘上方 | 输入栏的上下边都落在可视区内（可视区 = `visualViewport` 的 `offsetTop` 到 `offsetTop + height`） |
| C2 最后一条消息贴着输入栏 | 0 ≤ 输入栏顶边 − 最后一条消息底边 ≤ 列表底部留白 + 24px |
| C3 消息区不是空白 | 最后一条消息至少有 20px 落在可视区内 |
| 附带显示（不判对错） | 页面被 Safari 推上去的距离；可视高度；窗口高度 |

阈值是初值，首跑后如需调整，写进本文件「补充」。

**检查本身要先被证明有效**：开关关闭（现有逻辑）时跑一次，预期 C2 或 C3 不通过，与 Shiyu 看到的空白一致。如果现状下三项全过，说明检查看不出问题，**停下报告，不继续实现**。

**顺序**：第 1 个提交只有目标检查；第 2 个提交才是键盘层。部署后同一台手机先关开关跑、再开开关跑，两张截图就是改前改后。

## 二、目标与非目标

| # | 目标 | 怎么验 |
|---|---|---|
| G1 | 开关打开时，目标检查 C1–C3 全过 | 真机截图 |
| G2 | 过程顺滑，页面没有被弹开再拽回 | 真机，Shiyu 主观判断，对照 `web.telegram.org/a` |
| G3 | 开关关闭时角色聊天与现在一致；其他页面不受影响 | 单测 + Playwright |

非目标（各在后续段落）：消息列表组件与回到底部按钮；聊天骨架；群聊、私信、登录页接入；删除旧逻辑；顶栏固定。

## 三、行为清单

| # | 行为 | 生效条件 |
|---|---|---|
| B1 | 开关：网址带 `?kbd=native` / `?kbd=legacy` 时写入本地存储键 `kbd_mode`，之后读该键；默认 `legacy`。`?kbdcheck=1` / `0` 同理，键 `kbd_check` | 始终 |
| B2 | 页面声明键盘交给浏览器：挂载时 `<html data-kbd="native">` 并清掉 `--vvh`，卸载时移除属性 | 开关 = native 且手机宽度 |
| B3 | 有 `data-kbd="native"` 时，旧 hook 不写 `--vvh`、不拽页面、不派发 `vvchange` | 同上 |
| B4 | 有 `data-kbd="native"` 时 `body` 不固定（其余铺满和禁止滚动的规则仓库已有，不重复写） | 同上 |
| B5 | 页面内的文字输入框聚焦时给 `<body>` 加 `kbd-focusing`，失焦后下一个事件循环移除 | 同上 |
| B6 | `kbd-focusing` 期间：底部安全区留白归零；仅 iPhone 上，标了 `data-kbd-lift` 的列表其第一个子元素顶部加 75% 屏高留白 | 同上 |
| B7 | 聚焦时把列表立即滚到底（不带动画） | 同上 |
| B8 | 非 Safari 的内嵌浏览器：聚焦后 300ms、1000ms 各把输入栏滚进视野一次；iOS 上失焦时页面滚回顶部。iPhone 上键盘收起但输入框未失焦时，让它失焦 | 同上 |

## 四、已查实的约束（S0 逐条复核，任一条不成立就停下报告）

| # | 事实 | 坐标 @ `15b0b7c` |
|---|---|---|
| F1 | 角色聊天根节点是 `div.chat-view.chat-area`；列表是 `div.chat-messages`，子元素直接是各条消息，末尾一个空的占位 `div` | `web/frontend/src/components/ChatArea.jsx:499`、`:674`、`:691`、`:751` |
| F2 | 贴底靠 `useAutoScroll`（消息变化、`vvchange`、滚动三处）；本页另有一份键盘监听 | `ChatArea.jsx:395`、`:397-408`；`src/hooks/useAutoScroll.js:11`、`:18`、`:27` |
| F3 | 列表设了平滑滚动，所以 B7 必须显式指定不带动画 | `src/styles/global.css:4558` |
| F4 | 旧 hook 全站生效：写 `--vvh`、拽页面、派发 `vvchange` | `src/utils/useVisualViewport.js:25`、`:28-30`、`:32-34`；`src/App.jsx:109` |
| F5 | 外壳高度 `var(--vvh, 100dvh)`，`--vvh` 不存在时回落 | `global.css:18575-18577` |
| F6 | `html, body, #root` 已是铺满并禁止滚动；手机端另把 `body` 固定 | `global.css:905`；`:18568-18572` |
| F7 | 手机断点 768，已有 `useIsMobile` | `src/hooks/useIsMobile.js:3` |
| F8 | 没有功能开关工具和读网址参数的先例；本地存储有布尔开关先例 `affinity_enabled`，现有键里没有 `kbd_` 开头的 | 现有键：`affinity_enabled affinity_open auth_token charsim-font-level distill_tasks nav_* tts_voice` |
| F9 | 只有 main 上的提交会构建镜像；部署可只选新加坡；支持按旧镜像回滚；没有测试环境 | `.github/workflows/build.yml:272`；`deploy.yml:20-35`；`DEPLOY.md` |
| F10 | 前端 lint + vitest 是分支合并门；Playwright 不在 CI | `build.yml:238-267`；`web/frontend/vite.config.js:83` |

### 这条路径上已有的机制

| 机制 | 本段怎么处理 | 说明 |
|---|---|---|
| 旧 hook（F4） | 加一处判断（B3） | 临时，全部页面迁完后随旧逻辑删除 |
| `useAutoScroll` 的 `vvchange` 监听 | 不改 | native 时旧 hook 不派发，它自然不触发 |
| `ChatArea` 自己的键盘监听（F2） | 不改 | **未核实**：native 时它仍会在可视高度变小时把列表滚到底（带动画），与 B7 方向相同，是否干扰看目标检查结果；第二段换消息列表组件时删除 |
| `body` 固定（F6） | native 时取消（B4） | **未核实**是否必要：ChatUI 的 `body` 不固定。只能真机看 |

### 全仓碰视口的地方（跨模块规则，贴原文）

`git grep -n -E "visualViewport|vvchange" 15b0b7c -- web/frontend/src web/frontend/index.html`
```
src/components/ChatArea.jsx:397:  // Mobile: scroll messages to bottom when keyboard opens (visualViewport resize)
src/components/ChatArea.jsx:399:    if (!window.visualViewport) return
src/components/ChatArea.jsx:401:      if (window.visualViewport.height < window.screen.height * 0.8) {
src/components/ChatArea.jsx:406:    window.visualViewport.addEventListener('resize', handler)
src/components/ChatArea.jsx:407:    return () => window.visualViewport.removeEventListener('resize', handler)
src/hooks/useAutoScroll.js:29:    window.addEventListener('vvchange', onVVChange)
src/hooks/useAutoScroll.js:30:    return () => window.removeEventListener('vvchange', onVVChange)
src/utils/useVisualViewport.js:4: * Track window.visualViewport height and expose it as --vvh CSS variable.
src/utils/useVisualViewport.js:5: * Also dispatch a 'vvchange' custom event so message containers can
src/utils/useVisualViewport.js:8: * Only active when visualViewport is available (mobile browsers).
src/utils/useVisualViewport.js:13:    const vv = window.visualViewport
src/utils/useVisualViewport.js:16:    const SYNC_EVENT = 'vvchange'
src/utils/useVisualViewport.js:31:      // Dispatch vvchange only when keyboard opens (height shrinks)
```
本段之后新增碰视口的只有 `src/keyboard/` 目录。

### 起环境与样本

- 真机验收：合并进 main → 构建 → 部署新加坡（`sg-only`）。开关默认关，线上用户不受影响。
- Playwright 起 vite 占 7860，原生后端也用 7860，跑之前先停（`web/frontend/playwright.config.js:13-16`、`start_all.bat:21`）。本段不加新依赖。
- 样本只有 Shiyu 的 iPhone 14 Pro（iOS 26.6.2），Safari 标签页与主屏幕各一遍。安卓与内嵌浏览器无设备，B8 照 ChatUI 原样搬，**未验证**。
- B6 的留白规则只在 iPhone 上生效，模拟里测不到。

## 五、改动

| 文件 | 内容 | 它消掉什么 / 何时删 |
|---|---|---|
| 新增 `src/keyboard/keyboardMode.js` | B1 | 迁移期开关；全部迁完后删 |
| 新增 `src/keyboard/goalCheck.js` | 第一节的检查与屏幕显示 | 每一段的验收都用它；全部迁完后删 |
| 新增 `src/keyboard/useNativeKeyboardPage.js` | B2，返回是否生效 | 长期保留：页面接入键盘层的入口 |
| 新增 `src/keyboard/useKeyboardFocus.js` | B5、B7（通过回调）、B8；用事件委托监听根节点 | 长期保留。不改 `ChatInputBar` |
| 新增 `src/keyboard/keyboard.css` | B4、B6 | 长期保留 |
| `src/main.jsx` | 引入 `keyboard.css` | — |
| `src/utils/useVisualViewport.js` | `sync` 开头加一处判断（B3） | 临时；消掉「两套逻辑同时动视口」 |
| `ChatArea.jsx` | 调用两个 hook；根节点挂 ref；列表加 `data-kbd-lift`；聚焦回调里把列表滚到底 | 第三段抽出聊天骨架后，这几行收进骨架，页面里不再有键盘代码 |

以后新页面接入要改几处：一处（调用 `useNativeKeyboardPage` 与 `useKeyboardFocus`）；第三段之后是零处，换上骨架即可。
以后加一条键盘规则要改几处：一处（`src/keyboard/`）。

## 六、出处对照

| 行为 | 出处（`@chatui/core@3.8.0`，MIT，`https://github.com/alibaba/ChatUI`） |
|---|---|
| B4 | `es/components/Chat/style.less:2` 起：`html`、`body`、`#root` 铺满，`body` 只禁止滚动 |
| B5 | `es/components/Composer/index.js:225-235`（`handleInputFocus`）、`:236-247`（`handleInputBlur`） |
| B6 | `Chat/style.less:39-41`（`--safe-bottom: 0px`）、`:44-48`（`@supports (-webkit-touch-callout: none)` 下 `margin-top: 75vh`） |
| B7 | `es/components/Chat/index.js:59-65`（聚焦时 `scrollToEnd`） |
| B8 | `es/components/Composer/riseInput.js:1-48`；`es/utils/ua.js:4`；`Composer/index.js:130-156` |

照搬的文件在文件头注明出处与 MIT 许可。与 ChatUI 的一处差异：它的留白加在列表内的内容容器上；本仓列表没有内容容器（F1），加在第一个子元素上，效果相同，**未核实**。

## 七、测试（与风险相称）

- **目标检查**：见第一节。它守 G1。
- **单测**（vitest，进 CI，约 9 条）：开关读写与默认值；`useNativeKeyboardPage` 在 native 且手机时加属性、卸载移除，否则不加；`useKeyboardFocus` 的加类、移除、只认文字输入框、聚焦回调被调用；旧 hook 在有属性时三件事都不做、没有时照旧。
- **一把锁**：「native 时旧 hook 不动作」是本段唯一跨模块的规则。实现方预跑一个变异（去掉那处判断），确认对应单测变红，输出贴进报告；作者审计时亲手再跑。
- **Playwright**（2 条，不在 CI）：开关关闭时无 `data-kbd`、列表进入后贴底；开关打开时有属性，点输入框后 `body` 有 `kbd-focusing`、失焦后没有。
- 其余不设变异、不设调用点矩阵。

本地只跑受影响文件加 `npm test`；本段不碰后端，不起 PG。合并前提是分支 CI 绿；合并只做 git 操作，合并后不等 main 的 CI。

## 八、执行（本地实现，作者审计；一步一报，前一步审计过了才给下一步）

| 步 | 谁 | 做什么 | 完成判据 |
|---|---|---|---|
| 1 | 本地 | 拉分支，`Test-Path docs/specs/mobile-keyboard-pilot.md`，S0 复核 F1–F10 | 逐条写明成立 / 不成立 |
| 2 | 本地 | **提交 1：只做目标检查**——开关读写（B1）、`goalCheck.js`、在角色聊天页接上检查、对应单测。推分支 | 受影响的测试与 `npm test` 全绿 |
| 3 | 作者 | 审计提交 1：**先看它是否真能量出第一节的三项**，再对照本文件；逐文件写结论 | 每个问题注明是实现偏离还是本文件设计错 |
| 4 | 本地 | 提交 2：键盘层其余部分（B2–B8）与接线、测试；预跑那一个变异并贴输出。推分支 | 全绿；变异被打红 |
| 5 | 作者 | 审计提交 2；测试和那个变异亲手再跑一遍 | 同第 3 步 |
| 6 | Shiyu | 分支 CI 绿后合并，部署新加坡 | 开关关闭时线上无变化 |
| 7 | Shiyu | 手机打开 `…?kbdcheck=1`，进角色聊天点输入框，截图（基线） | 预期 C2 或 C3 不通过；若全过则停 |
| 8 | Shiyu | 再打开 `…?kbd=native`，同样操作，截图 | C1–C3 全过；顺滑程度主观判断 |

改动面内的小问题直接修并写明；事实不成立、需要改判据、接口或增减范围时停下报告，不自行记账。
skill：第 2、4 步 `tdd`；第 3、5 步 `code-review-and-quality`；各一个。
退出试点：手机打开 `…?kbd=legacy&kbdcheck=0`。

## 九、未核实 / 未验证

1. 这套做法在 iOS 26 上是否成立：正是本段要回答的问题。
2. `body` 不固定是否必要；`ChatArea` 自带的键盘监听是否干扰；留白加在第一个子元素上是否等效（均见上文标注）。
3. 列表手机端底部有 80px 留白（`global.css:18621-18626`），本段不动，所以 C2 的判据把它算了进去。开关打开后「贴着」的实际间距是否太大，由 Shiyu 看截图后定，再决定是否在第二段改。
4. 安卓与内嵌浏览器。

## 十、后续段落（各自单独一份 spec，前一段过了目标检查才开下一段）

- **1b 顶栏留在原位（需 Shiyu 拍板是否要做）**：已读清 ChatUI 的 `--viewport-top`（`es/components/Composer/viewportTop.js`）：聚焦后逐帧量出页面被推上去的距离并写成变量，失焦归零；ChatUI 自己的样式不使用它，是留给应用把顶栏移回视野用的。用它可以让顶栏在键盘弹起时留在屏幕顶部，但它是推上去之后才量到的，顶栏会晚一拍归位，做不到与键盘完全同步。
- **2 消息列表组件**：`use-stick-to-bottom`，贴底与回到底部按钮；删掉 `ChatArea` 自带的键盘监听和平滑滚动依赖。
- **3 聊天骨架 + 群聊接入**；之后私信、登录页；最后删旧逻辑与开关。

## 补充 1（2026-10-06）：提交 1（`e095dce`）审计

审计顺序：先看目标检查能否量出第一节的三项、结果能否在手机上被看到，再对照本文件。

**亲手跑的结果**：受影响的 3 个测试文件 28/28 通过；CI 的 lint 命令对改动文件退出码 0。对三项判据做了 9 个变异（放宽、收紧两个方向）：6 个被打红，3 个存活（见 P3）。

**逐文件结论**

| 文件 | 结论 |
|---|---|
| `src/keyboard/keyboardMode.js` | 通过。与 B1 一致 |
| `src/keyboard/keyboardMode.test.js` | 通过 |
| `src/keyboard/goalCheck.js` | 判据通过：C1–C3 的公式、800ms / 2000ms、留白实时读取与第一节逐项一致。显示部分有 P1、P2 |
| `src/keyboard/goalCheck.test.js` | 有缺口，见 P3 |
| `src/components/ChatArea.jsx` | 通过。开关关闭时只多一个 ref 和一个不生效的 effect。P5 |
| `src/components/__tests__/ChatAreaGoalCheckWiring.test.jsx` | 保留。它守的是选择器：选择器失效会让真机检查只显示「找不到」，白跑一次部署 |

**问题**

| # | 问题 | 属于 | 根因 | 处理 |
|---|---|---|---|---|
| P1 | 结果浮层是 `position: fixed; top: 0`。iPhone 上页面被键盘推上去时，fixed 元素跟的是布局视口，会被一起推出可视区——恰好是要验证的那个状态下看不到结果。主屏幕模式下还会被灵动岛挡住 | 本文件设计缺口（第一节只写了「屏幕顶部」） | 把「屏幕顶部」等同于「布局视口顶部」，正是本项目要处理的同一个坑 | 浮层的 `top` 每次写入时取 `visualViewport.offsetTop`，并在可视视口 `scroll` / `resize` 时更新；顶部加 `env(safe-area-inset-top)` 内边距。**未核实**：iOS 26 上 fixed 元素是否确实被推出，修法对两种情况都成立 |
| P2 | 「推上去」只显示 `visualViewport.offsetTop`。ChatUI 是用根节点的位置量这个距离的（`viewportTop.js`），说明页面被推上去也可能表现为文档滚动，此时 `offsetTop` 是 0，读数会误导 | 本文件设计缺口（没定义「推上去的距离」） | 同上：没分清两种视口 | 同时显示 `offsetTop`、`pageTop`、`scrollY` 三个数。判据不变：C1–C3 用的矩形与可视区同在布局视口坐标里，两种情况都成立 |
| P3 | 测试里的 `offsetTop` 全是 0，变异「可视区不加 offsetTop」存活。另有两个边界变异存活：C1 下边取等号、C3 恰好 20px | 实现（测试） | 测试样本没覆盖页面被推上去这个核心场景 | 必须补一条 `offsetTop > 0` 且忽略它会翻转结论的用例。两个边界用例可补可不补 |
| P4 | `isTextEntry` 只认 `textarea` 和 `input[type=text]`。提交 2 的 B5 也要用「是否文字输入框」这个判断 | 提交 2 的要求 | 防止同一条规则写两处 | 提交 2 把它抽成 `src/keyboard/` 下唯一的一个函数，并覆盖 password、email、search、tel、url、number（登录页要用） |
| P5 | 网址参数在 `ChatArea` 挂载时才处理（执行方已声明） | 可接受的临时偏离 | — | 提交 2 移到应用入口，只此一处。**未核实**：带参数打开后经过登录跳转，参数是否还在 |

**结论**：判据实现正确；P1–P3 修完才能用于真机。修正只动 `goalCheck.js` 的显示部分和它的测试，不改判据、不改接口。

## 补充 2（2026-10-06）：修正提交（`2c5de65`）审计，以及键盘层提交发出前对 B2–B8 的复查

### 修正提交审计

亲手跑：受影响测试 37/37；lint 退出码 0。变异 7 个全部被打红：上一轮存活的 3 个，加上 P1 的 3 个（写入时不同步 `top`、`top` 恒为 0、不监听视口 `scroll`）和 P2 的 1 个（`scrollY` 恒为 0）。

| 文件 | 结论 |
|---|---|
| `src/keyboard/goalCheck.js` | 通过。P1、P2 按补充 1 修正；判据未动。字段 `pushedUp` 改名为 `offsetTop` 属模块内部，接受 |
| `src/keyboard/goalCheck.test.js` | 通过。P3 的翻转用例与两个边界用例已补 |

浮层在各状态下的位置（本应在提交 1 之前就写出来）：

| 状态 | 浮层在哪 |
|---|---|
| 键盘收起 | `top: 0`，加安全区内边距，可见 |
| 开关关闭、键盘弹起（页面被旧逻辑钉住，`visualViewport.offsetTop` = 0） | `top: 0`，可见 |
| 开关打开、键盘弹起、页面以 `visualViewport.offsetTop` > 0 的方式被推上去 | `top` = `offsetTop`，落在可视区顶端 |
| 开关打开、键盘弹起、页面以文档滚动（`scrollY` > 0）的方式被推上去 | fixed 相对布局视口，`top: 0` 即可视区顶端 |

未覆盖的一种情况，**未核实**：Safari 如果滚动的是 `body` 或 `#root` 这类禁止滚动的容器，三个读数都会是 0。此时 C1–C3 仍然成立（它们比较的是矩形与可视区），只是「怎么被推上去的」读不出来；真机若出现三数全 0 而画面确实上移，报告后再加读数。

### 对 B2–B8 的复查（做法：状态表、有歧义的词落到具体属性、核心用例逐条点名）

**状态表**

| 状态 | `<html data-kbd>` | `--vvh` | `body` 定位 | `body.kbd-focusing` | 列表首个子元素的顶部留白 |
|---|---|---|---|---|---|
| 开关关闭 | 无 | 旧 hook 写入 | `fixed`（现状） | 无 | 无 |
| 开关打开，键盘收起 | `native` | 无，外壳回落 `100dvh` | `relative` | 无 | 无 |
| 开关打开，文字输入框聚焦 | `native` | 无 | `relative` | 有 | iPhone：`75vh`；其他：无 |
| 开关打开，离开角色聊天 | 无 | 下一次视口事件时旧 hook 重新写入 | `fixed` | 无 | 无 |

**对正文的更正与补全**

| # | 正文写法 | 问题 | 更正 |
|---|---|---|---|
| D1 | B4「`body` 不固定」 | 没写成具体属性 | `html[data-kbd="native"] body { position: relative; inset: auto; }`——回到基础规则的值（`global.css:917`），覆盖手机端的 `fixed`（`:18568-18572`） |
| D2 | B5「失焦后下一个事件循环移除」 | 漏了两种情况 | 照 ChatUI `Composer/index.js:226`：聚焦时先取消尚未执行的移除（焦点从一个输入框换到另一个时类不能闪掉）；页面卸载时如仍聚焦，移除该类 |
| D3 | B6 | 没写选择器 | `html[data-kbd="native"] body.kbd-focusing { --safe-bottom: 0px; }`；`@supports (-webkit-touch-callout: none) { html[data-kbd="native"] body.kbd-focusing [data-kbd-lift] > :first-child { margin-top: 75vh; } }` |
| D4 | B7「聚焦时把列表立即滚到底」 | 没写顺序和调用 | 必须在 `kbd-focusing` 加上之后执行（留白生效后 `scrollHeight` 才是新的）；调用 `list.scrollTo({ top: list.scrollHeight, behavior: 'instant' })`（列表设了平滑滚动，`global.css:4558`） |
| D5 | B8「非 Safari 的内嵌浏览器」 | **写错了范围** | 照 ChatUI `riseInput.js` 与 `utils/ua.js:4` 的原判断：iOS 且 UA 含 `Safari/` → 不处理；**其余全部**（含所有安卓浏览器、iOS 内嵌浏览器）→ 聚焦后 300ms、1000ms 各对输入栏调用 `scrollIntoView(false)`。失焦时的 `document.body.scrollIntoView()` 只在 iOS 且 UA 不含 `Safari/` 时执行。iOS 12 及以下的分支不搬；ArkWeb / AliApp 的分支不搬 |
| D6 | B8「键盘收起但未失焦时让它失焦」 | 没写判据 | 仅 iOS：`visualViewport` 的 `resize` 事件里，若仍聚焦且 `visualViewport.height >= window.innerHeight`，对该输入框调用 `blur()`（`Composer/index.js:130-156`） |

**一处新发现的风险，未核实**：开关打开、键盘开着时如果来了新消息，现有的 `useAutoScroll.js:11` 会调用原生 `scrollIntoView`（带动画），它可能连页面一起滚。本段不改（第二段换消息列表组件时消除）。真机验收时顺手看一眼：键盘开着发一条消息，页面是否跳动；跳了就记下来，不算本段不通过。

**键盘层提交必须点名的用例**

- 开关与声明：native 且手机宽度时加 `data-kbd` 并清掉 `--vvh`，卸载移除；legacy 或桌面宽度不加。
- 锁：有 `data-kbd="native"` 时旧 hook 不写 `--vvh`、不调 `scrollTo`、不派发 `vvchange`；预跑变异「去掉该判断」必须变红。
- 聚焦类：聚焦加、失焦移除；**从一个文字输入框换到另一个时不闪掉**；卸载时移除；非文字控件不触发。
- 顺序：聚焦回调执行时 `kbd-focusing` 已经在 `body` 上。
- UA 三种：iOS Safari 的 UA 不滚；iOS 微信的 UA 在 300ms、1000ms 各滚一次；安卓 Chrome 的 UA 同样滚。
- P4：「是否文字输入框」只有一个函数，`goalCheck.js` 与聚焦逻辑共用；text、password、email、search、tel、url、number 与 `textarea` 为真，checkbox、button 为假。
- P5：网址参数在应用入口处理一次，`ChatArea` 不再调用。

## 补充 3（2026-10-06）：键盘层提交（`59f8577`）审计

**亲手跑**：受影响的 7 个测试文件 76/76 通过；CI 的 lint 命令对改动文件退出码 0。对核心规则做了 17 个变异（放宽、收紧两个方向）：15 个被打红，2 个存活（见 P7、P8）。执行方预跑的那一个锁变异我复跑，同样变红。

**对着状态表走了一遍**：开关关闭时，`useKeyboardFocus` 不挂监听、`data-kbd-lift` 属性没有规则命中、旧 hook 照旧，角色聊天行为不变。旧 hook 的判断放在 `rafId = null` 之后（`useVisualViewport.js:21-24`），离开角色聊天后它能恢复工作。`keyboard.css` 的选择器优先级高于手机端固定 `body` 的规则，能覆盖。

**逐文件结论**

| 文件 | 结论 |
|---|---|
| `src/keyboard/textEntry.js` 及测试 | 通过。P4 已落实，全仓只此一处 |
| `src/keyboard/useNativeKeyboardPage.js` 及测试 | 通过 |
| `src/keyboard/keyboard.css` | 通过。与 D1、D3 一致；效果只能真机看 |
| `src/keyboard/useKeyboardFocus.js` | B5、B7 的顺序正确。有 P6、P7 |
| `src/keyboard/useKeyboardFocus.test.jsx` | 缺 P7 的用例 |
| `src/utils/useVisualViewport.js` 及测试 | 通过 |
| `src/main.jsx` | 通过。P5 已落实；入口处读本地存储与现有的 `utils/theme.js:7` 同等暴露，没有新增故障方式 |
| `src/components/ChatArea.jsx` | 通过。有 P8、P9 |
| `src/components/__tests__/ChatAreaGoalCheckWiring.test.jsx` | 缺 P8 的断言 |
| `src/keyboard/goalCheck.js` | 通过，改为共用 `isTextEntry` |

**问题**

| # | 问题 | 属于 | 根因 | 处理 |
|---|---|---|---|---|
| P7 | 「键盘收起但未失焦时让它失焦」的判据是 `visualViewport.height >= window.innerHeight`，每次取当前的 `innerHeight`。ChatUI 的原实现不是这样：它在安装时记下窗口高度 `winHeight`，只在 `innerHeight` 变大时更新，然后比较 `visualViewport.height >= winHeight`（`Composer/index.js:134-146`）。iOS 26 上键盘弹起过程中 `innerHeight` 会短暂变小（svedit PR #352 的真机记录），按现在的写法这一瞬间条件可能成立，输入框被失焦，**键盘刚弹起就自己收回去** | **本文件补充 2 的 D6 写错**，执行方照做 | 我转述了源码而没有照抄，丢掉了「只增不减的窗口高度」这个关键细节（经验第 13 条） | 照抄原实现：`winHeight` 安装时取 `window.innerHeight`，处理函数里若 `innerHeight` 更大则更新，再比较。补两条用例：键盘开着（可视高度小于 `winHeight`）时触发 `resize` 不失焦；`innerHeight` 短暂变小到等于可视高度时不失焦。前一条同时消灭存活的变异「键盘还开着也让它失焦」 |
| P6 | B8 的滚入视野打在聚焦的输入框上，不是输入栏。执行方的说明是「与 ChatUI 默认分支一致」，但 ChatUI 的调用点传了输入栏：`riseInput(inputRef.current, $composer)`（`Composer/ComposerInput.js:37-38`）。只影响安卓和内嵌浏览器 | 实现偏离 D5 | 引用出处时只看了被调函数的默认参数，没看调用点 | `useKeyboardFocus` 增加一个可选参数，由页面给出要滚进视野的元素，默认是输入框本身；`ChatArea` 传输入栏（`.composer-bar`）。不要在 hook 里写死类名 |
| P8 | B7（聚焦时列表滚到底）没有测试守着，变异「聚焦时不滚到底」存活 | 实现（测试） | 接线测试只断言了类和属性，没断言回调的效果 | 接线测试里补一条：native 下聚焦后，列表的 `scrollTo` 以 `top = scrollHeight`、`behavior: 'instant'` 被调用 |
| P9 | 监听挂在页面根节点上，所以根节点内任何文字输入框聚焦（例如记忆面板里的输入框）也会把消息列表滚到底 | 已知行为，本段接受 | 本段还没有「输入区」这个边界 | 不改。第三段抽出聊天骨架后监听收窄到输入区插槽 |

**结论**：不通过，修完 P6、P7、P8 再部署。P7 最要紧，而且出在本文件自己。修正只动 `useKeyboardFocus.js`、它的测试、`ChatArea.jsx` 里的一个参数和接线测试的一条断言；不改接口的既有部分，不扩范围。
