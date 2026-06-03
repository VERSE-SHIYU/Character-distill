import Avatar from './Avatar'

const MAX_ITEMS = 6

export default function MentionDropdown({ show, items, selectedIndex, onSelect, position }) {
  if (!show || !items || items.length === 0) return null

  return (
    <div
      className="mention-dropdown"
      style={{
        position: 'absolute',
        left: position?.left || 0,
        bottom: position?.bottom || '100%',
        marginBottom: 4,
        zIndex: 1000,
        background: 'color-mix(in srgb, var(--card-bg) 98%, transparent)',
        backdropFilter: 'blur(12px)',
        border: '1px solid var(--border-color)',
        borderRadius: 10,
        boxShadow: '0 4px 16px rgba(0,0,0,0.15)',
        minWidth: 200,
        maxHeight: MAX_ITEMS * 52 + 8,
        overflowY: 'auto',
        padding: 4,
      }}
    >
      {items.slice(0, MAX_ITEMS).map((item, i) => (
        <button
          key={item.id || item.name}
          type="button"
          className={`mention-item${i === selectedIndex ? ' mention-item-active' : ''}`}
          onMouseDown={(e) => { e.preventDefault(); onSelect(item) }}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            width: '100%',
            padding: '6px 8px',
            border: 'none',
            borderRadius: 6,
            background: i === selectedIndex ? 'var(--accent-bg, rgba(99,102,241,0.1))' : 'transparent',
            color: 'var(--text-color)',
            cursor: 'pointer',
            fontSize: 14,
            textAlign: 'left',
          }}
        >
          <Avatar name={item.name || '?'} src={item.avatar} size={32} />
          <span style={{ fontWeight: i === selectedIndex ? 600 : 400 }}>{item.name}</span>
        </button>
      ))}
    </div>
  )
}
