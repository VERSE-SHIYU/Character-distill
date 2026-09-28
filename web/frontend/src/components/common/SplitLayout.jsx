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
