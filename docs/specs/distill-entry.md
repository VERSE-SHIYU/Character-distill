# Spec：蒸馏工作台固定入口

基线 `main` `40cd7b0`。只改前端。改动全文在同目录 `distill-entry.diff`（含测试），已在沙箱基于该基线实跑。

## 目标
侧栏加「蒸馏」项；角色管理页（CharCard）头部加「蒸馏工作台」按钮；与「创作→角色」已有按钮共用一个组件。

## 已查实约束（S0 重跑下面扫描，与原始输出比对，不一致即停）
- 已有入口只有 `TextPanel.jsx:586`（页面按钮）与 `DistillTaskBar.jsx:193`（浮动任务面板，另一种形态，不动）
- `CharCard.jsx:81` 的 `PageHeader` 支持 `actions` 插槽（`PageHeader.jsx:3`）
- 侧栏 `isActive` / `handleNav` 的 default 分支即 `currentView === id` / `setView(id)`（`Sidebar.jsx:80,89`），新项无需加 case
- 图标 `Terminal` 与管理后台「蒸馏任务」一致（`AdminPanel.jsx:31`）；按钮复用 `.dw-entry-btn`（`global.css:19013`），无新 CSS

扫描命令与原始输出：
```
echo "== distillWorkbench 全部引用"; grep -rn "distillWorkbench" src --include=*.js --include=*.jsx | grep -v __tests__
echo "== dw-entry-btn 全部引用"; grep -rn "dw-entry-btn" src
echo "== Sidebar NAV_ITEMS 与分支"; grep -n "id: '\|case '\|default:" src/components/Sidebar.jsx
echo "== CharCard 头部"; grep -n "PageHeader" src/components/CharCard.jsx
echo "== PageHeader 签名"; sed -n 1,12p src/components/PageHeader.jsx
echo "== 现有 Sidebar / TextPanel / CharCard 测试"; ls src/components/__tests__ | grep -iE "sidebar|textpanel|charcard"
```
```
== distillWorkbench 全部引用
src/components/DistillTaskBar.jsx:193:              <button className="distill-panel-wb" onClick={() => { setCollapsed(true); pushView('distillWorkbench') }}>工作台</button>
src/components/TextPanel.jsx:586:        <button type="button" className="dw-entry-btn" onClick={() => pushView('distillWorkbench')}>蒸馏工作台</button>
src/config/navigation.js:6:  'messages', 'history', 'groupChat', 'feed', 'market', 'distillWorkbench',
src/config/navigation.js:16:  distillWorkbench: 'text',
src/App.jsx:69:  distillWorkbench: DistillWorkbench,
== dw-entry-btn 全部引用
src/components/TextPanel.jsx:586:        <button type="button" className="dw-entry-btn" onClick={() => pushView('distillWorkbench')}>蒸馏工作台</button>
src/styles/global.css:19013:.dw-entry-btn {
src/styles/global.css:19019:.dw-entry-btn:hover { background: var(--accent-soft); }
== Sidebar NAV_ITEMS 与分支
15:    id: 'home',
20:    id: 'workbench',
25:    id: 'groupChat',
31:    id: 'history',
36:    id: 'market',
41:    id: 'trash',
47:    id: 'feed',
52:    id: 'mine',
75:      case 'feed': return currentView === 'feed'
76:      case 'workbench': return ['text', 'character', 'chat'].includes(currentView)
77:      case 'market': return ['market', 'author', 'textDetail'].includes(currentView)
78:      case 'history': return currentView === 'history'
79:      case 'mine': return ['mine', 'messages', 'admin'].includes(currentView)
80:      default: return currentView === id
86:      case 'workbench': setView('text'); break   // tab-level: sidebar nav
87:      case 'trash': setView('trash'); break       // tab-level: sidebar nav
88:      case 'mine': setView('mine'); break          // tab-level: sidebar nav
89:      default: setView(id)                         // tab-level: sidebar nav
95:      ? [...NAV_ITEMS, { id: 'admin', icon: <Shield size={20} />, label: '管理' }]
== CharCard 头部
13:import PageHeader from './PageHeader'
81:          <PageHeader title="角色管理" onBack={goBack} />
118:          <PageHeader title={currentCard.name || '角色详情'} onBack={() => viewCard(null)} />
== PageHeader 签名
import { ArrowLeft } from './common/Icon'

export default function PageHeader({ title, onBack, actions }) {
  return (
    <div className="page-header">
      <h1 className="page-header-title">{title}</h1>
      <div className="page-header-actions">
        {actions}
        {onBack && (
          <button type="button" className="page-header-back" onClick={onBack} aria-label="返回">
            <ArrowLeft size={20} />
          </button>
== 现有 Sidebar / TextPanel / CharCard 测试
CharCardCharacterArc.test.jsx
CharCardPublishError.test.jsx
TextPanelDeleteError.test.jsx
```

## 改动
1. 新建 `common/DistillWorkbenchButton.jsx`（文案与跳转唯一定义处）
2. `TextPanel.jsx:586`、`CharCard.jsx:81` 改用它
3. `Sidebar.jsx` NAV_ITEMS 在「创作」后加 `{ id: 'distillWorkbench', icon: <Terminal size={20} />, label: '蒸馏' }`

触发链：点侧栏「蒸馏」→ handleNav → setView('distillWorkbench') → 该项高亮；点页面按钮 → pushView('distillWorkbench') → ← 返回原页。

## 测试与对账（我已实跑）
`DistillWorkbenchEntry.test.jsx` D1–D5；`npm test` 262/262；eslint 无告警。

| 调用点 | 可观测输出 | 测试 | 预跑变异 → 结果 |
|---|---|---|---|
| 共用按钮 | 类名、跳转目标 | D1 | K1 跳错视图、K2 换类名 → 🔴 |
| 侧栏 | 有该项、点击跳转 | D2 | K3 删项、K5 拦截跳转 → 🔴 |
| 侧栏 | 仅在工作台时高亮 | D3 | K4 高亮恒真 → 🔴 |
| 角色管理头部 | 有按钮 | D4 | K6 不接按钮 → 🔴 |
| 创作→角色头部 | 有按钮且可跳转 | D5 | K7 不接按钮 → 🔴 |

不需要 docker、不需要 e2e：行为全在 jsdom 可测；外观复用现有类，只需一张截图判断。

## 执行
`git apply --check` 通过后 `git apply`；跑 D1–D5、`npm test`、eslint；按 组件 / 接线 / 测试 分 commit，推送，开 PR 合入 main。
