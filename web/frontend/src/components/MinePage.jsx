import { useState, useEffect, useRef } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout, getAuthHeaders } from '../api/client'
import Avatar from './common/Avatar'
import Loading from './common/Loading'
import PostCard from './common/PostCard'
import { Theater, Book, MessageSquare } from './common/Icon'

/* ── MineCardMenu ── */
function MineCardMenu({ card, onRefresh }) {
  const [open, setOpen] = useState(false)

  const handleTogglePublic = async (e) => {
    e.stopPropagation()
    try {
      await fetchWithTimeout(`/api/market/${card.id}/visibility`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ visibility: card.visibility === 'public' ? 'private' : 'public' }),
      })
      onRefresh()
    } catch {}
    setOpen(false)
  }

  const handleDelete = async (e) => {
    e.stopPropagation()
    const cardData = typeof card.card_json === 'string' ? JSON.parse(card.card_json) : card.card_json || {}
    const name = cardData.name || card.name || '?'
    if (!confirm(`确定删除角色「${name}」？`)) return
    try {
      await fetchWithTimeout(`/api/distill/card/${card.id}`, {
        method: 'DELETE',
        headers: getAuthHeaders(),
      })
      onRefresh()
    } catch {}
    setOpen(false)
  }

  return (
    <div className="mine-card-menu-wrap">
      <button
        type="button"
        className="mine-card-menu-btn"
        onClick={(e) => { e.stopPropagation(); setOpen(!open) }}
      >⋯</button>
      {open && (
        <>
          <div className="mine-card-menu-backdrop" onClick={(e) => { e.stopPropagation(); setOpen(false) }} />
          <div className="mine-card-menu">
            <button type="button" onClick={handleTogglePublic}>
              {card.visibility === 'public' ? '取消公开' : '公开到市场'}
            </button>
            <button type="button" onClick={(e) => {
              e.stopPropagation()
              useAppStore.getState().setCurrentMarketCardId(card.id)
              useAppStore.getState().setView('marketCardDetail')
              setOpen(false)
            }}>编辑</button>
            <button type="button" className="mine-card-menu-danger" onClick={handleDelete}>删除</button>
          </div>
        </>
      )}
    </div>
  )
}

