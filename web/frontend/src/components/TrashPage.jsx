import { useState, useEffect } from 'react'
import { fetchWithTimeout } from '../api/client'
import HistoryPanel from './HistoryPanel'
import Avatar from './common/Avatar'
import Loading from './common/Loading'
import ConfirmModal from './common/ConfirmModal'

export default function TrashPage() {
  const [tab, setTab] = useState('chat')
  const [cards, setCards] = useState([])
  const [cardsLoading, setCardsLoading] = useState(false)
  const [purgeId, setPurgeId] = useState(null)

  const loadTrashCards = async () => {
    setCardsLoading(true)
    try {
      const res = await fetchWithTimeout('/api/cards/trash')
      const data = await res.json()
      setCards(Array.isArray(data) ? data : data.cards || [])
    } catch { /* ignore */ } finally {
      setCardsLoading(false)
    }
  }

  useEffect(() => {
    if (tab === 'cards') loadTrashCards()
  }, [tab])

  const handleRestore = async (cardId) => {
    try {
      await fetchWithTimeout(`/api/cards/${cardId}/restore`, { method: 'POST' })
      setCards((prev) => prev.filter((c) => c.id !== cardId))
    } catch { /* ignore */ }
  }

  const handlePurge = async () => {
    const id = purgeId
    setPurgeId(null)
    if (!id) return
    try {
      await fetchWithTimeout(`/api/cards/${id}/purge`, { method: 'DELETE' })
      setCards((prev) => prev.filter((c) => c.id !== id))
    } catch { /* ignore */ }
  }

  return (
    <div className="panel" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <header className="panel-header">
        <h1 className="panel-title">回收站</h1>
        <p className="panel-desc">管理已删除的对话和角色卡</p>
      </header>

      <div className="history-tab-bar">
        <button
          type="button"
          className={`history-tab${tab === 'chat' ? ' active' : ''}`}
          onClick={() => setTab('chat')}
        >
          已删除对话
        </button>
        <button
          type="button"
          className={`history-tab${tab === 'cards' ? ' active' : ''}`}
          onClick={() => setTab('cards')}
        >
          已删除角色卡
        </button>
      </div>

      {tab === 'chat' && <HistoryPanel initialTrash />}

      {tab === 'cards' && (
        <>
          {cardsLoading ? (
            <Loading text="加载已删除角色卡…" />
          ) : cards.length === 0 ? (
            <p style={{ textAlign: 'center', color: 'var(--text-dim)', padding: 40, fontSize: 13 }}>
              回收站暂无角色卡
            </p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, padding: '12px 0' }}>
              {cards.map((card) => {
                const cardData = typeof card.card_json === 'string'
                  ? JSON.parse(card.card_json)
                  : card.card_json || {}
                const name = cardData.name || card.name || '?'
                return (
                  <div key={card.id} className="history-swipe-wrapper">
                    <div className="history-swipe-actions">
                      <button
                        type="button"
                        className="history-swipe-restore"
                        onClick={() => handleRestore(card.id)}
                      >
                        恢复
                      </button>
                      <button
                        type="button"
                        className="history-swipe-delete"
                        onClick={() => setPurgeId(card.id)}
                      >
                        彻底删除
                      </button>
                    </div>
                    <div className="history-item" style={{ cursor: 'default' }}>
                      <Avatar name={name} size={40} />
                      <div className="history-item-body">
                        <div className="history-item-head">
                          <span className="history-item-name">{name}</span>
                        </div>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}

      <ConfirmModal
        isOpen={!!purgeId}
        title="彻底删除"
        message="确定彻底删除该角色卡？此操作不可恢复。"
        confirmText="彻底删除"
        onConfirm={handlePurge}
        onCancel={() => setPurgeId(null)}
        danger
      />
    </div>
  )
}
