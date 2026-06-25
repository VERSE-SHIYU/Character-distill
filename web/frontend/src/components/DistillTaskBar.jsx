import useAppStore from '../store/useAppStore'
import { fetchWithTimeout } from '../api/client'

function getStore() { return useAppStore.getState() }

export default function DistillTaskBar() {
  const tasks = useAppStore((s) => s.distillTasks)
  const setView = useAppStore((s) => s.setView)
  const removeDistillTask = useAppStore((s) => s.removeDistillTask)
  const distillCharacter = useAppStore((s) => s.distillCharacter)
  const loadCards = useAppStore((s) => s.loadCards)

  if (tasks.length === 0) return null

  return (
    <div className="distill-task-bar">
      {tasks.map((t) => {
        const isDone = t.status === 'done'
        const isError = t.status === 'error'
        const statusText = isDone
          ? `${t.character} 蒸馏完成，点击查看`
          : isError
            ? `${t.character || ''} 蒸馏失败: ${t.message || '未知错误'}`
            : t.message || `正在蒸馏 ${t.character || '…'}`
        return (
          <div
            key={t.id}
            className={`distill-task-item${isDone ? ' done' : ''}${isError ? ' error' : ''}`}
            onClick={() => {
              if (isDone) {

                const s = getStore()
                if (t.textId && s.currentTextId !== t.textId) {

                  s.selectText(t.textId)
                } else {
                  if (t.textId) s.loadCards(t.textId)
                  s.setView('character')
                }
              }
            }}
            role={isDone ? 'button' : undefined}
            tabIndex={isDone ? 0 : undefined}
          >
            <span className="distill-task-icon">
              {isDone ? '✅' : isError ? '❌' : '⚙'}
            </span>
            <span className="distill-task-text">{statusText}</span>
            {!isDone && !isError && t.progress_pct > 5 && (
              <span className="distill-task-pct">{t.progress_pct}%</span>
            )}
            {!isDone && !isError && (
              <span className="distill-task-bar-track">
                <span className={`distill-task-bar-fill${(!t.progress_pct || t.progress_pct <= 5) ? ' indeterminate' : ''}`} style={{ width: `${t.progress_pct || 0}%` }} />
              </span>
            )}
            {!isDone && !isError && (
              <span
                className="distill-task-close"
                onClick={(e) => {
                  e.stopPropagation()
                  fetchWithTimeout(`/api/distill/task/${t.id}`, { method: 'DELETE' }).catch(() => {})
                  removeDistillTask(t.id)
                }}
                title="取消蒸馏"
              >
                ✕
              </span>
            )}
            {(isDone || isError) && (
              <>
                {isError && t.textId && (
                  <span
                    className="distill-task-retry"
                    onClick={(e) => {
                      e.stopPropagation()
                      removeDistillTask(t.id)
                      distillCharacter(t.textId, t.character)
                    }}
                    title="重新蒸馏"
                  >
                    ↻
                  </span>
                )}
                <span
                  className="distill-task-close"
                  onClick={(e) => { e.stopPropagation(); removeDistillTask(t.id) }}
                  title="关闭"
                >
                  ✕
                </span>
              </>
            )}
          </div>
        )
      })}
    </div>
  )
}
