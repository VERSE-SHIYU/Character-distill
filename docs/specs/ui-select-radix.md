# Spec：下拉改为复用 Radix Select（取代自研方案）

基线：分支 `worktree-ui-select` 的 `fa864cf`。只改前端，不涉及后端和部署。
本 spec 取代 `docs/specs/ui-select-unify.md` 中 S1 和 S4 的自研交互部分。那份文件保留作历史记录，首行加一句「已被 ui-select-radix.md 取代」。

## 目标

`common/Select.jsx` 改为对 `@radix-ui/react-select` 的薄封装：键盘、读屏、键入跳转、定位、portal 全部交给库。我们只保留对外接口和主题样式，3 处调用点一行不改。

## 已查实约束（每条在 `fa864cf` 或已安装的 Radix 2.3.7 源码上现查；S0 逐条复核，任一不成立即停）

| # | 事实 | 坐标 |
|---|---|---|
| R1 | 对外接口 `value / options / onChange / disabled / size / ariaLabel / className` 被 3 处调用使用，保持不变 | `AdminPanel.jsx:14,552`；`HistoryPanel.jsx:16,572,581` |
| R2 | 自研实现与其测试将被整体替换 | `Select.jsx`（242 行）、`__tests__/Select.test.jsx`（441 行） |
| R3 | Radix Select 最新版 2.3.7，peer 依赖声明含 `react ^19.0` | `npm view @radix-ui/react-select` |
| R4 | **Radix 把 `''` 当成「未选中、显示 placeholder」**：Root 的值为 `''` 时，触发器文字为空，而不是显示「全部文本」。历史筛选的「全部」项取值正是 `''`，所以封装层必须把 `''` 与一个哨兵值互相映射 | Radix `dist/index.mjs:1140-1141` `shouldShowPlaceholder`；jsdom 实测触发器文字为空 |
| R5 | 触发器用鼠标 `pointerdown` 打开，用 `click` 处理非鼠标指针，键盘用 `" " / Enter / ArrowUp / ArrowDown` 打开 | `dist/index.mjs:32,202-229` |
| R6 | 状态属性：触发器 `data-state=open/closed`、`data-disabled`、`data-placeholder`；选项 `data-highlighted`、`data-state=checked/unchecked`、`data-disabled` | `dist/index.mjs:196-199,858-862` |
| R7 | `position` 默认值是 `item-aligned`（弹层盖在触发器上）；设为 `popper` 才会贴在触发器下方并避让视口 | `dist/index.mjs:300,315` |
| R8 | popper 模式暴露的 CSS 变量：`--radix-select-trigger-width`、`--radix-select-content-available-height` | `dist/index.mjs:712-716` |
| R9 | 弹层外包一层 wrapper，它的 z-index 会复制弹层自身计算出的 z-index，所以在 `.ui-select-menu` 上写 `z-index` 就能生效 | `@radix-ui/react-popper/dist/index.mjs:192,206` |
| R10 | Viewport 自带 `overflow: hidden auto`，并注入样式隐藏滚动条 | `dist/index.mjs:736,758` |
| R11 | 打开时会通过 `react-remove-scroll` 锁住页面滚动，这是库的默认行为，接受。原先的「滚动即关闭」随之作废 | `dist/index.mjs:30,458` |
| R12 | 现有 3 个接线测试文件不改一行，在 Radix 原型上实跑 5/5 通过 | `AdminPanelRoleSelect.test.jsx`、`HistoryPanelFilter.test.jsx` |
| R13 | 现有 `.ui-select*` 样式块 | `global.css:6832-6925` |

## ① 出处对照表

出处：Radix Primitives Select 官方文档（radix-ui.com/primitives/docs/components/select，声明遵循 WAI-ARIA listbox 模式，自带完整键盘导航和键入跳转），加上已安装的 `@radix-ui/react-select@2.3.7` 源码。封装层只使用下列公开部件和属性：

