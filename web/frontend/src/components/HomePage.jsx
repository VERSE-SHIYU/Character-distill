import { useCallback, useEffect, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout } from '../api/client'
import Avatar from './common/Avatar'
import RoleSetupModal from './RoleSetupModal'
import { loadCardAvatar } from '../store/db'

function fmtTime(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    const now = new Date()
    const diffMs = now - d
    const diffMin = Math.floor(diffMs / 60000)
    if (diffMin < 1) return '刚刚'
    if (diffMin < 60) return `${diffMin}分钟前`
    const diffHour = Math.floor(diffMin / 60)
    if (diffHour < 24) return `${diffHour}小时前`
    const diffDay = Math.floor(diffHour / 24)
    if (diffDay < 7) return `${diffDay}天前`
    return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
  } catch {
    return ''
  }
}

function previewText(text, max = 60) {
  if (!text) return ''
  const one = text.replace(/\s+/g, ' ').trim()
  return one.length > max ? `${one.slice(0, max)}…` : one
}

function truncate(str, max) {
  if (!str) return ''
  return str.length > max ? `${str.slice(0, max)}…` : str
}

function parseCardJson(card) {
  if (!card) return {}
  if (typeof card.card_json === 'string') {
    try { return JSON.parse(card.card_json) } catch { return {} }
  }
  return card.card_json || card
}

