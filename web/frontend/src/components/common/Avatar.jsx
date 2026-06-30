const COLORS = ['#C9D8F5', '#E8D87A', '#7EC8C8', '#A8D878', '#D4A0D0', '#F5B87A']

function hashColor(name) {
  let h = 0
  for (let i = 0; i < name.length; i++) {
    h = ((h << 5) - h + name.charCodeAt(i)) | 0
  }
  return COLORS[Math.abs(h) % COLORS.length]
}

export default function Avatar({ name = '?', src = null, size = 40, className = '', onClick, style }) {
  const bg = hashColor(name)
  const letter = name[0] || '?'

  return (
    <div
      className={className}
      onClick={onClick}
      style={{
        width: size,
        height: size,
        borderRadius: 'var(--avatar-radius)',
        background: src ? 'transparent' : bg,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: size * 0.42,
        fontWeight: 700,
        color: '#fff',
        overflow: 'hidden',
        flexShrink: 0,
        position: 'relative',
        border: '2px solid rgba(128,128,128,0.15)',
        boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
        cursor: onClick ? 'pointer' : undefined,
        ...style,
      }}
    >
      {src ? (
        <img
          src={src}
          alt={name}
          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
          onError={(e) => { e.target.style.display = 'none'; if (e.target.nextSibling) e.target.nextSibling.style.display = 'flex' }}
        />
      ) : null}
      <span style={{
        display: src ? 'none' : 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: '100%',
        height: '100%',
        position: 'absolute',
        borderRadius: 'var(--avatar-radius)',
        background: bg,
      }}>
        {letter}
      </span>
    </div>
  )
}
