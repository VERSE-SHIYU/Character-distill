import useAppStore from '../store/useAppStore'

export default function DistillTaskBar() {
  const tasks = useAppStore((s) => s.distillTasks)
  const setView = useAppStore((s) => s.setView)

  if (tasks.length === 0) return null

  return (
    <div className="distill-task-bar">
      {tasks.map((t) => {
        const isDone = t.status === 'done'
        const isError = t.status === 'error'
        return (
          <div
            key={t.id}
            className={`distill-task-item${isDone ? ' done' : ''}${isError ? ' error' : ''}`}
            onClick={() => { if (isDone) setView('character') }}
            role={isDone ? 'button' : undefined}
            tabIndex={isDone ? 0 : undefined}
          >
            <span className="distill-task-icon">
              {isDone ? '✅' : isError ? '❌' : '⚙'}
            </span>
            <span className="distill-task-text">
              {isDone
                ? `${t.character} 蒸馏完成，点击查看`
                : isError
                  ? `${t.character || ''} 蒸馏失败: ${t.message || '未知错误'}`
                  : `正在蒸馏 ${t.character || '…'} ${t.progress_pct || 0}%`}
            </span>
            {!isDone && !isError && (
              <span className="distill-task-bar-track">
                <span className="distill-task-bar-fill" style={{ width: `${t.progress_pct || 0}%` }} />
              </span>
            )}
          </div>
        )
      })}
    </div>
  )
}
