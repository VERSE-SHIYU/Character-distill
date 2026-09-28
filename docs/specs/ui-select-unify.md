已被 ui-select-radix.md 取代（S1/S4 的自研交互部分作废，本文件保留作历史记录）。

# Spec：全站下拉统一为主题化 Select 组件

基线：main `ae89f7d`。只改前端，不涉及后端和部署。

## 目标

全站 3 处原生 `<select>` 统一换成一个公共组件 `common/Select.jsx`。弹出列表跟随主题配色（`--accent` 系），替掉系统白底蓝高亮的原生菜单。旧样式直接删除，不留兼容层。

## 已查实约束（基线 `ae89f7d`，S0 逐条复核，一条不成立即停下报告）

| # | 事实 | 坐标 |
|---|---|---|
| C1 | 全站 `<select>` 仅 3 处：角色下拉 1 处，历史筛选 2 处 | `components/AdminPanel.jsx:550`；`components/HistoryPanel.jsx:571`、`:581` |
| C2 | 旧样式仅 4 条规则，除上述 3 处外无其他引用（`group-history-filter-*` 是另一套，不动） | `styles/global.css:5007`、`:5024`、`:9056`、`:9067` |
| C3 | 角色下拉外层 `.admin-table-wrap { overflow-x: auto }`，绝对定位的弹层会被裁掉，所以必须用 portal | `global.css:8971-8975` |
| C4 | portal 已有现成写法：`createPortal(..., document.body)` | `components/common/Modal.jsx:1,5` |
| C5 | 主题 token 已齐：`--surface` `--border-strong` `--radius-sm` `--ring` `--accent` `--accent-soft` `--accent-rgb` `--text` `--card-blur` `--card-shadow`。深色主题和各主题色都靠这些 token 切换 | `global.css:32,51,70,859,884,892,895,897` |
| C6 | （补充·更正）z-index 分布：1000（`.modal-overlay` 等，`6875/9188/9256`）、1001（`9488/12271/16404/17804`）、9999 `.img-preview-overlay`（`16097`）、10000 `.img-preview-close`（`16127`） | `global.css` 各行 |
| C7 | 角色选项来自 `ROLE_KEYS` 和 `roleLabel`；`changeRole(u, value)`、`rolePending` 逻辑不改 | `AdminPanel.jsx:40-41,550-557` |
| C8 | 现有测试无一处依赖这 3 个 select | `components/__tests__/` 已 grep |
| C10 | 箭头图标已有 `ChevronDown` | `components/common/Icon.jsx:536` |
| C9 | 仓库内没有自定义下拉组件，`MentionDropdown` 是 @ 提及专用（内联样式、token 名不同），不复用，也不改 | `components/common/MentionDropdown.jsx` |

## ① 出处对照表（补充·按 APG 重订，基线 `0ad2f9f`）

出处：W3C WAI-ARIA APG《Select-Only Combobox Example》，https://www.w3.org/WAI/ARIA/apg/patterns/combobox/examples/combobox-select-only/ （页面最后更新 2025-08-12），取其中的 Keyboard Support 与 Role/Property/State 两节。行为逐条照抄，偏离必须写明理由。

