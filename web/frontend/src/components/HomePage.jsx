import { useCallback, useEffect, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout } from '../api/client'
import Avatar from './common/Avatar'
import { loadCardAvatar } from '../store/db'
import { parseCardJson } from '../utils/card'
import { SkeletonCard } from './common/Skeleton'

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

export default function HomePage() {
  const texts = useAppStore((s) => s.texts)
  const loadTexts = useAppStore((s) => s.loadTexts)
  const resumeSession = useAppStore((s) => s.resumeSession)
  const setView = useAppStore((s) => s.setView)
  const viewCard = useAppStore((s) => s.viewCard)
  const apiConfigured = useAppStore((s) => s.apiConfigured)
  const authUser = useAppStore((s) => s.authUser)
  const setCurrentMarketCardId = useAppStore((s) => s.setCurrentMarketCardId)
  const cardAvatars = useAppStore((s) => s.cardAvatars)
  const setCardAvatar = useAppStore((s) => s.setCardAvatar)

  const [allCards, setAllCards] = useState([])
  const [cardsLoading, setCardsLoading] = useState(true)
  const [recentSessions, setRecentSessions] = useState([])
  const [resumingId, setResumingId] = useState(null)

  // ---- 标签 & 推荐角色 ----
  const [tags, setTags] = useState([])
  const [selectedTag, setSelectedTag] = useState('')
  const [discoverCards, setDiscoverCards] = useState([])
  const [discoverLoading, setDiscoverLoading] = useState(true)
  const [showTags, setShowTags] = useState(false)

  // ---- 置顶推荐 ----
  const [featuredCards, setFeaturedCards] = useState([])

  useEffect(() => {
    if (texts.length === 0) loadTexts()
  }, [])

  // 加载置顶推荐
  useEffect(() => {
    let cancelled = false
    fetchWithTimeout('/api/market/featured')
      .then((r) => r.json())
      .then((data) => { if (!cancelled) setFeaturedCards(Array.isArray(data) ? data : []) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [])

  // 加载标签列表
  useEffect(() => {
    fetchWithTimeout('/api/market/tags')
      .then((r) => r.json())
      .then((data) => setTags(data.tags || []))
      .catch(() => {})
  }, [])

  // 选中标签后加载推荐角色
  useEffect(() => {
    let cancelled = false
    setDiscoverLoading(true)
    const params = new URLSearchParams({ sort: 'hot', page_size: '8' })
    if (selectedTag) params.set('tag', selectedTag)
    fetchWithTimeout(`/api/market/list?${params}`)
      .then((r) => r.json())
      .then((data) => { if (!cancelled) setDiscoverCards(data.cards || []) })
      .catch(() => {})
      .finally(() => { if (!cancelled) setDiscoverLoading(false) })
    return () => { cancelled = true }
  }, [selectedTag])

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

  // 加载头像
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

  const handleDiscoverCardClick = (c) => {
    setCurrentMarketCardId(c.id)
    setView('marketCardDetail')
  }

  // 时段问候
  const hour = new Date().getHours()
  let greeting = '你好'
  if (hour < 5) greeting = '夜深了'
  else if (hour < 9) greeting = '早上好'
  else if (hour < 12) greeting = '上午好'
  else if (hour < 14) greeting = '中午好'
  else if (hour < 18) greeting = '下午好'
  else greeting = '晚上好'
  const username = authUser?.username || '用户'

  const cardCount = allCards.length
  const textCount = texts.length
  const isNewUser = allCards.length === 0 && recentSessions.length === 0 && !cardsLoading

  return (
    <div className="home-page panel">
      {/* API Key alert */}
      {!apiConfigured && authUser && (
        <div className="api-config-alert" style={{ marginBottom: 16, cursor: 'pointer' }} onClick={() => setView('settings')}>
          请先配置 API Key 才能开始对话
        </div>
      )}

      {isNewUser ? (
        /* ── 新用户引导视图 ── */
        <div className="home-onboard-container">
          <div className="home-onboard-header">
            <div className="home-onboard-title-lg">欢迎来到 CharSim</div>
            <div className="home-onboard-subtitle">三步开始，创造你的角色</div>
          </div>

          {/* 步骤 1 — 激活 */}
          <div className="home-onboard-card home-onboard-card-active">
            <div className="home-onboard-icon">
              <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
                <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
                <path d="M12 6v7" />
                <path d="M9 9l3 3 3-3" />
              </svg>
            </div>
            <div className="home-onboard-body">
              <div className="home-onboard-step-title">上传你的小说或聊天记录</div>
              <div className="home-onboard-step-desc">支持 txt 文件，AI 会自动识别角色</div>
            </div>
            <button
              type="button"
              className="home-onboard-btn"
              onClick={() => setView('text')}
            >
              去上传
            </button>
          </div>

          {/* 步骤 2 — 未激活 */}
          <div className="home-onboard-card">
            <div className="home-onboard-icon">
              <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--text-dim)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 3l1.5 5.5L19 10l-5.5 1.5L12 17l-1.5-5.5L5 10l5.5-1.5z" />
                <circle cx="18" cy="5" r="1" fill="var(--text-dim)" stroke="none" />
                <circle cx="6" cy="19" r="1.2" fill="var(--text-dim)" stroke="none" />
                <path d="M20 15l-1 3 3 1" />
              </svg>
            </div>
            <div className="home-onboard-body">
              <div className="home-onboard-step-title">AI 自动蒸馏角色卡</div>
              <div className="home-onboard-step-desc">从文本中提取性格、语气、口头禅</div>
            </div>
          </div>

          {/* 步骤 3 — 未激活 */}
          <div className="home-onboard-card">
            <div className="home-onboard-icon">
              <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--text-dim)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                <path d="M8 10h.01" strokeWidth="2.5" />
                <path d="M12 10h.01" strokeWidth="2.5" />
                <path d="M16 10h.01" strokeWidth="2.5" />
              </svg>
            </div>
            <div className="home-onboard-body">
              <div className="home-onboard-step-title">和角色沉浸式对话</div>
              <div className="home-onboard-step-desc">角色会记住之前的聊天内容</div>
            </div>
          </div>

          {/* 分割 */}
          <div className="home-onboard-divider">
            <span>—— 或者直接从市场挑一个角色 ——</span>
          </div>

          <button
            type="button"
            className="btn-primary"
            style={{ width: '100%' }}
            onClick={() => setView('market')}
          >
            浏览角色市场
          </button>
        </div>
      ) : (
        <>
          {/* A. 欢迎区 */}
          <div className="home-welcome">
            <div className="home-greeting">{greeting}，{username}</div>
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
          </div>

          {/* E. 最近对话 */}
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

          {/* A1. 编辑推荐 */}
          {featuredCards.length > 0 && (
            <div className="home-featured-section">
              <h2 className="home-section-title">编辑推荐</h2>
              <div className="home-featured-bar">
                {featuredCards.map((fc) => {
                  let fcData = {}
                  try { if (fc.card_json) fcData = JSON.parse(fc.card_json) } catch {}
                  return (
                    <button
                      key={fc.id}
                      type="button"
                      className="home-featured-card"
                      onClick={() => { setCurrentMarketCardId(fc.card_id); setView('marketCardDetail') }}
                    >
                      <div className="home-featured-cover">
                        {fc.avatar_data ? (
                          <>
                            <img className="home-featured-blur" src={fc.avatar_data} alt="" />
                            <img className="home-featured-img" src={fc.avatar_data} alt={fc.name || '角色'} />
                          </>
                        ) : (
                          <div className="home-featured-fallback">
                            <span className="home-featured-letter">{(fc.name || '?')[0]}</span>
                          </div>
                        )}
                      </div>
                      <div className="home-featured-info">
                        <div className="home-featured-name">{fc.name || '未知角色'}</div>
                        <div className="home-featured-identity">{fcData.identity || ''}</div>
                      </div>
                    </button>
                  )
                })}
              </div>
            </div>
          )}

          {/* C. 发现角色区 */}
          <div className="home-discover-section">
            <div className="home-section-header">
              <h2 className="home-section-title">发现角色</h2>
              <div className="home-section-header-actions">
                <button
                  type="button"
                  className="home-tags-toggle-btn"
                  onClick={() => setShowTags(prev => !prev)}
                >
                  🏷️筛选
                </button>
                <button type="button" className="home-view-all-btn" onClick={() => setView('market')}>
                  查看更多 &gt;
                </button>
              </div>
            </div>
            {showTags && (
              <div className="home-tags-bar-inline">
                <button
                  type="button"
                  className={`home-tags-pill${selectedTag === '' ? ' active' : ''}`}
                  onClick={() => setSelectedTag('')}
                >
                  全部
                </button>
                {tags.map((tag) => (
                  <button
                    key={tag}
                    type="button"
                    className={`home-tags-pill${selectedTag === tag ? ' active' : ''}`}
                    onClick={() => { setSelectedTag(tag); setShowTags(true) }}
                  >
                    {tag}
                  </button>
                ))}
              </div>
            )}
            {discoverLoading ? (
              <div className="home-discover-grid">
                {[1, 2, 3, 4].map((i) => (
                  <SkeletonCard key={i} />
                ))}
              </div>
            ) : discoverCards.length === 0 && selectedTag === '' ? (
              <div className="home-empty-hero">
                <div className="home-empty-icon">✨</div>
                <h3>还没有公开角色</h3>
                <p>上传小说或聊天记录，AI 蒸馏出角色，成为第一个创作者</p>
                <button className="btn-primary" onClick={() => setView('text')}>开始创作</button>
              </div>
            ) : discoverCards.length === 0 ? (
              <div className="home-no-chars">
                <p style={{ fontSize: 14, color: 'var(--text-dim)' }}>该分类暂无推荐角色</p>
              </div>
            ) : (
              <div className="home-discover-grid">
                {discoverCards.map((c, idx) => {
                  let cData = {}
                  try { if (c.card_json) cData = JSON.parse(c.card_json) } catch {}
                  return (
                    <button
                      key={c.id}
                      type="button"
                      className="market-card-v2 anim-item"
                      style={{ animationDelay: `${idx * 50}ms` }}
                      onClick={() => handleDiscoverCardClick(c)}
                    >
                      <div className="market-card-v2-cover">
                        {c.avatar_data ? (
                          <>
                            <img className="market-card-v2-cover-blur" src={c.avatar_data} alt="" />
                            <img className="market-card-v2-cover-img" src={c.avatar_data} alt={c.name || '角色'} />
                          </>
                        ) : (
                          <div className="market-card-v2-cover-fallback">
                            <span className="market-card-v2-fallback-letter">
                              {(c.name || '?')[0]}
                            </span>
                          </div>
                        )}
                      </div>
                      <div className="market-card-v2-glass-info">
                        <div className="market-card-v2-name">{c.name || '未知角色'}</div>
                        <div className="market-card-v2-identity">{cData.identity || ''}</div>
                        <div className="market-card-v2-bottom">
                          <div className="market-card-v2-stats">
                            <span>{'❤'} {c.likes ?? 0}</span>
                          </div>
                        </div>
                      </div>
                    </button>
                  )
                })}
                {discoverCards.length < 4 && Array.from({ length: 4 - discoverCards.length }).map((_, i) => (
                  <button key={`guide-${i}`} className="market-card-v2 home-guide-card" onClick={() => setView('text')}>
                    <div className="home-guide-card-inner">
                      <span className="home-guide-card-plus">+</span>
                      <span className="home-guide-card-text">创建角色</span>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* D. 管理员置顶（预留）— 后端暂无 featured API，空状态不渲染 */}

          {/* F. 我的角色 */}
          <div className="home-card-section">
            <div className="home-section-header">
              <h2 className="home-section-title">我的角色</h2>
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
                      <span className="home-char-arrow">{'›'}</span>
                    </button>
                  )
                })}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}
