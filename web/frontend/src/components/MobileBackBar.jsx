import useAppStore from '../store/useAppStore'
import { SECONDARY_VIEWS, TITLE_MAP } from '../config/navigation'

export default function MobileBackBar() {
  const currentView = useAppStore((s) => s.currentView)
  const popView = useAppStore((s) => s.popView)
  const inConversation = useAppStore((s) => s.inConversation)

  if (!SECONDARY_VIEWS.includes(currentView) || currentView === 'chat' || inConversation) return null

  return (
    <>
      <div className="mobile-backbar">
        <button
          type="button"
          className="mobile-backbar-btn"
          onClick={popView}
          aria-label="返回"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M19 12H5m7-7-7 7 7 7" />
          </svg>
        </button>
        <span className="mobile-backbar-title">{TITLE_MAP[currentView] || ''}</span>
      </div>
      <div className="mobile-backbar-placeholder" />
    </>
  )
}

export { SECONDARY_VIEWS }
