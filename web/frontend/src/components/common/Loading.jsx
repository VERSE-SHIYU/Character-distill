export default function Loading({ text = 'Loading...' }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: 16 }}>
      <div className="loading-dots">
        <span /><span /><span />
      </div>
      <span style={{ fontSize: 13, color: 'var(--text-dim)' }}>{text}</span>
    </div>
  )
}