| Radix 部件或属性 | 用途 | 本 spec 对应 |
|---|---|---|
| `Root`：`value`、`onValueChange`、`disabled` | 受控取值与禁用 | S1、R4 |
| `Trigger`：`aria-label`、`className` | 触发器语义与样式 | S1 |
| `Value` | 显示当前项文字 | S1 |
| `Icon` | 放置 `ChevronDown` | S1 |
| `Portal` | 挂到 body，避免被表格的 overflow 裁掉 | S1 |
| `Content`：`position="popper"`、`sideOffset={4}`、`className` | 贴在触发器下方、避让视口 | S1、R7 |
| `Viewport` | 可滚动容器 | S1、R10 |
| `Item`：`value`、`className`、`title`，以及 `ItemText` | 选项 | S1 |
| `ItemIndicator` | 选中项的 ✓ | S1 |

键盘、读屏语义、键入跳转、焦点管理不在封装层实现，也不在我们的测试里重测。

## ② 全量扫描原文（`fa864cf`，在 `web/frontend` 下执行）

```
== 引用 Select 组件的文件
src/components/AdminPanel.jsx:14:import Select from './common/Select'
src/components/AdminPanel.jsx:552:                      <Select
src/components/HistoryPanel.jsx:16:import Select from './common/Select'
src/components/HistoryPanel.jsx:572:            <Select
src/components/HistoryPanel.jsx:581:            <Select
== 待替换文件行数
242 src/components/common/Select.jsx
441 src/components/common/__tests__/Select.test.jsx
== .ui-select* 样式选择器（global.css）
6834 .ui-select / 6850 :hover / 6854 :focus / 6855 [aria-expanded="true"] / 6860 :disabled
6866 --md / 6876 --sm / 6885 -menu / 6896 -option / 6909 .is-active / 6913 .is-current / 6919 .is-current::after
== package.json 现有 radix 依赖
（无）
```

## ③ 规模表

| 数据源 | 上限 | 来源 | 展示策略 |
|---|---|---|---|
| 角色选项 | 固定 3 个 | `ROLE_KEYS` | 不滚动 |
| 历史 · 文本选项 | 无上限 | store `texts`；`web/routers/text.py` 只限频率和单文件大小，`storage/*_store.py` 的 list 查询没有 LIMIT | `.ui-select-menu` 最大高度 `min(280px, var(--radix-select-content-available-height))`，由 Viewport 滚动；键入跳转和翻页由 Radix 提供 |
| 历史 · 角色选项 | 无上限 | `HistoryPanel.jsx` 由 `cards` 推导 | 同上 |

## ④ 调用点矩阵（变异均已由我在 Radix 原型上预跑）

| 调用点 | 可观测输出 | 守它的测试 | 预跑变异 → 结果 |
|---|---|---|---|
| AdminPanel 角色 | 选中值原样提交 `setUserRole(id, v)` | AdminPanelRoleSelect「选管理员后提交」 | C1 把提交值写死成 `'user'` → 🔴 |
| AdminPanel 角色 | 请求进行中该行禁用 | AdminPanelRoleSelect「pending 期间禁用」 | C2 删除 `disabled` → 🔴 |
| AdminPanel 角色 | 自己那行和对端行不渲染下拉 | AdminPanelRoleSelect「自己与对端行」 | C3 渲染条件改为 `false` → 🔴 |
| HistoryPanel 文本 | 请求带或不带 `text_id` | HistoryPanelFilter「文本」 | C4 空项写成 `'all'` → 🔴 |
| HistoryPanel 角色 | 请求带或不带 `character` | HistoryPanelFilter「角色」 | C5 空项写成 `'all'` → 🔴；C6 断开 `onChange` → 🔴 |

## S0：动手前先审

1. 逐条复核 R1–R13，以及 ①–④。② 的扫描命令原样重跑并比对输出。
2. 审文末对账表，确认每个变异在改后的代码上可观测。
3. 有问题先停下报告，再写代码。

Skill：本机装有 `frontend-design` 就调用它，只用于视觉细节，不加其他 skill。

## 执行纪律

