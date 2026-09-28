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

## S0：动手前先审

1. 逐条复核 C1–C9。
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

## 测试

新建 `components/common/__tests__/Select.test.jsx`（vitest + Testing Library），覆盖 B1–B8。另在两处调用方各补一条接线测试：

- `AdminPanel`：选「管理员」后调用了 `changeRole(u, 'admin')`；`rolePending` 期间下拉被禁用。
- `HistoryPanel`：选某文本后筛选参数更新；选「全部文本」回到 `''`。

- 本地只跑受影响的文件：`npx vitest run src/components/common/__tests__/Select.test.jsx` 加上两个接线测试文件，最后跑一遍 `npm test`。
- 本段只改前端，不跑后端 pytest，也不需要 Docker PG。
- 合并门是分支 CI。合并本身只做 git 操作，不跑测试，不等 CI。
- 报告里不得出现本地全量的数字。

（补充·审计）B8 测试里的 4 个 `vi.spyOn` 结束时要 `mockRestore()`（或包 try/finally），避免泄漏到同文件的后续用例。

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
| B3 选中态 | Select：当前值 option 带 `aria-selected="true"`，其余为 false | 比较条件取反 | value=`'guest'` |
| B4 选新值触发一次并关闭 | Select：点「管理员」后 onChange 收到 `'admin'` 1 次，listbox 消失 | 选中后不关闭，或调用两次 | 点击其他选项 |
| B4 选同值不触发 | Select：点当前值后 onChange 调用 0 次 | 删除同值判断 | 点击已选中项 |
| B5 键盘展开与高亮 | Select：聚焦后按 ↓，listbox 出现且高亮在当前值 | 高亮初值改为 0 | value 为中间项时按 ↓ |
| B5 ↑↓ 移动与 Enter 选中 | Select：↓↓ Enter 后 onChange 收到对应值 | Enter 分支删除 | 展开后操作键盘 |
| B5 首尾不循环 | Select：在末项按 ↓，高亮仍在末项 | 改成取模循环 | 高亮在最后一项 |
| B5 Esc 关闭并归还焦点 | Select：按 Esc 后 listbox 消失，`document.activeElement` 是触发器 | 删除 `focus()` | 展开状态按 Esc |
| （补充）B5 Tab 关闭 | Select：展开后按 Tab，listbox 消失 | 删除 Tab 分支 | 展开状态按 Tab |
| （补充）B2 宽度不小于触发器 | Select：mock 触发器 rect 宽 200，断言弹层 `minWidth` 为 `200px` | 删除宽度设置 | 选项文字比触发器短 |
| （补充·审计）展开时再点触发器（真实 mousedown+click 序列）会关闭 | Select：展开后对触发器依次 `fireEvent.mouseDown` 和 `fireEvent.click`，listbox 消失 | 删除 `onMouseDown` 里的 `triggerRef.contains` 豁免（审计实测该变异当前存活） | 展开后再点一次触发器 |
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
| 连带：旧样式删除，无残留引用 | S3 末尾的 grep 为空（CI 前手动核查，写进交付报告） | 保留任一旧 className | 全站 |
| 连带：配色随主题 | 手动验证 1、3（纯 CSS token，jsdom 无法断言） | 写死十六进制色 | 切换主题色或深浅色 |
