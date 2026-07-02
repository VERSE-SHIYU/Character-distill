import useAppStore from '../store/useAppStore'
import Avatar from './common/Avatar'

export default function AwakeningToast() {
  const toast = useAppStore((s) => s.awakeningToast)
  const dismiss = useAppStore((s) => s.dismissAwakeningToast)
  const viewCard = useAppStore((s) => s.viewCard)
  const loadCards = useAppStore((s) => s.loadCards)
  const selectText = useAppStore((s) => s.selectText)
  const setView = useAppStore((s) => s.setView)

  if (!toast) return null

  const handleGoSee = async () => {
    const { card_id, textId } = toast
    if (textId) {
      await selectText(textId)
      await loadCards(textId)
      // find and select the card
      const state = useAppStore.getState()
      const { cards } = state
      const card = cards.find((c) => c.id === card_id)
        || cards.find((c) => c.name === toast.character)
      if (card) {
        viewCard(card)
        setView('character')
      }
    }
    dismiss()
  }

  return (
    <div className="awakening-toast">
      <button className="awakening-toast-close" onClick={dismiss} aria-label="关闭">✕</button>
      <div className="awakening-toast-body">
        <div className="awakening-toast-avatar">
          <Avatar name={toast.character} size={40} />
        </div>
        <div className="awakening-toast-content">
          <div className="awakening-toast-name">{toast.character}</div>
          <div className="awakening-toast-line">{toast.awakening}</div>
          <button className="awakening-toast-btn" onClick={handleGoSee}>去见 TA</button>
        </div>
      </div>
    </div>
  )
}