- 本段改动面内新发现的问题直接修；只有会与其他线撞车，或需要 Shiyu 拍板时才停下报告，不许自行记账。
- 任何补充都写进本文件，标「补充」。
- 「往已有调用路径加闸、重试、超时、缓存、记账」这条规则本段不适用：纯 UI，调用链不变。

## S1：依赖与封装

1. 在 `web/frontend` 执行 `npm install @radix-ui/react-select@2.3.7`，`package.json` 和 `package-lock.json` 一起提交。
2. 用下面的实现整体替换 `Select.jsx`，约 30 行。结构照抄，只允许调整格式：

```jsx
import * as RS from '@radix-ui/react-select'
import { ChevronDown } from './Icon'

// Radix 把 '' 当作「未选中」显示 placeholder（R4）；调用方的「全部」项取值为 ''，
// 所以进出 Radix 时与哨兵值互相映射。
const EMPTY = '__ui_select_empty__'
const toRadix = (v) => (v === '' ? EMPTY : v)
const fromRadix = (v) => (v === EMPTY ? '' : v)

export default function Select({ value, options, onChange, disabled = false, size = 'md', ariaLabel, className = '' }) {
  return (
    <RS.Root value={toRadix(value)} onValueChange={(v) => onChange(fromRadix(v))} disabled={disabled}>
      <RS.Trigger aria-label={ariaLabel} className={`ui-select ui-select--${size}${className ? ` ${className}` : ''}`}>
        <RS.Value />
        <RS.Icon className="ui-select-caret"><ChevronDown size={size === 'sm' ? 14 : 16} /></RS.Icon>
      </RS.Trigger>
      <RS.Portal>
        <RS.Content position="popper" sideOffset={4} className="ui-select-menu">
          <RS.Viewport>
            {options.map((o) => (
              <RS.Item key={o.value} value={toRadix(o.value)} className="ui-select-option" title={o.label}>
                <RS.ItemText>{o.label}</RS.ItemText>
                <RS.ItemIndicator className="ui-select-check">✓</RS.ItemIndicator>
              </RS.Item>
            ))}
          </RS.Viewport>
        </RS.Content>
      </RS.Portal>
    </RS.Root>
  )
}
```

## S2：样式（`global.css:6832-6925` 原地改）

只改选择器与少量属性，配色 token 全部保留：

| 原选择器 | 改为 |
|---|---|
| `.ui-select[aria-expanded="true"]` | `.ui-select[data-state="open"]` |
| `.ui-select:disabled` | `.ui-select[data-disabled]` |
| `.ui-select-option.is-active` | `.ui-select-option[data-highlighted]`，并加 `outline: none` |
| `.ui-select-option.is-current` | `.ui-select-option[data-state="checked"]` |
| `.ui-select-option.is-current::after`（✓） | 删除，改为 `.ui-select-check { position: absolute; right: 10px; }`，由 `ItemIndicator` 渲染 |

`.ui-select-menu` 追加两条：
- `min-width: var(--radix-select-trigger-width)`
- `max-height: min(280px, var(--radix-select-content-available-height))`

`z-index: 1100` 保留（R9）。

## S3：测试

- 删除 `common/__tests__/Select.test.jsx`（自研交互测试）。
- 新建同名文件，只放下面 6 条封装层测试。打开下拉统一用 `fireEvent.keyDown(trigger, { key: 'Enter' })`（R5）：
  - W1：值为 `''` 时，触发器显示该项的 label；
  - W2：选中「全部」项时，`onChange` 收到 `''`；
  - W3：打开后 listbox 不在调用方容器内（portal）；
  - W4：传入 `disabled` 后触发器为禁用；
  - W5：`size` 和 `className` 落到触发器上；
  - W6：打开后 `.ui-select-menu` 的 `data-side` 为 `bottom`（popper 模式）。
- 3 个接线测试文件不改。
- 本地只跑：`npx vitest run` 上述 3 个测试文件，然后 `npm test`，再跑 `npx eslint src -c eslint.ci.config.js --quiet`。不跑后端测试，也不需要 Docker PG。合并门是分支 CI；合并本身只做 git 操作，不跑测试，不等 CI。

## S4（补充）：浏览器自动验收，取代原「手动验证」