| APG 条目 | 本 spec 行为 | 现状（`0ad2f9f`） |
|---|---|---|
| 收起 · Down Arrow：展开，不改选中值 | B5 | ✅ |
| 收起 · Alt+Down：展开，不改选中值 | B5 | ✅（按 ArrowDown 处理） |
| 收起 · Up Arrow：展开，高亮移到**第一项** | A1 | ❌ 待做 |
| 收起 · Enter / Space：展开 | B5 | ✅ |
| 收起 · Home / End：展开，高亮移到首项 / 末项 | A2 | ❌ 待做 |
| 收起/展开 · 可打印字符：展开并跳到首个匹配项；快速连打按整串匹配；重复同一字符在同首字母项间循环 | A3 | ❌ 待做 |
| 展开 · Enter / Space：选中高亮项并关闭 | B5 | ✅ |
| 展开 · Tab：**选中高亮项**并关闭，保留默认的焦点移动 | A4 | ❌ 现在只关闭不选中，待改 |
| 展开 · Escape：关闭，保留原值 | B5 | ✅ |
| 展开 · Down / Up：移动高亮，首尾不循环 | B5 | ✅ |
| 展开 · Alt+Up：选中高亮项并关闭 | A5 | ❌ 待做 |
| 展开 · Home / End：高亮移到首项 / 末项 | A2 | ❌ 待做 |
| 展开 · PageUp / PageDown：高亮跳 10 项，到头为止 | A6 | ❌ 待做 |
| 点击触发器收起：保留原值 | B6 | ✅ |
| 焦点离开 combobox 时写入值 | —— | **有意偏离**：外部点击或失焦只关闭、不写值。理由：角色变更是高权限写操作，鼠标移开就误提交不可接受；原生 `<select>` 失焦也不提交。 |
| 高亮变化时把高亮项滚入可视区 | B9 | ✅ |
| combobox：`aria-expanded`、`aria-activedescendant` | B1/B5 | ✅ |
| combobox：`aria-controls` 指向 listbox 的 id | A7 | ❌ 待做 |
| combobox：`aria-labelledby` | B1 | 等价实现：用 `aria-label`（调用方没有可见的 label 元素） |
| option：`aria-selected="true"` 标在**高亮项**上（APG 原文：只出现在被 `aria-activedescendant` 引用的选项上） | A8 | ❌ 现在标在当前值上，待改。当前值的 ✓ 改挂 `.is-current` 类 |

## ② 全量扫描原文（补充，基线 `0ad2f9f`，在 `web/frontend/src` 下执行）

```
== JSX <select / role=combobox|listbox
./components/common/Select.jsx:137:        role="combobox"
./components/common/Select.jsx:151:        <div ref={menuRef} role="listbox" ...>
== CSS select 元素选择器
761:/* Dropdown / select */
762:.theme-midnight select.settings-input,
763:.theme-galaxy select.settings-input {
768:.theme-midnight select.settings-input option,
769:.theme-galaxy select.settings-input option {
5945:select.settings-input {
6849:/* ===== Themed select (ui-select) =====
18757:  select {
== 旧类名 .history-filter / admin-role-select
（空）
== z-index 全部取值（global.css，数量 取值）
-3 -2 -1 0 1 2 3 5 10 11 19 20 50 99 100 101 140 150 200 201 1000×8 1001×4 1100(本组件) 9999 10000
== JSX 内联 zIndex 最大值
1000
== texts / cards 数量上限
（补充·更正：原命令 grep 的 web/backend 目录不存在，扫描不完整。实际路径为 web/routers/text.py + storage/*_store.py：路由只有 @limiter.limit 频率限制和 MAX_FILE_SIZE 单文件大小限制；list_texts 的 SELECT 无 LIMIT。结论不变：无数量上限）
```

结论：761–769、5945、18757 是本段要清理的遗留 `select` 样式；弹层取 1100，仅低于图片预览的 9999/10000，与 C6 一致。

## ③ 规模表（补充）

| 数据源 | 上限 | 来源 | 展示策略 |
|---|---|---|---|
| 角色选项 | 固定 3 个 | `AdminPanel.jsx` 的 `ROLE_KEYS` | 不滚动 |
| 历史 · 文本选项 | **无上限**，随用户上传增长 | store `texts`；后端无数量限制（见 ②） | B9：最大高度 280 可滚，单行省略，A3 键入跳转，A6 翻页 |
| 历史 · 角色选项 | **无上限**，随卡片数量增长 | `HistoryPanel.jsx` 中由 `cards` 推导的 `characterOptions`（:163） | 同上 |

## ④ 调用点矩阵（补充）

| 调用点 | 可观测输出 | 守它的测试 | 发出前预跑的变异及结果 |
|---|---|---|---|
| AdminPanel 角色 | 选中值原样提交为 `setUserRole(id, v)` | AdminPanelRoleSelect「选管理员后提交」 | 把提交值写死成 `'user'` → 🔴 红 |
| AdminPanel 角色 | 请求进行中该行禁用 | AdminPanelRoleSelect「pending 期间禁用」 | 删除 `disabled` → 🔴 红 |
| AdminPanel 角色 | **自己那行和对端节点的行不渲染下拉，只显示文字** | **缺，待补** | 条件改成 `false` → 🟢 **存活** |
| HistoryPanel 文本 | 选中后请求带 `text_id`，选「全部」后不带 | HistoryPanelFilter「文本」 | 空项写成 `'all'` → 🔴 红 |
| HistoryPanel 角色 | 选中后请求带 `character`，选「全部」后不带 | **缺，待补** | 空项写成 `'all'` → 🟢 **存活**；`onChange` 断开 → 🟢 **存活** |

