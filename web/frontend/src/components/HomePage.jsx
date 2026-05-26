import { useCallback, useEffect, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout } from '../api/client'
import Avatar from './common/Avatar'
import { loadCardAvatar } from '../store/db'

function fmtTime(iso) {
  if (!iso) return ''
  try {
    let s = iso
    if (!s.includes('T')) s = s.replace(' ', 'T')
    if (!s.endsWith('Z') && !s.includes('+')) s += 'Z'
    const date = new Date(s)
    if (isNaN(date.getTime())) return ''
    const now = new Date()
    const diff = Math.floor((now - date) / 1000)
    if (diff < 60) return '刚刚'
    if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
    if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
    const target = new Date(date.getFullYear(), date.getMonth(), date.getDate())
    const dayDiff = Math.floor((today - target) / 86400000)
    if (dayDiff === 1) return `昨天 ${date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`
    if (dayDiff < 7) {
      const weekdays = ['日', '一', '二', '三', '四', '五', '六']
      return `星期${weekdays[date.getDay()]} ${date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`
    }
    if (date.getFullYear() === now.getFullYear()) return date.toLocaleDateString('zh-CN', { month: 'long', day: 'numeric' })
    return date.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' })
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
  const resumeSession = useAppStore((s) => s.resumeSession)
  const setView = useAppStore((s) => s.setView)
  const viewCard = useAppStore((s) => s.viewCard)
  const apiConfigured = useAppStore((s) => s.apiConfigured)
  const authUser = useAppStore((s) => s.authUser)

  const [allCards, setAllCards] = useState([])
  const [cardsLoading, setCardsLoading] = useState(true)
  const [recentSessions, setRecentSessions] = useState([])
  const [resumingId, setResumingId] = useState(null)
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
        const card = allCards.find((c) => (c.id || c.card_id) === id)
        if (card?.avatar_data) {
          setCardAvatar(id, card.avatar_data)
        } else {
          loadCardAvatar(id).then((dataUrl) => {
            if (dataUrl) setCardAvatar(id, dataUrl)
          })
        }
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
    const enriched = { ...card, ...cardData, text_id: card.text_id }
    viewCard(enriched)
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

      {/* Dashboard stats */}
      <div className="home-stats-bar">
        <div className="home-stats-item">
          <span className="home-stats-num">{cardCount}</span>
          <span className="home-stats-label">角色</span>
        </div>
        <div className="home-stats-divider" />
        <div className="home-stats-item">
          <span className="home-stats-num">{recentSessions.length > 0 ? recentSessions.length : '-'}</span>
          <span className="home-stats-label">对话</span>
        </div>
        <div className="home-stats-divider" />
        <div className="home-stats-item">
          <span className="home-stats-num">{textCount}</span>
          <span className="home-stats-label">文本</span>
        </div>
      </div>

      {/* Character card grid */}
      <div className="home-card-section">
        <div className="home-section-header">
          <h2 className="home-section-title">角色卡片</h2>
          {allCards.length > 4 && (
            <button type="button" className="home-view-all-btn" onClick={() => setView('text')}>
              查看全部 ({allCards.length})
            </button>
          )}
        </div>
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
                  <span className="home-char-arrow">{'\u{203A}'}</span>
                </button>
              )
            })}
          </div>
        )}
      </div>

      {/* Recent sessions */}
      {recentSessions.length > 0 && (
        <div className="home-recent-section">
          <h2 className="home-section-title">最近对话</h2>
          <div className="home-recent-container">
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
                    <div className="home-recent-head">
                      <span className="home-recent-name">{s.character_name}</span>
                      <span className="home-recent-time">{fmtTime(s.last_message_at || s.updated_at)}</span>
                    </div>
                    <span className="home-recent-preview">{previewText(s.last_message)}</span>
                  </div>
                  {resumingId === s.id && <span className="home-recent-loading">加载中…</span>}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