复用仓库现成的 e2e 做法：`web/frontend/e2e/helpers.cjs` 导出的 `openApp / login / goToView / cs / shot`。写法参照 `e2e/settings-verify.cjs`，用 `page.route` 注入假数据。

新建 `web/frontend/e2e/ui-select-verify.cjs`（与同目录其他 verify 脚本一样入库），用法：`node e2e/ui-select-verify.cjs`。

**已查实约束（补充，基线 `2986443`）**

| # | 事实 | 坐标 |
|---|---|---|
| E1 | helpers 连接 `http://localhost:7861`，账号 `testadmin`，密码只从环境变量 `TEST_PASSWORD` 读取 | `e2e/helpers.cjs:11-12,17-21` |
| E2 | 7861 是本地 docker 编排的端口：在 worktree 根目录执行 `docker compose -f docker-compose.local.yml up -d --build`，会用当前分支代码现场构建 | `README.md:105` |
| E3 | 主题由 `<html>` 的类名 `theme-<key>` 切换，共 6 个：aurora / milktea / ocean / sakura / midnight / galaxy（后两个是深色） | `src/utils/theme.js:20-23`、`src/utils/themes.js:3-8` |
| E4 | 视图名 `admin`（管理，内含用户管理标签页）和 `history`，可用 `goToView(page, name)` 进入 | `src/components/MobileTabBar.jsx:33`、`src/components/Sidebar.jsx:78,95` |
| E5 | 接口：`/api/admin/users/federated`、`/api/text/list`、`/api/history/list` | `src/api/client.js:297`、`src/store/useAppStore.js:658`、`HistoryPanel.jsx:211` |

**补充·环境（回应执行方报告的端口与容器冲突）**
- 同意只起两个服务：`docker compose -f docker-compose.local.yml up -d --build postgres app`。依据：`app` 的 `depends_on` 只有 `postgres`（`docker-compose.local.yml:107-109`），OTel 默认关闭（`:100-103`，`OTEL_ENABLED` 默认 0），不需要 jaeger。报告里单列这处对 E2 的偏离。
- 新发现：本 worktree 起的是一套**空库**。`pg_data` 是按项目名隔离的卷（`:17-18,125-126`），`./data` 挂的是 worktree 自己的目录，所以没有 testadmin 账号，真登录必然失败。处理：**登录也用 mock，不依赖任何真实账号**。
  - 做法：先注册一条兜底路由 `**/api/**`，统一返回 `{}`；再注册下面的具体路由。Playwright 优先匹配后注册的路由。
  - `POST /api/auth/login` 返回 `{ access_token: 'e2e', refresh_token: 'e2e', user: ADMIN }`，`/api/auth/me` 返回 `ADMIN`。其中 `ADMIN = { id: 'admin1', username: 'testadmin', role: 'admin' }`。登录流程见 `src/store/useAppStore.js:72-77`。
  - `helpers.login` 会检查 `TEST_PASSWORD` 是否存在（`helpers.cjs:17-21`），运行时设 `TEST_PASSWORD=e2e-mock`。这是占位值，不是凭据，可以写进命令，也可以出现在报告里。
- 跑完执行 `docker compose -f docker-compose.local.yml down`（不加 `-v`）停掉本 worktree 的栈。

**假数据（page.route）**
- `/api/admin/users/federated` 返回：本地 `[自己 testadmin, 他人 u2 role:user]`，对端 `[]`。
- `/api/text/list` 返回 30 条文本，文件名依次为「文本01」到「文本30」。
- `/api/history/list*` 返回 `{ items: [], total: 0 }`，同时记录每次请求的 URL。

**断言（桌面视口 1280×800；V2 还要在 390×844 下再跑一遍；V1–V3 对 6 个主题各跑一遍）**

