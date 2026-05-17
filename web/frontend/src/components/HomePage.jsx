import useAppStore from '../store/useAppStore'

export default function HomePage() {
  const texts = useAppStore((s) => s.texts)
  const setView = useAppStore((s) => s.setView)

  return (
    <div className="home-page panel">
      <header className="panel-header">
        <h1 className="panel-title">角色蒸馏</h1>
        <p className="panel-desc">上传小说文本，AI 自动识别角色并生成角色卡</p>
      </header>

      <div className="home-actions">
        {texts.length === 0 ? (
          <div className="home-empty">
            <p className="home-empty-icon">{'\u{1F4DC}'}</p>
            <p className="home-empty-text">还没有上传任何文本</p>
            <button
              type="button"
              className="btn-primary"
              onClick={() => setView('text')}
            >
              上传第一份文本
            </button>
          </div>
        ) : (
          <div className="home-stats">
            <p className="home-stat-count">
              <span className="home-stat-num">{texts.length}</span> 份文本
            </p>
            <div className="home-action-row">
              <button
                type="button"
                className="btn-primary"
                onClick={() => setView('text')}
              >
                文本管理
              </button>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setView('history')}
              >
                历史记录
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
