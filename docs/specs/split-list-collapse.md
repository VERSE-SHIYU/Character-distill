# Spec：双栏「收起列表」＋对话头部让位

基线 `main` `fb893b9`。只改前端。本文件是最终版，覆盖此前同名文件和聊天里的「补充 1」。参考实现已在沙箱独立 worktree 实跑（未动仓库），随附 `split-list-collapse.impl.diff`、`SplitLayout.test.jsx`、`split-layout-verify.cjs`。

## 目标
三个桌面双栏页（角色对话、私信、群聊）用同一套机制收起左侧列表，对话区占满；窄窗口下头部按优先级让位，不硬裁。移动端（≤768px）不变。

## 设计（已拍板）
- 自写薄层，不引库：`react-resizable-panels` 4.14.1、`allotment` 1.20.5（均 MIT、支持 React 19）是拖拽调宽分栏，需整体改写布局并引入多余的拖拽条；`react-collapse-pane` 3.0.1 自 2022 年未发布、强依赖 MUI。
- 开关在对话头部最左，语义按 W3C APG Disclosure（`aria-expanded`）；默认展开；每页各记一份。
- **一套机制**：`SplitLayout` 接收 `list` 并自己包裹、隐藏；页面不加任何约定类。三页统一为 flex 布局（角色对话由网格改 flex）。头部元素只声明优先级 `data-shed="1"|"2"`，全站两条规则。
- 已接受的代价：地区标签与「N 个角色」同档让位（头部内容区 ≤640px 隐藏）。

## 已查实约束（`fb893b9`；S0 逐条复核，任一不成立即停）
| # | 事实 | 坐标 |
|---|---|---|
| C1 | 三个双栏容器与列表 | `ChatArea.jsx:109-115`；`MessagesPage.jsx:144-183`；`GroupChatPage.jsx:870-937` |
| C2 | 三个头部 | `ChatArea.jsx:501`、`PrivateMessageChat.jsx:472`、`GroupChatPage.jsx:955` |
| C3 | 桌面＝宽度 ≥769 | `hooks/useIsMobile.js:3,7` |
| C4 | `.chat-desktop` 是网格 `minmax(300px,320px) 1fr`，改 flex 后列表固定 320px（实测改前改后均 320） | `global.css:3994-4003` |
| C5 | 私信列表内联 `display` 压过 CSS，须改为仅移动端设置 | `MessagesPage.jsx:149` |
| C6 | 群名 `flex-shrink:0` 导致硬切 | `global.css:12443-12447` |
| C7 | 改名 `onClick` 只在群名 span 上 | `GroupChatPage.jsx:967` |
| C8 | 让位标记位置：`group-avatar-stack` 只标头部那处（`:968`），**列表项里的 `:910` 不标**；`group-header-count` `:985`；`dm-peer-tag` `ChatArea.jsx:527,528`、`PrivateMessageChat.jsx:489` | 见左 |
| C9 | `ChatView` 也用于移动端单栏，`PaneToggle` 在 SplitLayout 外渲染空 | `ChatArea.jsx:106` |
| C10 | 私信空状态是另一个 `.messages-layout`，不包 SplitLayout | `MessagesPage.jsx:126` |

## 改动（全貌见 diff）
1. `Icon.jsx` 新增 `PanelLeft`。
2. 新建 `common/SplitLayout.jsx`（结构照抄）：