预跑说明：上表 6 个变异由我在 `0ad2f9f` 上实跑。Select 本体的变异，我在 `c5f7a79` 上抽检 6 个、在 `d78ceb7` 上抽检 6 个，存活的已补测试，其余均为红。A1–A8 是新行为，代码尚未实现，无法预跑，改由执行方实现后跑、审计复跑。

## S0：动手前先审

1. 逐条复核 C1–C10，以及 ①–④ 四节（补充：出处表逐条对照 APG 原页；扫描命令原样重跑并比对输出；矩阵里标「存活」的变异先复现存活，再补测试）。
2. 审文末对账表：逐行确认「变异」在改后代码上能让对应测试变红，每个行为都有测试。
3. 有问题先停下报告，再写代码。

Skill：本机装有 `frontend-design` 就调用这一个，只用于视觉细节，其他不加。

## 执行纪律

- 本段改动面内新发现的问题直接修。只有会与其他线未合改动撞车，或需要 Shiyu 拍板时，才停下报告；不许自行记账。
- 本 spec 发出后的任何补充都写进本文件（标「补充」），执行方上下文被压缩后重读本文件。
- 「往已有调用路径加闸/重试/超时/缓存/记账」规则：本段不适用。改动只涉及纯 UI 下拉，不涉及请求路径；`changeRole` 的调用链原样保留（C7）。

## S1：`components/common/Select.jsx`（新建，单文件，不拆 hook）

接口：

```jsx
<Select
  value={string}
  options={[{ value, label }]}
  onChange={(value) => {}}
  disabled={bool}
  size="md" | "sm"        // 默认 md；sm 给表格内紧凑场景
  ariaLabel={string}
  className={string}      // 可选，只追加布局类
/>
```

行为（按顺序实现，每条都有测试）：

- B1 触发器是 `<button type="button" role="combobox" aria-expanded aria-haspopup="listbox">`，显示当前选项的 label，右侧箭头用 `common/Icon.jsx:536` 的 `ChevronDown`。
- B2 点击展开。弹层用 `createPortal` 挂到 `document.body`（同 C4），`position: fixed`，展开时按触发器 `getBoundingClientRect()` 定位，宽度不小于触发器；下方空间不够时向上翻。
- B3 弹层 `role="listbox"`，选项 `role="option"`，当前值 `aria-selected="true"`。
- B4 选中后触发 `onChange(value)` 并关闭；选的是当前值则只关闭，不触发。
- B5 键盘操作：
  - 触发器聚焦时，↓、Enter、Space 展开，高亮落在当前值上。
  - 展开后 ↑↓ 移动高亮（首尾不循环），Enter 选中。
  - Esc 关闭并把焦点还给触发器，Tab 关闭。
- B6 在组件外 `mousedown`、window `resize`、任意滚动（`scroll` 捕获阶段）都会关闭。不做跟随重定位，避免过度实现。
- B7 `disabled` 时点击和键盘都不展开，触发器带 `disabled` 属性。
- B8 卸载时移除全部监听。
- （补充）B9 列表过长：弹层 `max-height: 280px`、`overflow-y: auto`。历史筛选的文本数不设上限，必须能滚动。选项单行显示，加 `white-space: nowrap; overflow: hidden; text-overflow: ellipsis`，完整 label 放进 `title`。行高因此固定为 32px，翻转判断用 `min(n*32+8, 280)` 估高是精确的，不需要 `useLayoutEffect` 实测。
- （补充）size 落到 DOM 的方式：触发器加 `ui-select--md` / `ui-select--sm` 修饰类；箭头 md 16px、sm 14px。已认可。
- （补充·审计 c5f7a79）B5 对齐 WAI-ARIA APG 的 select-only combobox 模式：
  - 展开时按 Space 与 Enter 同效，选中高亮项并关闭，且要 `preventDefault`。否则按钮在 keyup 时触发原生 click，只关闭不选中。
  - 选项加 `id`（`useId` 前缀 + 下标），触发器加 `aria-activedescendant` 指向当前高亮项；收起时去掉该属性。
