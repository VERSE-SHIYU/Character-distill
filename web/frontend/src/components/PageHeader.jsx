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
        )}
      </div>
    </div>
  )
}
