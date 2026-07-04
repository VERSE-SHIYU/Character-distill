import { getIcon } from './Icon'

export default function EntryGrid({ entries, columns = 4, badge, onAction }) {
  return (
    <div className="entry-grid" style={{ gridTemplateColumns: `repeat(${columns}, 1fr)` }}>
      {entries.map((entry) => {
        const IconComp = getIcon(entry.icon)
        const count = entry.badge && badge ? badge : null
        return (
          <button
            key={entry.key}
            type="button"
            className="entry-grid-item"
            onClick={() => onAction?.(entry.key, entry.view)}
          >
            <span className="entry-grid-icon-wrap">
              <IconComp size={26} />
              {count != null && count > 0 && (
                <span className="entry-grid-badge">{count > 99 ? '99+' : count}</span>
              )}
            </span>
            <span className="entry-grid-label">{entry.label}</span>
          </button>
        )
      })}
    </div>
  )
}