- （补充·审计 c5f7a79）B9 键盘可达：`activeIndex` 变化时，对高亮项调用 `scrollIntoView({ block: 'nearest' })`。否则长列表里用 ↓ 移到第 9 项以后，高亮就不可见了。
- （补充）弹层内部的滚动不关闭弹层：B6 的 scroll 监听要判断 `menuRef.contains(e.target)`，是则忽略，否则 B9 一滚就关。

## S2：样式（`global.css`，新增 `.ui-select*` 一块，删掉 C2 的 4 条）

只用 C5 的 token，不写死颜色：

- 触发器 `.ui-select`：
  - （补充）md 必须完整承接原 `.history-filter`（`global.css:5008,5010,5014`）的 `min-width: 140px`、`padding: 0 12px`、`backdrop-filter: blur(12px) saturate(160%)`；sm 带 `padding: 0 8px`。
  - md：高 40px，字号 13px，边框 `--border-strong`，圆角 `--radius-sm`，背景 `--surface`，承接原 `.history-filter` 的尺寸。
  - sm：高 30px，字号 12px，承接原 `.admin-role-select`。
  - 悬停时边框取 `--accent`；聚焦或展开时边框 `--accent`，加 `box-shadow: 0 0 0 3px var(--ring)`。
  - disabled：`opacity: .5`，`cursor: not-allowed`。
- 弹层 `.ui-select-menu`：背景 `color-mix(in srgb, var(--surface) 96%, transparent)`，加 `backdrop-filter: var(--card-blur)`；边框 `--border-strong`，圆角 `--radius-sm`，阴影 `--card-shadow`，内边距 4px，`z-index: 1100`，注释写明：高于 1000/1001 的弹窗与抽屉，低于全屏图片预览（9999/10000），预览层之下不显示属预期。
- 选项 `.ui-select-option`：圆角 6px，内边距 `6px 10px`。
  - 高亮（`.is-active`）：背景 `--accent-soft`。
  - 选中（`[aria-selected="true"]`）：文字 `--accent`，`font-weight: 600`，右侧带 ✓。
- 深色主题不单独写覆盖，全部靠 token 自动切换。

## S3：替换 3 处调用

- `AdminPanel.jsx:550`：换成
  `<Select size="sm" ariaLabel="角色" value={u.role || 'user'} options={ROLE_KEYS.map(r => ({ value: r, label: roleLabel(r) }))} disabled={rolePending === u.id} onChange={(v) => changeRole(u, v)} />`
  `options` 可提成模块级常量 `ROLE_OPTIONS`，放在 `ROLE_KEYS` 旁边。
- `HistoryPanel.jsx:571/581`：换成 `<Select>`（md）。第一项分别是 `{ value: '', label: '全部文本' }` 和 `{ value: '', label: '全部角色' }`，其余选项照原 map 生成。`setTextFilter` 和 `setCharacter` 直接作为 `onChange` 传入。
- 完成后 （补充）`grep -rnE "<select|\.history-filter|[\"' ]history-filter|admin-role-select" src` 必须为空。

## S4（补充·按 APG 重订）：补齐 A1–A8

对 `Select.jsx` 做如下修改，逻辑全部留在组件内部，不拆 hook：

- A1：收起时按 ↑，展开并把高亮放在第一项。
- A2：Home / End。收起时展开并高亮首项 / 末项；展开时直接把高亮移到首项 / 末项。
- （补充·审计 fa864cf）A3 以 APG 官方源码为准，逐项照抄，不再用上面的转述：`w3c/aria-practices` main 分支 `content/patterns/combobox/examples/js/select-only.js` 的 `onComboType`、`getSearchString`、`getIndexByLetter` 三个函数。与当前实现的差异有 3 处，必须改：
  1. 键入时**无论是否匹配都先展开**（`onComboType` 第一行 `updateMenuState(true)`）；
  2. **没有匹配时立即清空缓冲串和计时器**；
  3. 整串匹配也从 `activeIndex + 1` 开始、绕回查找（`getIndexByLetter(options, searchString, activeIndex + 1)`）：先找整串前缀匹配，找不到且缓冲串全是同一个字符时，再按首字符找。
