import { useCallback, useEffect, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout, getAuthHeaders } from '../api/client'
import Avatar from './common/Avatar'
import Loading from './common/Loading'
import ErrorBox from './common/ErrorBox'

export default function AuthorPage() {
  const setView = useAppStore((s) => s.setView)
  const authorUserId = useAppStore((s) => s.authorUserId)
  const authUser = useAppStore((s) => s.authUser)
  const startChat = useAppStore((s) => s.startChat)

  const [author, setAuthor] = useState(null)
  const [cards, setCards] = useState([])
  const [isFollowing, setIsFollowing] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!authorUserId) { setView('market'); return }
    ;(async () => {
      setLoading(true)
      try {
        const res = await fetchWithTimeout(`/api/market/author/${authorUserId}`)
        const data = await res.json()
        setAuthor(data.author)
        setCards(data.cards || [])
        setIsFollowing(data.is_following || false)
      } catch (err) {
        setError(err.message)
      } finally {
        setLoading(false)
      }
    })()
  }, [authorUserId])

  const handleFollow = async () => {
    try {
      const res = await fetchWithTimeout(`/api/market/author/${authorUserId}/follow`, {
        method: 'POST',
        headers: { ...getAuthHeaders() },
      })
      const data = await res.json()
      setIsFollowing(data.following)
    } catch (err) {
      console.error('Follow failed:', err)
    }
  }

  return (
    <div className="panel author-page">
      <header className="panel-header">
        <button type="button" className="chat-back-btn" onClick={() => setView('market')} title="返回市场">
          {'◀'}
        </button>
        <h1 className="panel-title">作者主页</h1>
      </header>

      {error && <ErrorBox message={error} onDismiss={() => setError(null)} />}

      {loading ? (
        <Loading text="加载作者信息…" />
      ) : author ? (
        <>
          <div className="author-hero">
            <Avatar name={author.username || '?'} size={64} />
            <div className="author-hero-text">
              <h2 className="author-name">{author.username}</h2>
              <p className="author-meta">{cards.length} 个公开角色</p>
            </div>
            {authUser?.id !== authorUserId && (
              <button
                type="button"
                className={`btn-primary${isFollowing ? ' btn-secondary' : ''}`}
                style={{ marginLeft: 'auto' }}
                onClick={handleFollow}
              >
                {isFollowing ? '已关注' : '关注'}
              </button>
            )}
          </div>

          <div className="author-cards-section">
            <h3 className="author-cards-title">公开角色</h3>
            {cards.length === 0 ? (
              <p style={{ textAlign: 'center', color: 'var(--text-dim)', padding: 40 }}>暂无公开角色</p>
            ) : (
              <div className="market-grid">
                {cards.map((card) => {
                  const cardData = typeof card.card_json === 'string'
                    ? JSON.parse(card.card_json)
                    : card.card_json || {}
                  const name = cardData.name || card.name || '?'
                  const identity = cardData.identity || ''
                  return (
                    <div key={card.id} className="market-card">
                      <div className="market-card-body">
                        <h3 className="market-card-name">{name}</h3>
                        {identity && <p className="market-card-identity">{identity}</p>}
                      </div>
                      <div className="market-card-footer">
                        <span className="market-card-likes">{'\u{2764}'} {card.likes || 0}</span>
                        <button
                          type="button"
                          className="btn-primary btn-sm"
                          onClick={async () => {
                            const res = await fetchWithTimeout(`/api/market/${card.id}/fork`, {
                              method: 'POST',
                              headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
                              body: JSON.stringify({ text_id: '' }),
                            })
                            const data = await res.json()
                            if (data.card) startChat(data.card)
                          }}
                        >
                          使用
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </>
      ) : (
        <p style={{ textAlign: 'center', color: 'var(--text-dim)', padding: 40 }}>用户不存在</p>
      )}
    </div>
  )
}