```jsx
import { createContext, useContext, useState } from 'react'
import useIsMobile from '../../hooks/useIsMobile'
import { PanelLeft } from './Icon'

// 双栏页（列表 + 对话）的「收起列表」机制：容器、状态、记忆都在这里，三个页面共用。
// 语义照 W3C APG Disclosure：按钮 aria-expanded 反映列表是否可见。
const SplitContext = createContext(null)
const KEY_PREFIX = 'split_list_collapsed:'

function readFlag(id) {
  try { return localStorage.getItem(KEY_PREFIX + id) === '1' } catch { return false }
}
function writeFlag(id, value) {
  try { localStorage.setItem(KEY_PREFIX + id, value ? '1' : '0') } catch { /* 隐私模式等：只丢记忆 */ }
}

// id：存储键后缀（每页各存一份）；label：按钮的无障碍名称；list：左侧列表；children：对话区。
// hasActive：当前是否打开了某个对话。没有对话时头部不存在、开关不可达，所以此时强制显示列表。
export default function SplitLayout({ id, label, hasActive, list, className = '', children }) {
  const isMobile = useIsMobile()
  const [stored, setStored] = useState(() => readFlag(id))
  const collapsed = !isMobile && hasActive && stored
  const toggle = () => {
    const next = !stored
    setStored(next)
    writeFlag(id, next)
  }
  return (
    <SplitContext.Provider value={{ enabled: !isMobile, collapsed, toggle, label }}>
      <div className={`split-layout ${className}`.trim()} data-list-collapsed={collapsed ? 'true' : 'false'}>
        <div className="split-list">{list}</div>
        {children}
      </div>
    </SplitContext.Provider>
  )
}

// 放在对话头部最左。不在 SplitLayout 内（例如移动端单栏的 ChatView）或移动端时什么都不渲染。
export function PaneToggle() {
  const ctx = useContext(SplitContext)
  if (!ctx || !ctx.enabled) return null
  return (
    <button
      type="button"
      className="dm-back pane-toggle"
      aria-expanded={!ctx.collapsed}
      aria-label={ctx.label}
      title={ctx.collapsed ? `展开${ctx.label}` : `收起${ctx.label}`}
      onClick={ctx.toggle}
    >
      <PanelLeft size={20} />
    </button>
  )
}
```

3. 三页：`<SplitLayout id label hasActive list={列表} className>对话区</SplitLayout>`，头部最前放 `<PaneToggle />`。`hasActive`：角色对话恒真（已有会话守卫）、私信 `!!activeOtherId`、群聊 `!!currentGroup`。私信内联 display 改仅移动端（C5）。按 C8 加 `data-shed`。
4. `global.css`：`.chat-desktop` 改 flex，`.chat-desktop .tl-panel { width:320px; flex-shrink:0 }`、`.conv-panel { flex:1 }`；`.split-list { display: contents }`（**必须**：去掉则长列表撑出容器 1940px、无法滚动）；收起时 `display:none`；群名改 `flex:0 1 auto; min-width:5em`；头部 `container: chead / inline-size` 与两条 `[data-shed]` 规则，均在 `@media (min-width:769px)` 内。

触发链：点 PaneToggle → `toggle()` → `setStored` + 写 localStorage → `collapsed` 重算 → `data-list-collapsed` 变 → CSS 隐藏 `.split-list`。无活动对话或移动端时 `collapsed` 恒为 false。

## 测试
- `SplitLayout.test.jsx` S1–S8：切换与 `aria-expanded`、记忆、空状态兜底、移动端、容器外渲染空、存储抛错。
- `split-layout-verify.cjs` V1–V7：收起/展开几何与按钮不跳位；空状态兜底；头部让位（7 个宽度）；群聊改名不受影响；手机端标签；列表宽度 320/280；40 条长列表不超出容器且可滚动。路由只拦 `pathname` 以 `/api/` 开头的请求。
- 本地只跑 SplitLayout.test.jsx 与五个相关组件的既有测试、`npm test`、`npx eslint src -c eslint.ci.config.js --quiet`；验收环境：`docker compose -f docker-compose.local.yml up -d --build postgres app`，`TEST_PASSWORD=e2e-mock node e2e/split-layout-verify.cjs`，跑完 `down`（不加 `-v`）。合并门是分支 CI，合并只做 git 操作。

## 参考实现实测
单测 8/8；`npm test` 257/257；eslint 无告警；验收 27 项全过、pageerror 0。
变异（全部 🔴）：删列表隐藏规则、删 `display:contents`（V7）、恢复网格（V1）、删列表固定宽（V6）、SplitLayout 不渲染 list、`aria-expanded` 取反、不写记忆、移动端生效、空状态生效、开关塞进群名（V4）、三处 `data-shed` 各自丢失、两条让位规则各自删除、去掉桌面限定（V5）。

未验证：Windows 字体下的临界宽度；深色主题观感；docker 构建版上跑验收（我跑的是 Vite 开发服务器）。

## 执行纪律
只改上述范围，范围内新问题直接修，需拍板或会撞车才停下；不自行记账。Skill：`@search-first`、`@incremental-implementation`，按钮视觉可用 `frontend-design`。

## 手动验证（只看截图）
三页收起/展开各一张（浅色、深色）；群聊头部 769 / 900 / 1280 各一张。