| # | 场景 | 断言 |
|---|---|---|
| V1 | 用户管理页，打开 u2 行的角色下拉 | ① 弹层上沿 ≥ 触发器下沿；② 弹层整体在视口内；③ 在弹层中心点调用 `document.elementFromPoint`，命中的元素位于 `.ui-select-menu` 内（证明没被表格裁掉或被其他层盖住）；④ 弹层宽度 ≥ 触发器宽度 |
| V2 | 历史页两个筛选框 | 高度为 40px，宽度 ≥ 140px；未选择时文字分别为「全部文本」「全部角色」 |
| V3 | 配色（每个主题） | 用一个临时元素设 `color: var(--accent)` 取出计算值，与 `[data-state="checked"]` 选项的文字色一致；同样方法比对 `[data-highlighted]` 选项背景色与 `--accent-soft` 一致；角色下拉触发器高度为 30px |
| V4 | 历史页打开文本筛选 | 弹层高度 ≤ 280px；Viewport 的 `scrollHeight > clientHeight`；滚动到底后「文本30」可见 |
| V5 | 键盘，文本筛选 | 聚焦触发器 → Enter 打开 → 按 ↓ → 输入「文」→ Enter；断言最后一次 `/api/history/list` 请求带 `text_id`，且下拉已关闭 |
| V6 | 键盘，Esc | 打开后按 Esc，弹层消失，焦点回到触发器 |

**截图**：每个主题的 V1、V4 各截一张，文件名 `ui-select-<主题>-<V1|V4>.png`，存到默认的 `e2e/screenshots/`（该目录是否被 git 忽略由执行方先查；被忽略就不入库）。

**报告格式**：逐条列出 V1–V6 × 主题的结果（通过或失败，附实测值）；如有 `pageerror`，全部列出；附截图路径。

**变异（证明脚本有分辨力，跑前先确认无变异基线全过）**
- 去掉 `position="popper"` → V1① 应失败；
- 删掉 `.ui-select-menu` 的 `max-height` → V4 应失败；
- 把 `.ui-select-option[data-state="checked"]` 的 `color` 改成 `#000` → V3 应失败。

跑完这 3 个变异立即还原代码。

## 手动验证（补充：已由 S4 自动验收取代；Shiyu 只需浏览截图，判断主观观感）

1. 用户管理：角色下拉不被表格裁掉，贴在触发器下方，配色跟随当前主题色。
2. 历史页：两个筛选框尺寸和原来一致；未选时显示「全部文本」「全部角色」；长列表可以滚动。
3. 深浅色主题和至少两个主题色各看一遍。
4. 键盘：Tab 聚焦，Enter 打开，↑↓ 移动，输入首字跳转，Enter 选中，Esc 关闭。

## 对账表（W 与 C 两组共 12 个变异均已由我在原型上实跑，全部 🔴）

| 行为变化 | 守它的测试 | 让它变红的变异（预跑结果） | 改后能触发的状态 |
|---|---|---|---|
| 空值显示「全部」的 label | W1 | Root 不做空值映射 → 🔴 | 历史页初始状态 |
| 选「全部」回传 `''` | W2 | `fromRadix` 改为原样返回 → 🔴 | 从某个文本切回「全部」 |
| 弹层 portal 到 body | W3 | 去掉 `Portal` → 🔴 | 表格内的角色下拉 |
| disabled 透传 | W4 | 删除 `disabled` → 🔴 | 改角色请求进行中 |
| size 和 className 生效 | W5 | size 写死为 md → 🔴 | 表格内 sm 尺寸 |
| popper 定位 | W6 | 去掉 `position="popper"` → 🔴 | 弹层贴在触发器下方 |
| 角色提交值 | 接线 | C1 → 🔴 | 管理员改他人角色 |
| pending 期间禁用 | 接线 | C2 → 🔴 | 请求未返回 |
| 自己和对端行不可改 | 接线 | C3 → 🔴 | 管理员查看自己或对端用户 |
| 文本筛选空项 | 接线 | C4 → 🔴 | 选「全部文本」 |
| 角色筛选空项与接线 | 接线 | C5、C6 → 🔴 | 选「全部角色」 |
| 连带：样式选择器改挂 Radix 的状态属性 | 手动验证 1–3（jsdom 不加载 CSS，无法断言） | 不适用 | 打开下拉、高亮、选中 |
