import { getIcon } from './Icon'

export default function EntryList({ entries, flags = {}, badge, onAction }) {
  return (
    <div className="entry-list">
      {entries
        .filter((entry) => !entry.requires || flags[entry.requires])
        .map((entry) => {
          const IconComp = getIcon(entry.icon)
          const count = entry.badge && badge ? badge : null
          return (
            <button
              key={entry.key}
              type="button"
              className="entry-list-item"
              onClick={() => onAction?.(entry.key, entry.view)}
            >
              <span className="entry-list-icon-wrap">
                <IconComp size={20} />
                {count != null && count > 0 && (
                  <span className="entry-list-badge">{count > 99 ? '99+' : count}</span>
                )}
              </span>
              <span className="entry-list-label">{entry.label}</span>
              <svg className="entry-list-arrow" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6" /></svg>
            </button>
          )
        })}
    </div>
  )
}
