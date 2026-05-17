export default function ErrorBox({ message, onDismiss }) {
  if (!message) return null

  return (
    <div className="error-box" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ flex: 1 }}>?? {message}</span>
      {onDismiss && (
        <button
          onClick={onDismiss}
          style={{
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            fontSize: 14,
            color: '#a03030',
            padding: '2px 6px',
          }}
        >
          ?
        </button>
      )}
    </div>
  )
}
