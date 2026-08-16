import { useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout } from '../api/client'
import useSmoothProgress from '../hooks/useSmoothProgress'
import { Check, Close, RefreshCw, Settings } from './common/Icon'

function DistillTaskItem({ task }) {
  const setView = useAppStore((s) => s.setView)
  const pushView = useAppStore((s) => s.pushView)
  const loadCards = useAppStore((s) => s.loadCards)
  const removeDistillTask = useAppStore((s) => s.removeDistillTask)
  const distillCharacter = useAppStore((s) => s.distillCharacter)
  const displayPct = useSmoothProgress(task.progress_pct, task.status === 'done')

  const isDone = task.status === 'done'
  const isError = task.status === 'error'
  const isQueued = task.status === 'queued' && (task.progress_pct == null || task.progress_pct === 0)
  const statusText = isDone
    ? `${task.character} 蒸馏完成，点击查看`
    : isError
      ? `${task.character || ''} 蒸馏失败: ${task.message || '未知错误'}`
      : task.message || `正在蒸馏 ${task.character || '…'}`

  const strPct = displayPct > 0 ? `${Math.round(displayPct)}%` : '…'
  const showIndeterminate = displayPct <= 5

  const handleDoneClick = async () => {
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

  const handleCancel = (e) => {
    e.stopPropagation()
    if (!isDone && !isError) {
      fetchWithTimeout(`/api/distill/task/${task.id}`, { method: 'DELETE' }).catch(() => {})
    }
    removeDistillTask(task.id)
  }

  const handleRetry = (e) => {
    e.stopPropagation()
    removeDistillTask(task.id)
    distillCharacter(task.textId, task.character)
  }

  return (
    <div
      className={`distill-task-item${isDone ? ' done' : ''}${isError ? ' error' : ''}${isQueued ? ' is-queued' : ''}`}
      onClick={isDone ? handleDoneClick : undefined}
      role={isDone ? 'button' : undefined}
      tabIndex={isDone ? 0 : undefined}
    >
      <span className="distill-task-icon">
        {isDone ? '✅' : isError ? '❌' : isQueued ? '⏳' : '⚙'}
      </span>
      <div className="distill-task-body">
        <span className="distill-task-text">{statusText}</span>
        {isDone && task.awakening && (
          <div className="distill-task-awakening">
            {task.character}：「{task.awakening}」
          </div>
        )}
      </div>
      {!isDone && !isError && (
        <>
          <span className="distill-task-pct">{strPct}</span>
          <span className="distill-task-bar-track">
            <span className={`distill-task-bar-fill${showIndeterminate ? ' indeterminate' : ''}`} style={{ width: `${displayPct}%` }} />
          </span>
          <span className="distill-task-close" onClick={handleCancel} title="取消蒸馏"><Close size={12} /></span>
        </>
      )}
      {(isDone || isError) && (
        <>
          {isError && task.textId && (
            <span className="distill-task-retry" onClick={handleRetry} title="重新蒸馏"><RefreshCw size={12} /></span>
          )}
          <span className="distill-task-close" onClick={handleCancel} title="关闭"><Close size={12} /></span>
        </>
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

  const runningCount = tasks.filter((t) => t.status !== 'done' && t.status !== 'error').length
  const hasQueued = tasks.some((t) => t.status === 'queued')
  const allTerminal = tasks.every((t) => t.status === 'done' || t.status === 'error')

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
