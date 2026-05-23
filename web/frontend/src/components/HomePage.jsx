import { useCallback, useEffect, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout } from '../api/client'
import Avatar from './common/Avatar'

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
  if (!text) return '暂无消息'
  const one = text.replace(/\s+/g, ' ').trim()
  return one.length > max ? `${one.slice(0, max)}…` : one
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
  const [sessionsLoading, setSessionsLoading] = useState(true)
  const [error, setError] = useState(null)
  const [resumingId, setResumingId] = useState(null)

  // Load texts if not loaded
  useEffect(() => {
    if (texts.length === 0) {
      loadTexts()
    }
  }, [])

  // Load all cards from all texts
  const loadAllCards = useCallback(async () => {
    if (texts.length === 0) {
      setAllCards([])
      setCardsLoading(false)
      return
    }
    setCardsLoading(true)
    try {
      const results = await Promise.all(
        texts.map((t) =>
          fetchWithTimeout(`/api/distill/cards/by-text/${t.id}`)
            .then((r) => r.json())
            .catch(() => []),
        ),
      )
      // Merge and deduplicate by card id
      const merged = []
      const seen = new Set()
      for (const list of results) {
        for (const card of list) {
          if (!seen.has(card.id || card.card_id)) {
            seen.add(card.id || card.card_id)
            merged.push(card)
          }
        }
      }
      setAllCards(merged)
    } catch (err) {
      setError(err.message)
    } finally {
      setCardsLoading(false)
    }
  }, [texts])

  useEffect(() => {
    loadAllCards()
  }, [loadAllCards])

  // Load recent 3 sessions
  useEffect(() => {
    let cancelled = false
    setSessionsLoading(true)
    fetchWithTimeout('/api/history/list?page=1&page_size=3')
      .then((r) => r.json())
      .then((data) => {
        if (!cancelled) {
          setRecentSessions(data.items || [])
          setSessionsLoading(false)
        }
      })
      .catch(() => {
        if (!cancelled) setSessionsLoading(false)
      })
    return () => { cancelled = true }
  }, [])

  const handleCardClick = async (card) => {
    const textId = card.text_id
    const textTitle = texts.find((t) => t.id === textId)?.title || ''
    const cardData = parseCardJson(card)
    await startChat({ ...card, ...cardData, text_id: textId })
  }

  const handleResume = async (sessionId) => {
    setResumingId(sessionId)
    try {
      await resumeSession(sessionId)
    } catch {
      setResumingId(null)
    }
  }

  const cardCount = allCards.length
  const sessionCount = recentSessions.length > 0
    ? Math.max(recentSessions.length, recentSessions[0].total || 0)
    : 0
  const textCount = texts.length

  return (
    <div className="home-page panel" style={{ alignItems: 'stretch', justifyContent: 'flex-start', textAlign: 'left', overflowY: 'auto', padding: '24px 28px' }}>
      {/* API Key alert */}
      {!apiConfigured && authUser && (
        <div className="api-config-alert" style={{ marginBottom: 20, cursor: 'pointer' }} onClick={() => setView('settings')}>
          请先配置 API Key 才能开始对话
        </div>
      )}

      {/* Stats bar */}
      <div className="home-stats" style={{ flexDirection: 'row', justifyContent: 'space-around', padding: '18px 20px', marginBottom: 24 }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--primary)', lineHeight: 1.2 }}>{cardCount}</div>
          <div style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 2 }}>个角色</div>
        </div>
        <div style={{ width: 1, height: 36, background: 'var(--glass-border)', alignSelf: 'center' }} />
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--primary)', lineHeight: 1.2 }}>{sessionCount > 0 ? sessionCount : '-'}</div>
          <div style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 2 }}>次对话</div>
        </div>
        <div style={{ width: 1, height: 36, background: 'var(--glass-border)', alignSelf: 'center' }} />
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--primary)', lineHeight: 1.2 }}>{textCount}</div>
          <div style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 2 }}>份文本</div>
        </div>
      </div>

      {/* Character card grid */}
      <div style={{ flex: 1, minHeight: 0 }}>
        <h2 className="panel-title" style={{ fontSize: 16, marginBottom: 12 }}>角色卡片</h2>
        {cardsLoading ? (
          <div className="admin-loading">加载中…</div>
        ) : allCards.length === 0 ? (
          <div className="home-no-chars" style={{ padding: '48px 20px' }}>
            <p style={{ fontSize: 15, marginBottom: 16 }}>还没有角色，去蒸馏一个</p>
            <button type="button" className="btn-primary" onClick={() => setView('text')}>
              上传文本开始蒸馏
            </button>
          </div>
        ) : (
          <div className="home-char-grid">
            {allCards.map((card) => {
              const data = parseCardJson(card)
              const name = data.name || card.name || '?'
              const identity = data.identity || ''
              const traits = data.personality_traits || []
              return (
                <button
                  key={card.id || card.card_id}
                  type="button"
                  className="home-char-item"
                  onClick={() => handleCardClick(card)}
                >
                  <Avatar name={name} size={40} />
                  <div className="home-char-info">
                    <span className="home-char-name">{name}</span>
                    {identity && <span className="home-char-identity">{identity}</span>}
                    {traits.length > 0 && (
                      <div className="home-char-tags">
                        {traits.slice(0, 2).map((t, i) => (
                          <span key={i} className="home-char-tag">{t}</span>
                        ))}
                        {traits.length > 2 && (
                          <span className="home-char-tag">+{traits.length - 2}</span>
                        )}
                      </div>
                    )}
                  </div>
                </button>
              )
            })}
          </div>
        )}
      </div>

      {/* Recent sessions */}
      {recentSessions.length > 0 && (
        <div style={{ marginTop: 24, flexShrink: 0 }}>
          <h2 className="panel-title" style={{ fontSize: 16, marginBottom: 12 }}>最近对话</h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {recentSessions.map((s) => (
              <button
                key={s.id}
                type="button"
                className="home-char-item"
                style={{ alignItems: 'center' }}
                onClick={() => handleResume(s.id)}
                disabled={resumingId === s.id}
              >
                <Avatar name={s.character_name || '?'} size={40} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                    <span className="home-char-name">{s.character_name}</span>
                    <span style={{ fontSize: 11, color: 'var(--text-dim)', whiteSpace: 'nowrap' }}>
                      {fmtTime(s.last_message_at || s.updated_at)}
                    </span>
                  </div>
                  <span className="home-char-identity" style={{ marginTop: 0 }}>
                    {previewText(s.last_message)}
                  </span>
                </div>
                {resumingId === s.id && (
                  <span style={{ fontSize: 11, color: 'var(--primary)', marginLeft: 8 }}>加载中…</span>
                )}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
