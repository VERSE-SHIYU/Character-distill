import { useEffect, useRef, useState } from 'react'
import useAppStore, { isTerminal, taskActions } from '../store/useAppStore'
import { fetchWithTimeout } from '../api/client'
import useSmoothProgress from '../hooks/useSmoothProgress'
import { Check, Close, Clock, Play, RefreshCw, Settings, Zap } from './common/Icon'

// 终态三态各自的展示表。查表而非散落 `status === 'done'` 之类的终态谓词 —— 是否终态
// 只读 done；status 仅在"已终结"之后用来挑具体文案/图标（§3.2 的三分支要求）。
const TERMINAL_VIEW = {
  done: {
    cls: ' done',
    icon: <Check size={16} />,
    text: (t) => `${t.character} 蒸馏完成，点击查看`,
    openable: true,
  },
  error: {
    cls: ' error',
    icon: <Close size={16} />,
    text: (t) => `${t.character || ''} 蒸馏失败: ${t.message || '未知错误'}`,
    openable: false,
  },
  interrupted: {
    // 复用 error 视觉（无独立 .interrupted 规则，且主题覆盖只认 .error/.done）；
    // 靠图标 + 文案 + 可续跑动作与失败区分。
    cls: ' error',
    icon: <Clock size={16} />,
    text: (t) => `${t.character || ''} 蒸馏已中断，可继续蒸馏`,
    openable: false,
  },
}

function DistillTaskItem({ task }) {
  const setView = useAppStore((s) => s.setView)
  const pushView = useAppStore((s) => s.pushView)
  const loadCards = useAppStore((s) => s.loadCards)
  const removeDistillTask = useAppStore((s) => s.removeDistillTask)
  const distillCharacter = useAppStore((s) => s.distillCharacter)
  const done = isTerminal(task)
  const actions = taskActions(task)
  const displayPct = useSmoothProgress(task.progress_pct, done)

  const view = done ? TERMINAL_VIEW[task.status] : null
  const statusText = view ? view.text(task) : (task.message || `正在蒸馏 ${task.character || '…'}`)

  const strPct = displayPct > 0 ? `${Math.round(displayPct)}%` : '…'
  const showIndeterminate = displayPct <= 5

  const handleOpen = async () => {
    const s = useAppStore.getState()
    const { card_id } = task
    if (task.textId && s.currentTextId !== task.textId) {
      s.pushView('character')
      await s.selectText(task.textId)
    } else {
      if (task.textId) await loadCards(task.textId)
      pushView('character')
    }
    if (card_id) {
      const state = useAppStore.getState()
      const card = state.cards.find(c => c.id === card_id)
      if (card) state.viewCard(card)
    }
  }

  // 取消 = 服务端动作（actions 含 cancel 才发 DELETE）+ 本地移出列表
  const handleCancel = (e) => {
    e.stopPropagation()
    if (actions.includes('cancel')) {
      fetchWithTimeout(`/api/distill/task/${task.id}`, { method: 'DELETE' }).catch(() => {})
    }
    removeDistillTask(task.id)
  }

  // resume 与 retry 打同一个端点（POST /api/distill/start），续跑 vs 整批重跑由后端
  // 任务级门裁决 —— 前端不判断，二者只差按钮文案。
  const handleRestart = (e) => {
    e.stopPropagation()
    removeDistillTask(task.id)
    distillCharacter(task.textId, task.character)
  }

  // 本地关闭：纯列表管理，不是服务端动作，故不受 actions 管辖
  const handleDismiss = (e) => {
    e.stopPropagation()
    removeDistillTask(task.id)
  }

  return (
    <div
      className={`distill-task-item${view ? view.cls : ''}`}
      onClick={view?.openable ? handleOpen : undefined}
      role={view?.openable ? 'button' : undefined}
      tabIndex={view?.openable ? 0 : undefined}
    >
      <span className="distill-task-icon">{view ? view.icon : <Zap size={16} />}</span>
      <div className="distill-task-body">
        <span className="distill-task-text">{statusText}</span>
        {done && task.awakening && (
          <div className="distill-task-awakening">
            {task.character}：「{task.awakening}」
          </div>
        )}
      </div>
      {!done && (
        <>
          <span className="distill-task-pct">{strPct}</span>
          <span className="distill-task-bar-track">
            <span className={`distill-task-bar-fill${showIndeterminate ? ' indeterminate' : ''}`} style={{ width: `${displayPct}%` }} />
          </span>
        </>
      )}
      {actions.includes('cancel') && (
        <span className="distill-task-close" onClick={handleCancel} title="取消蒸馏"><Close size={12} /></span>
      )}
      {actions.includes('resume') && (
        <span className="distill-task-retry" onClick={handleRestart} title="继续蒸馏"><Play size={12} /></span>
      )}
      {actions.includes('retry') && (
        <span className="distill-task-retry" onClick={handleRestart} title="重新蒸馏"><RefreshCw size={12} /></span>
      )}
      {done && (
        <span className="distill-task-close" onClick={handleDismiss} title="关闭"><Close size={12} /></span>
      )}
    </div>
  )
}

export default function DistillTaskBar() {
  const tasks = useAppStore((s) => s.distillTasks)
  const pushView = useAppStore((s) => s.pushView)

  const [collapsed, setCollapsed] = useState(true)
  const [shake, setShake] = useState(false)
  const prevCountRef = useRef(tasks.length)
  const panelRef = useRef(null)

  // Track task count changes to trigger shake on new arrivals
  useEffect(() => {
    if (tasks.length > prevCountRef.current && tasks.length > 0 && collapsed) {
      setShake(true)
    }
    prevCountRef.current = tasks.length
  }, [tasks.length, collapsed])

  // Dismiss panel on outside mousedown
  useEffect(() => {
    if (collapsed) return
    const handler = (e) => {
      if (panelRef.current && !panelRef.current.contains(e.target)) {
        setCollapsed(true)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [collapsed])

  if (tasks.length === 0) return null

  const runningCount = tasks.filter((t) => !isTerminal(t)).length
  // 尚无服务端动作 = 刚建、还没拿到第一次响应 → 徽标脉冲（仅视觉，不参与任何判定）
  const hasQueued = tasks.some((t) => !isTerminal(t) && taskActions(t).length === 0)
  const allTerminal = tasks.every(isTerminal)

  const fabClass = `distill-fab${shake ? ' shake' : ''}`

  return (
    <>
      {collapsed ? (
        <button
          className={fabClass}
          onClick={() => setCollapsed(false)}
          onAnimationEnd={() => setShake(false)}
          title="蒸馏任务"
        >
          <span className="distill-fab-icon">{allTerminal ? <Check size={16} /> : <Settings size={16} />}</span>
          <span className={`distill-fab-badge${hasQueued ? ' badge-pulse' : ''}`}>
            {allTerminal ? <Check size={11} /> : runningCount}
          </span>
        </button>
      ) : (
        <div className="distill-panel" ref={panelRef}>
          <div className="distill-panel-header">
            <span className="distill-panel-title">蒸馏任务 ({tasks.length})</span>
            <div className="distill-panel-actions">
              <button className="distill-panel-wb" onClick={() => { setCollapsed(true); pushView('distillWorkbench') }}>工作台</button>
              <button className="distill-panel-close" onClick={() => setCollapsed(true)}><Close size={14} /></button>
            </div>
          </div>
          <div className="distill-panel-body">
            {tasks.map((t) => (
              <DistillTaskItem key={t.id} task={t} />
            ))}
          </div>
        </div>
      )}
    </>
  )
}
