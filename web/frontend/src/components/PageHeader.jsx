export default function PageHeader({ title, onBack, actions }) {
  return (
    <div className="page-header">
      <h1 className="page-header-title">{title}</h1>
      <div className="page-header-actions">
        {actions}
        {onBack && (
          <button type="button" className="page-header-back" onClick={onBack} aria-label="返回">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M19 12H5m7-7-7 7 7 7" />
            </svg>
          </button>
        )}
      </div>
    </div>
  )
}
