import { AlertTriangle, Close } from './Icon'

export default function ErrorBox({ message, onDismiss }) {
  if (!message) return null

  return (
    <div className="error-box" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 6 }}>
        <AlertTriangle size={14} aria-hidden="true" />
        {message}
      </span>
      {onDismiss && (
        <button
          onClick={onDismiss}
          aria-label="关闭"
          style={{
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            fontSize: 14,
            color: '#a03030',
            padding: '2px 6px',
          }}
        >
          <Close size={12} aria-hidden="true" />
        </button>
      )}
    </div>
  )
}