export default function HomePage() {
  const texts = useAppStore((s) => s.texts)
  const loadTexts = useAppStore((s) => s.loadTexts)
  const startChat = useAppStore((s) => s.startChat)
  const resumeSession = useAppStore((s) => s.resumeSession)
  const setView = useAppStore((s) => s.setView)
  const apiConfigured = useAppStore((s) => s.apiConfigured)
  const authUser = useAppStore((s) => s.authUser)

  const [allCards, setAllCards] = useState([])
  const [cardsLoading, setCardsLoading] = useState(true)
  const [recentSessions, setRecentSessions] = useState([])
  const [resumingId, setResumingId] = useState(null)
  const [pendingCard, setPendingCard] = useState(null)
  const cardAvatars = useAppStore((s) => s.cardAvatars)
  const setCardAvatar = useAppStore((s) => s.setCardAvatar)

  useEffect(() => {
    if (texts.length === 0) loadTexts()
  }, [])

  const loadAllCards = useCallback(async () => {
    if (texts.length === 0) { setAllCards([]); setCardsLoading(false); return }
    setCardsLoading(true)
    try {
      const results = await Promise.all(
        texts.map((t) =>
          fetchWithTimeout(`/api/distill/cards/by-text/${t.id}`)
            .then((r) => r.json())
            .catch(() => []),
        ),
      )
      const merged = []
      const seen = new Set()
      for (const list of results) {
        for (const card of list) {
          const key = card.id || card.card_id
          if (!seen.has(key)) { seen.add(key); merged.push(card) }
        }
      }
      setAllCards(merged)
    } catch {} finally { setCardsLoading(false) }
  }, [texts])

  useEffect(() => { loadAllCards() }, [loadAllCards])

  // Load card avatars for the grid and recent sessions
  useEffect(() => {
    const ids = new Set()
    allCards.forEach((c) => { if (c.id || c.card_id) ids.add(c.id || c.card_id) })
    recentSessions.forEach((s) => { if (s.card_id) ids.add(s.card_id) })
    ids.forEach((id) => {
      if (!cardAvatars[id]) {
        loadCardAvatar(id).then((dataUrl) => {
          if (dataUrl) setCardAvatar(id, dataUrl)
        })
      }
    })
  }, [allCards, recentSessions])

  useEffect(() => {
    let cancelled = false
    fetchWithTimeout('/api/history/list?page=1&page_size=4')
      .then((r) => r.json())
      .then((data) => { if (!cancelled) setRecentSessions(data.items || []) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [])

  const handleCardClick = (card) => {
    const cardData = parseCardJson(card)
    const chatCard = { ...card, ...cardData, text_id: card.text_id, session_id: null }
    setPendingCard(chatCard)
  }

  const handleResume = async (sessionId) => {
    setResumingId(sessionId)
    try { await resumeSession(sessionId) } catch { setResumingId(null) }
  }

  const cardCount = allCards.length
  const textCount = texts.length

  return (
    <div className="home-page panel">
      {/* API Key alert */}
      {!apiConfigured && authUser && (
        <div className="api-config-alert" style={{ marginBottom: 16, cursor: 'pointer' }} onClick={() => setView('settings')}>
          请先配置 API Key 才能开始对话
        </div>
      )}

      {/* Stats bar */}
      <div className="home-stats-bar">
        <div className="home-stats-item">
          <span className="home-stats-num">{cardCount}</span>
          <span className="home-stats-label"> 个角色</span>
        </div>
        <div className="home-stats-divider" />
        <div className="home-stats-item">
          <span className="home-stats-num">{recentSessions.length > 0 ? recentSessions.length : '-'}</span>
          <span className="home-stats-label"> 次对话</span>
        </div>
        <div className="home-stats-divider" />
        <div className="home-stats-item">
          <span className="home-stats-num">{textCount}</span>
          <span className="home-stats-label"> 份文本</span>
        </div>
      </div>

      {/* Character card grid */}
      <div className="home-card-section">
        <h2 className="home-section-title">角色卡片</h2>
        {cardsLoading ? (
          <div className="admin-loading">加载中…</div>
        ) : allCards.length === 0 ? (
          <div className="home-no-chars">
            <p style={{ fontSize: 14, marginBottom: 12, color: 'var(--text-dim)' }}>还没有角色，去蒸馏一个</p>
            <button type="button" className="btn-primary" onClick={() => setView('text')}>
              上传文本开始蒸馏
            </button>
          </div>
        ) : (
          <>
          <div className="home-char-grid">
            {allCards.slice(0, 4).map((card) => {
              const data = parseCardJson(card)
              const name = data.name || card.name || '?'
              const identity = data.identity || ''
              return (
                <button
                  key={card.id || card.card_id}
                  type="button"
                  className="home-char-card"
                  onClick={() => handleCardClick(card)}
                >
                  <Avatar name={name} size={56} src={cardAvatars[card.id || card.card_id]} />
                  <div className="home-char-card-text">
                    <span className="home-char-name">{name}</span>
                    {identity && <span className="home-char-identity">{truncate(identity, 20)}</span>}
                  </div>
                </button>
              )
            })}
            {allCards.length > 4 && (
              <button
                type="button"
                className="home-char-card home-char-card-more"
                onClick={() => setView('text')}
              >
                <span style={{ fontSize: 24 }}>{'\u{203A}'}</span>
                <span style={{ fontSize: 14, color: 'var(--text-dim)' }}>
                  查看全部 ({allCards.length})
                </span>
              </button>
            )}
          </div>
          </>
        )}
      </div>

      {/* Recent sessions */}
      {recentSessions.length > 0 && (
        <div className="home-recent-section">
          <h2 className="home-section-title">最近对话</h2>
          <div className="home-recent-list">
            {recentSessions.map((s) => (
              <button
                key={s.id}
                type="button"
                className="home-recent-item"
                onClick={() => handleResume(s.id)}
                disabled={resumingId === s.id}
              >
                <Avatar name={s.character_name || '?'} size={36} src={cardAvatars[s.card_id]} />
                <div className="home-recent-body">
                  <span className="home-recent-name">{s.character_name}</span>
                  <span className="home-recent-preview">{previewText(s.last_message)}</span>
                </div>
                <span className="home-recent-time">{fmtTime(s.last_message_at || s.updated_at)}</span>
                {resumingId === s.id && <span className="home-recent-loading">加载中…</span>}
              </button>
            ))}
          </div>
        </div>
      )}

      {pendingCard && (
        <RoleSetupModal
          isOpen={!!pendingCard}
          characterName={pendingCard.name || pendingCard.character_name}
          relationships={pendingCard.relationships || []}
          onConfirm={async (role) => {
            const card = pendingCard
            setPendingCard(null)
            await startChat(card)
          }}
          onSkip={() => setPendingCard(null)}
        />
      )}
    </div>
  )
}