- A3（原文，已被上一条取代）：键入跳转。可打印单字符（`e.key.length === 1`，且没有按 Ctrl/Meta/Alt）追加进缓冲串，缓冲 500ms 后清空；按不区分大小写的前缀匹配 label。缓冲串若全由同一个字符组成，就在以该字符开头的选项之间循环。收起时先展开。**Space 仍按 B5 处理，不进入缓冲。**
- A4：展开时按 Tab，先 `commit(activeIndex)`，**不要** `preventDefault`，让焦点正常移走。
- A5：展开时按 Alt+↑，等同于 Enter。
- A6：PageUp / PageDown 让高亮上下跳 10 项，到首尾为止。
- A7：listbox 加 `id={listId}`；触发器加 `aria-controls={listId}`，仅在展开时设置。
- A8：`aria-selected` 改为 `i === activeIndex`；当前值加 `.is-current` 类。CSS 中 `.ui-select-option[aria-selected="true"]` 的规则（文字色、加粗、✓）全部改挂到 `.is-current`。

调用点补 2 条测试：AdminPanel 自己那行和对端行不出现 combobox；HistoryPanel 角色筛选的接线（同「补充·审计 0ad2f9f」）。

## 测试

新建 `components/common/__tests__/Select.test.jsx`（vitest + Testing Library），覆盖 B1–B8。另在两处调用方各补一条接线测试：

- `AdminPanel`：选「管理员」后调用了 `changeRole(u, 'admin')`；`rolePending` 期间下拉被禁用。
- `HistoryPanel`：选某文本后筛选参数更新；选「全部文本」回到 `''`。

- 本地只跑受影响的文件：`npx vitest run src/components/common/__tests__/Select.test.jsx` 加上两个接线测试文件，最后跑一遍 `npm test`。
- 本段只改前端，不跑后端 pytest，也不需要 Docker PG。
- 合并门是分支 CI。合并本身只做 git 操作，不跑测试，不等 CI。
- 报告里不得出现本地全量的数字。

（补充·审计）B8 测试里的 4 个 `vi.spyOn` 结束时要 `mockRestore()`（或包 try/finally），避免泄漏到同文件的后续用例。

（补充·审计 0ad2f9f）遗留死样式一并清掉，属本段改动面：`global.css` 的 `.theme-midnight/.theme-galaxy select.settings-input`（`:762-772`，含 option 规则）和 `select.settings-input`（`:5945`）。基线起全仓就没有 `<select className="settings-input">`，`settings-input` 只挂在 input 上。移动端 `@media (max-width: 768px)` 选择器列表里的 `select`（`:18757`）顺手删掉，`input` 和 `textarea` 保留。

## 手动验证

1. 用户管理的角色下拉：弹层没有被表格裁掉，配色跟随当前主题色（青色主题下为青色）。
2. 历史页两个筛选框的外观、尺寸和原来一致，弹层统一成新样式。
3. 深浅色主题、至少两个主题色各看一遍。
4. 键盘全流程可用；展开后滚动页面，弹层关闭。

## 对账表