/* ── MinePage ── */
export default function MinePage() {
  const authUser = useAppStore((s) => s.authUser)
  const userAvatar = useAppStore((s) => s.userAvatar)
  const userBanner = useAppStore((s) => s.userBanner)
  const uploadUserBanner = useAppStore((s) => s.uploadUserBanner)
  const fetchUserBanner = useAppStore((s) => s.fetchUserBanner)
  const setView = useAppStore((s) => s.setView)
  const setAuthorUserId = useAppStore((s) => s.setAuthorUserId)
  const setMessageTargetUserId = useAppStore((s) => s.setMessageTargetUserId)
  const setMessageTargetUsername = useAppStore((s) => s.setMessageTargetUsername)

  const [tab, setTab] = useState('characters')
  const [loading, setLoading] = useState(true)
  const [cards, setCards] = useState([])
  const [texts, setTexts] = useState([])
  const [followersCount, setFollowersCount] = useState(0)
  const [followingCount, setFollowingCount] = useState(0)
  const [refreshKey, setRefreshKey] = useState(0)

  // Posts state
  const [posts, setPosts] = useState([])
  const [postsLoading, setPostsLoading] = useState(false)
  const [postContent, setPostContent] = useState('')
  const [posting, setPosting] = useState(false)

  // Following state
  const [following, setFollowing] = useState([])
  const [followingLoading, setFollowingLoading] = useState(false)

  const bannerInputRef = useRef(null)

  const loadData = () => {
    if (!authUser?.id) return
    setLoading(true)
    Promise.all([
      fetchWithTimeout(`/api/market/author/${authUser.id}`).then(r => r.json()),
      fetchUserBanner?.(),
    ]).then(([authorData]) => {
      setCards(authorData.cards || [])
      setTexts(authorData.texts || [])
      setFollowersCount(authorData.followers_count || 0)
      setFollowingCount(authorData.following_count || 0)
    }).catch(() => {})
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadData() }, [authUser?.id, refreshKey]) // eslint-disable-line react-hooks/exhaustive-deps

  // Tab data lazy loading
  useEffect(() => {
    if (tab === 'following') {
      setFollowingLoading(true)
      fetchWithTimeout('/api/market/my/following')
        .then(r => r.json())
        .then(data => setFollowing(data.users || []))
        .catch(() => {})
        .finally(() => setFollowingLoading(false))
    }
    if (tab === 'posts' && posts.length === 0) {
      setPostsLoading(true)
      fetchWithTimeout(`/api/market/posts/${authUser?.id}`)
        .then(r => r.json())
        .then(data => setPosts(data.posts || []))
        .catch(() => {})
        .finally(() => setPostsLoading(false))
    }
  }, [tab]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleRefresh = () => setRefreshKey(k => k + 1)

  const handleBannerUpload = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      const img = new Image()
      img.onload = async () => {
        const maxW = 1200
        const scale = Math.min(1, maxW / img.width)
        const canvas = document.createElement('canvas')
        canvas.width = img.width * scale
        canvas.height = img.height * scale
        canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height)
        // step down quality until under 300 KB
        const tryQuality = (q) => {
          const data = canvas.toDataURL('image/jpeg', q)
          if (data.length < 300_000 || q <= 0.1) return data
          return tryQuality(q - 0.1)
        }
        const compressed = tryQuality(0.8)
        try { await uploadUserBanner(compressed) } catch {}
      }
      img.src = reader.result
    }
    reader.readAsDataURL(file)
  }

  const tabs = [
    { key: 'characters', label: '角色', icon: <Theater size={15} /> },
    { key: 'books', label: '书籍', icon: <Book size={15} /> },
    { key: 'posts', label: '动态', icon: <MessageSquare size={15} /> },
    { key: 'following', label: '关注', icon: <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg> },
  ]

  if (loading) return <Loading text="加载中…" />

  return (
    <div className="mine-page-v2">
      {/* ── Banner ── */}
      <div className="mine-banner">
        {userBanner ? (
          <>
            <img src={userBanner} alt="" className="mine-banner-blur" aria-hidden="true" />
            <img src={userBanner} alt="" className="mine-banner-img" />
          </>
        ) : (
          <div className="mine-banner-fallback" />
        )}
        <div className="mine-banner-overlay" />
        <button type="button" className="mine-banner-upload" onClick={() => bannerInputRef.current?.click()} title="更换封面">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg> 更换封面
        </button>
        <input ref={bannerInputRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={handleBannerUpload} />
      </div>

      {/* ── Profile Header ── */}
      <div className="mine-profile-header">
        <Avatar name={authUser?.username || '?'} src={userAvatar} size={72} className="mine-avatar" />
        <div className="mine-profile-info">
          <h2 className="mine-profile-name">{authUser?.username}</h2>
          <button type="button" className="mine-edit-btn" onClick={() => setView('settings')}>编辑资料</button>
        </div>
      </div>

      {/* ── 统计卡片 ── */}
      <div className="mine-stats-row">
        <div className="mine-stat-card">
          <span className="mine-stat-number">{followersCount}</span>
          <span className="mine-stat-label">粉丝</span>
        </div>
        <div className="mine-stat-card" onClick={() => setTab('following')}>
          <span className="mine-stat-number">{followingCount}</span>
          <span className="mine-stat-label">关注</span>
        </div>
        <div className="mine-stat-card" onClick={() => setTab('characters')}>
          <span className="mine-stat-number">{cards.length}</span>
          <span className="mine-stat-label">角色</span>
        </div>
        <div className="mine-stat-card" onClick={() => setTab('books')}>
          <span className="mine-stat-number">{texts.length}</span>
          <span className="mine-stat-label">书籍</span>
        </div>
      </div>

      {/* ── Tab 栏 ── */}
      <div className="mine-tab-bar">
        {tabs.map(t => (
          <button
            key={t.key}
            type="button"
            className={`mine-tab${tab === t.key ? ' active' : ''}`}
            onClick={() => setTab(t.key)}
          >
            {t.icon} {t.label}
          </button>
        ))}
      </div>

      {/* ── Tab 内容 ── */}
      <div className="mine-tab-content">
        {/* 角色 tab */}
        {tab === 'characters' && (
          cards.length === 0 ? (
            <div className="mine-onboard-card">
              <h3 className="mine-onboard-title">创建你的第一个角色</h3>
              <div className="mine-onboard-steps">
                <div className="mine-onboard-step">
                  <span className="mine-onboard-step-num">1</span>
                  <span>上传小说或聊天记录</span>
                </div>
                <div className="mine-onboard-step">
                  <span className="mine-onboard-step-num">2</span>
                  <span>AI 自动蒸馏角色卡</span>
                </div>
                <div className="mine-onboard-step">
                  <span className="mine-onboard-step-num">3</span>
                  <span>公开分享到角色市场</span>
                </div>
              </div>
              <button type="button" className="btn-primary" onClick={() => setView('text')}>
                去上传文本
              </button>
            </div>
          ) : (
            <div className="market-grid-v2">
              {cards.map(card => {
                const cardData = typeof card.card_json === 'string' ? JSON.parse(card.card_json) : card.card_json || {}
                const name = cardData.name || card.name || '?'
                const identity = cardData.identity || ''
                return (
                  <div key={card.id} className="market-card-v2" onClick={() => {
                    useAppStore.getState().setCurrentMarketCardId(card.id)
                    setView('marketCardDetail')
                  }}>
                    <div className="market-card-v2-cover">
                      {card.avatar_data ? (
                        <>
                          <img src={card.avatar_data} alt="" className="market-card-v2-cover-blur" aria-hidden="true" />
                          <img src={card.avatar_data} alt={name} className="market-card-v2-cover-img" />
                        </>
                      ) : (
                        <div className="market-card-v2-cover-fallback">
                          <Avatar name={name} size={56} />
                        </div>
                      )}
                    </div>
                    <div className="market-card-v2-glass-info">
                      <div className="market-card-v2-name">{name}</div>
                      {identity && <div className="market-card-v2-identity">{identity}</div>}
                    </div>
                    <MineCardMenu card={card} onRefresh={handleRefresh} />
                  </div>
                )
              })}
            </div>
          )
        )}

        {/* 书籍 tab */}
        {tab === 'books' && (
          texts.length === 0 ? (
            <div className="mine-onboard-card">
              <h3 className="mine-onboard-title">还没有导入书籍</h3>
              <p className="mine-onboard-desc">上传小说或聊天记录，AI 会自动识别并蒸馏角色</p>
              <button type="button" className="btn-primary" onClick={() => setView('text')}>
                去上传文本
              </button>
            </div>
          ) : (
            <div className="mine-books-list">
              {texts.map(text => {
                const textCards = cards.filter(c => c.text_id === text.id)
                return (
                  <div key={text.id} className="mine-book-item">
                    <div className="mine-book-header" onClick={() => setView('text')}>
                      <span className="mine-book-icon">📖</span>
                      <div className="mine-book-info">
                        <span className="mine-book-title">{text.title || text.filename || '未命名'}</span>
                        <span className="mine-book-meta">
                          {textCards.length} 个角色 · {text.status === 'done' ? '已完成' : text.status === 'processing' ? '蒸馏中…' : '待蒸馏'}
                        </span>
                      </div>
                    </div>
                    {textCards.length > 0 && (
                      <div className="mine-book-cards">
                        {textCards.map(c => {
                          const cd = typeof c.card_json === 'string' ? JSON.parse(c.card_json) : c.card_json || {}
                          return (
                            <div key={c.id} className="mine-book-card-chip" onClick={() => {
                              useAppStore.getState().setCurrentMarketCardId(c.id)
                              setView('marketCardDetail')
                            }}>
                              <Avatar name={cd.name || '?'} src={c.avatar_data} size={24} />
                              <span>{cd.name || '?'}</span>
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )
        )}

        {/* 动态 tab */}
        {tab === 'posts' && (
          <>
            <div className="mine-composer">
              <textarea
                className="mine-composer-input"
                placeholder="写点什么…"
                rows={3}
                value={postContent}
                onChange={(e) => setPostContent(e.target.value)}
              />
              <div className="mine-composer-toolbar">
                <div />
                <button
                  type="button"
                  className="btn-primary btn-sm"
                  disabled={!postContent.trim() || posting}
                  onClick={async () => {
                    setPosting(true)
                    try {
                      await fetchWithTimeout('/api/market/posts', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
                        body: JSON.stringify({ content: postContent, visibility: 'public' }),
                      })
                      setPostContent('')
                      const res = await fetchWithTimeout(`/api/market/posts/${authUser.id}`)
                      const data = await res.json()
                      setPosts(data.posts || [])
                    } catch {}
                    finally { setPosting(false) }
                  }}
                >
                  {posting ? '发布中…' : '发布'}
                </button>
              </div>
            </div>

            {postsLoading ? (
              <Loading text="加载动态…" />
            ) : posts.length === 0 ? (
              <div className="mine-onboard-card">
                <h3 className="mine-onboard-title">还没有动态</h3>
                <p className="mine-onboard-desc">分享你的想法、角色或创作</p>
              </div>
            ) : (
              <div className="mine-posts-list">
                {posts.map(p => (
                  <PostCard
                    key={p.id}
                    post={p}
                    onLike={async (id) => {
                      await fetchWithTimeout(`/api/market/posts/${id}/like`, { method: 'POST', headers: getAuthHeaders() })
                      const res = await fetchWithTimeout(`/api/market/posts/${authUser.id}`)
                      const data = await res.json()
                      setPosts(data.posts || [])
                    }}
                    onAuthorClick={(userId) => { setAuthorUserId(userId); setView('author') }}
                    onDelete={async (id) => {
                      if (!confirm('确定删除这条动态？')) return
                      await fetchWithTimeout(`/api/market/posts/${id}`, { method: 'DELETE', headers: getAuthHeaders() })
                      setPosts(prev => prev.filter(p => p.id !== id))
                    }}
                    showDelete={true}
                  />
                ))}
              </div>
            )}
          </>
        )}

        {/* 关注 tab */}
        {tab === 'following' && (
          followingLoading ? (
            <Loading text="加载中…" />
          ) : following.length === 0 ? (
            <div className="mine-onboard-card">
              <h3 className="mine-onboard-title">还没有关注任何人</h3>
              <p className="mine-onboard-desc">去角色市场发现有趣的创作者</p>
              <button type="button" className="btn-primary" onClick={() => setView('market')}>
                浏览市场
              </button>
            </div>
          ) : (
            <div className="mine-following-list">
              {following.map(u => (
                <div key={u.id} className="mine-following-card">
                  <Avatar name={u.username || '?'} src={u.avatar_data} size={44} />
                  <div className="mine-following-info">
                    <button
                      type="button"
                      className="mine-following-name"
                      onClick={() => { setAuthorUserId(u.id); setView('author') }}
                    >
                      {u.username}
                    </button>
                    <span className="mine-following-meta">
                      {u.cards_count ?? 0} 个角色
                    </span>
                  </div>
                  <div className="mine-following-actions">
                    <button
                      type="button"
                      className="btn-sm btn-outline"
                      onClick={() => {
                        setMessageTargetUserId(u.id)
                        setMessageTargetUsername(u.username)
                        setView('messages')
                      }}
                    >
                      私信
                    </button>
                    <button
                      type="button"
                      className="btn-sm btn-secondary"
                      onClick={async () => {
                        await fetchWithTimeout(`/api/market/author/${u.id}/follow`, {
                          method: 'POST',
                          headers: getAuthHeaders(),
                        })
                        setFollowing(prev => prev.filter(f => f.id !== u.id))
                        setFollowingCount(prev => Math.max(0, prev - 1))
                      }}
                    >
                      取消关注
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )
        )}
      </div>
    </div>
  )
}
