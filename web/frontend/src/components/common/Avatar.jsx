const COLORS = ['#C9D8F5', '#E8D87A', '#7EC8C8', '#A8D878', '#D4A0D0', '#F5B87A']

function hashColor(name) {
  let h = 0
  for (let i = 0; i < name.length; i++) {
    h = ((h << 5) - h + name.charCodeAt(i)) | 0
  }
  return COLORS[Math.abs(h) % COLORS.length]
}

export default function Avatar({ name = '?', src = null, size = 40 }) {
  const bg = hashColor(name)
  const letter = name[0] || '?'

  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: '50%',
        background: src ? 'var(--glass-bg)' : bg,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: size * 0.42,
        fontWeight: 700,
        color: '#fff',
        overflow: 'hidden',
        flexShrink: 0,
        border: '2px solid rgba(255,255,255,0.7)',
        boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
      }}
    >
      {src ? (
        <img src={src} alt={name} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
      ) : (
        letter
      )}
    </div>
  )
}