| 行为变化（含连带效果） | 守它的测试 | 让它变红的变异 | 改后能触发的具体状态 |
|---|---|---|---|
| B1 触发器语义、显示当前 label | Select：渲染后 `getByRole('combobox')` 文本等于当前 label | label 改取 `options[0]` | value=`'user'` 时显示「用户」 |
| B2 弹层 portal 到 body | （补充）Select：`expect(container.contains(listbox)).toBe(false)` | 去掉 `createPortal` 直接渲染 | 放在 `overflow:auto` 容器内展开 |
| B2 空间不足向上翻 | Select：mock `getBoundingClientRect` 让触发器贴近底部，断言弹层 `bottom` 已设置（未设置 `top`） | 删除翻转分支 | 触发器位于视口底部 |
| B3 选中态（补充：已被 A8 取代，当前值改用 `.is-current` 断言） | Select：当前值 option 带 `aria-selected="true"`，其余为 false | 比较条件取反 | value=`'guest'` |
| B4 选新值触发一次并关闭 | Select：点「管理员」后 onChange 收到 `'admin'` 1 次，listbox 消失 | 选中后不关闭，或调用两次 | 点击其他选项 |
| B4 选同值不触发 | Select：点当前值后 onChange 调用 0 次 | 删除同值判断 | 点击已选中项 |
| B5 键盘展开与高亮 | Select：聚焦后按 ↓，listbox 出现且高亮在当前值 | 高亮初值改为 0 | value 为中间项时按 ↓ |
| B5 ↑↓ 移动与 Enter 选中 | Select：↓↓ Enter 后 onChange 收到对应值 | Enter 分支删除 | 展开后操作键盘 |
| B5 首尾不循环 | Select：在末项按 ↓，高亮仍在末项 | 改成取模循环 | 高亮在最后一项 |
| B5 Esc 关闭并归还焦点 | Select：按 Esc 后 listbox 消失，`document.activeElement` 是触发器 | 删除 `focus()` | 展开状态按 Esc |
| （补充）B5 Tab 关闭 | Select：展开后按 Tab，listbox 消失 | 删除 Tab 分支 | 展开状态按 Tab |
| （补充）B2 宽度不小于触发器 | Select：mock 触发器 rect 宽 200，断言弹层 `minWidth` 为 `200px` | 删除宽度设置 | 选项文字比触发器短 |
| （补充·审计）展开时再点触发器（真实 mousedown+click 序列）会关闭 | Select：展开后对触发器依次 `fireEvent.mouseDown` 和 `fireEvent.click`，listbox 消失 | 删除 `onMouseDown` 里的 `triggerRef.contains` 豁免（审计实测该变异当前存活） | 展开后再点一次触发器 |
| （补充·审计 d78ceb7）收起时 Space 展开 | Select：聚焦触发器后按 Space，listbox 出现，且该 keydown 的 `defaultPrevented` 为 true | 把收起分支的条件改成只认 `ArrowDown` 和 `Enter`（审计实测该变异当前存活） | 键盘用户按空格打开 |
| （补充·审计）展开时 Space 选中 | Select：展开，↓ 后按 Space，onChange 收到对应值，listbox 消失，且该 keydown 的 `defaultPrevented` 为 true | 删除 Space 分支 | 键盘用户按空格选择 |
| （补充·审计）aria-activedescendant | Select：展开后触发器的 `aria-activedescendant` 等于高亮 option 的 id，↓ 后跟着变化；收起后属性消失 | 不设置该属性，或 id 不跟随高亮 | 读屏用户用键盘浏览 |
| （补充·审计）高亮项滚入可视区 | Select：mock `Element.prototype.scrollIntoView`，30 个选项时按 ↓，断言它在新高亮项上被调用，参数为 `{ block: 'nearest' }` | 删除该 effect | 长列表键盘下移 |
| B6 外部点击关闭 | Select：在 body 上触发 `mousedown` 后 listbox 消失 | 删除 mousedown 监听 | 点击页面其他位置 |
| B6 滚动或 resize 关闭 | （补充）Select：在容器内元素上 `fireEvent.scroll`（经捕获阶段到达 document），以及在 window 上 dispatch `resize`，listbox 都消失 | 删除任一监听 | 展开后滚动表格 |
| （补充）B9 长列表可滚、滚动不关闭 | Select：30 个选项，断言弹层 style 含 `maxHeight: 280px`；在弹层内 `fireEvent.scroll` 后 listbox 仍在 | 删除 `menuRef.contains` 判断；去掉 maxHeight | 历史页上传了大量文本 |
| （补充）B9 翻转估高封顶 | Select：30 个选项，触发器下方剩 300px，断言向下展开（估高按 280 封顶，而不是 30×32） | 去掉 `min(…, 280)` 封顶 | 长列表，触发器在页面中部 |
| B7 disabled 不展开 | Select：disabled 时点击和按 ↓ 都不出现 listbox | （补充）同时删除两处 disabled 判断（`openMenu` 和 `onTriggerKeyDown`）| `rolePending === u.id` |
| B8 卸载清理监听 | （补充）Select：spy 只筛选本组件的 (type, handler) 对（mousedown/scroll/resize），卸载后每一对都被 remove，且 handler 引用一致 | 删除 cleanup | 展开时切走页面 |
| 角色下拉接线 | AdminPanel 接线测试：选「管理员」后 `changeRole` 收到 `(u, 'admin')` | onChange 传错值 | 管理员改他人角色 |
| 角色请求中禁用 | AdminPanel 接线测试：pending 期间 combobox 为 disabled | 删除 disabled 传参 | 改角色请求未返回 |
| 历史筛选接线 | （补充）HistoryPanel 接线测试：先选某文本，断言请求 URL 带 `text_id=<id>`（`HistoryPanel.jsx:208`）；再选「全部文本」，断言请求 URL 不带 `text_id` | 空项 value 写成 `'all'` | 切换筛选 |
| （补充·审计 0ad2f9f）角色筛选接线 | HistoryPanel 接线测试：`mockState.cards` 放一张 `name: '甲'` 的卡；选「甲」后请求 URL 带 `character=`（值为 `encodeURIComponent('甲')`）；再选「全部角色」后不带 `character` | 角色空项写成 `'all'`；`onChange={() => {}}`（审计实测两条都存活） | 按角色筛选历史 |
| （补充·审计 0ad2f9f）遗留 select 样式清除 | 验收 grep 追加 `grep -nE "select\.settings-input" src/styles/global.css` 为空 | 保留任一条 | 全站 |
| （补充·APG）A1 收起时 ↑ | Select：value 为末项，聚焦后按 ↑，listbox 出现且高亮在第 0 项 | ↑ 走与 ↓ 相同的分支（高亮落在当前值） | 键盘用户按 ↑ |
| （补充·APG）A2 Home/End | Select：收起按 End，高亮在末项；展开后按 Home，高亮在首项 | 删除 Home/End 分支 | 长列表首尾跳转 |
| （补充·APG）A3 键入跳转 | Select：选项为「甲一」「乙一」「乙二」，按「乙」高亮在「乙一」，再按「乙」高亮在「乙二」；等 fake timer 走过 500ms 后按「甲」，高亮在「甲一」 | 去掉同字符循环；或不清空缓冲 | 长列表按首字定位 |
| （补充·APG）A4 Tab 选中 | Select：展开后 ↓，按 Tab，onChange 收到高亮值，listbox 消失，且 `defaultPrevented` 为 false | Tab 只关闭不选中；或加上 `preventDefault` | 键盘用户 Tab 离开 |
| （补充·APG）A5 Alt+↑ | Select：展开后 ↓，按 Alt+↑，onChange 收到高亮值并关闭 | 删除 Alt+↑ 分支 | 键盘用户 |
| （补充·审计 fa864cf）A6 PageUp | Select：30 个选项，高亮在 25 时按 PageUp 到 15，在 4 时按 PageUp 到 0 | PageUp 步长改为 1（审计实测存活） | 长列表向上翻页 |
| （补充·审计 fa864cf）A3 无匹配也展开并清空缓冲 | Select：收起时按一个无匹配的字符，listbox 出现，高亮仍在当前值；紧接着按一个有匹配的字符，高亮跳到该项（证明缓冲已清空，没有拼成两字串） | 无匹配时不展开；或不清空缓冲 | 键入了不存在的首字 |
| （补充·审计 fa864cf）A3 整串匹配从高亮项之后开始 | Select：选项为「ab1」「ab2」「ab3」，高亮在「ab1」，快速连打 a、b，高亮到「ab3」（按 APG 源码逐键推演：a 从第 1 项找到 ab2，ab 再从第 2 项找到 ab3；当前实现得到 ab1） | 整串匹配改为从 0 开始 | 同前缀的多个选项 |
| （补充·APG）A6 翻页 | Select：30 个选项，高亮在 0 时按 PageDown 到 10，在 25 时按 PageDown 到 29 | 不做末项封顶；或步长不是 10 | 长列表翻页 |
| （补充·APG）A7 aria-controls | Select：展开后触发器的 `aria-controls` 等于 listbox 的 id；收起后属性消失 | 删除 `aria-controls` | 读屏用户 |
| （补充·APG）A8 aria-selected 跟随高亮 | Select：value 为 user，展开后 ↓，`aria-selected="true"` 只在高亮项上，`is-current` 仍在「用户」上 | `aria-selected` 仍按当前值 | 读屏用户浏览选项 |
| （补充·矩阵）自己和对端行不渲染下拉 | AdminPanel：列表里放自己（`authUser.id`）一行和 `node_region: 'peer'` 一行，两行都没有 combobox，并显示角色文字 | 渲染条件改为 `false`（预跑存活） | 管理员查看自己或对端用户 |
| 连带：旧样式删除，无残留引用 | S3 末尾的 grep 为空（CI 前手动核查，写进交付报告） | 保留任一旧 className | 全站 |
| 连带：配色随主题 | 手动验证 1、3（纯 CSS token，jsdom 无法断言） | 写死十六进制色 | 切换主题色或深浅色 |
