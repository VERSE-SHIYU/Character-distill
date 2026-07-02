import useAppStore from '../store/useAppStore'
import { fetchWithTimeout } from '../api/client'
import useSmoothProgress from '../hooks/useSmoothProgress'

function DistillTaskItem({ task }) {
  const setView = useAppStore((s) => s.setView)
  const loadCards = useAppStore((s) => s.loadCards)
  const removeDistillTask = useAppStore((s) => s.removeDistillTask)
  const distillCharacter = useAppStore((s) => s.distillCharacter)
  const displayPct = useSmoothProgress(task.progress_pct, task.status === 'done')

  const isDone = task.status === 'done'
  const isError = task.status === 'error'
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
      await s.selectText(task.textId)
    } else {
      if (task.textId) await loadCards(task.textId)
      setView('character')
    }
    // After navigation, select the newly generated card
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
      className={`distill-task-item${isDone ? ' done' : ''}${isError ? ' error' : ''}`}
      onClick={isDone ? handleDoneClick : undefined}
      role={isDone ? 'button' : undefined}
      tabIndex={isDone ? 0 : undefined}
    >
      <span className="distill-task-icon">
        {isDone ? '✅' : isError ? '❌' : '⚙'}
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
          <span className="distill-task-close" onClick={handleCancel} title="取消蒸馏">✕</span>
        </>
      )}
      {(isDone || isError) && (
        <>
          {isError && task.textId && (
            <span className="distill-task-retry" onClick={handleRetry} title="重新蒸馏">↻</span>
          )}
          <span className="distill-task-close" onClick={handleCancel} title="关闭">✕</span>
        </>
      )}
    </div>
  )
}

export default function DistillTaskBar() {
  const tasks = useAppStore((s) => s.distillTasks)

  if (tasks.length === 0) return null

  return (
    <div className="distill-task-bar">
      {tasks.map((t) => (
        <DistillTaskItem key={t.id} task={t} />
      ))}
    </div>
  )
}
