import { useState, useEffect, useRef } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout, getAuthHeaders } from '../api/client'
import Avatar from './common/Avatar'
import Loading from './common/Loading'
import { SkeletonCard } from './common/Skeleton'
import PostCard from './common/PostCard'
import BannerCropModal from './common/BannerCropModal'
import ImageCropModal from './common/ImageCropModal'
import { Theater, Book, MessageSquare } from './common/Icon'
import { parseCardJson } from '../utils/card'
import { getCoverGradient } from './BookReader'

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
    const cardData = parseCardJson(card)
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
  const [bioEditing, setBioEditing] = useState(false)
  const [bioDraft, setBioDraft] = useState('')
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

  // Reading progress
  const [readingProgress, setReadingProgress] = useState({})

  const bannerInputRef = useRef(null)
  const [bannerCropFile, setBannerCropFile] = useState(null)
  const avatarInputRef = useRef(null)
  const [avatarCropFile, setAvatarCropFile] = useState(null)

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
    if (tab === 'posts') {
      setPostsLoading(true)
      fetchWithTimeout(`/api/market/author/${authUser?.id}/posts`)
        .then(r => r.json())
        .then(data => setPosts(data.posts || []))
        .catch(() => {})
        .finally(() => setPostsLoading(false))
    }
    if (tab === 'books') {
      fetchWithTimeout('/api/text/reading-progress/all')
        .then(r => r.json())
        .then(data => {
          const map = {}
          ;(data || []).forEach(p => { map[p.text_id] = p })
          setReadingProgress(map)
        })
        .catch(() => {})
    }
  }, [tab]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleRefresh = () => setRefreshKey(k => k + 1)

  const handleBannerSelect = (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setBannerCropFile(file)
    e.target.value = ''
  }

  const handleBannerCropConfirm = async (croppedDataUrl) => {
    setBannerCropFile(null)
    // Helper: re-encode at given quality
    const reEncode = (dataUrl, q) => new Promise((resolve) => {
      const img = new Image()
      img.onload = () => {
        const c = document.createElement('canvas')
        c.width = img.width
        c.height = img.height
        c.getContext('2d').drawImage(img, 0, 0)
        resolve(c.toDataURL('image/jpeg', q))
      }
      img.src = dataUrl
    })
    // Step down quality until under 300 KB
    let q = 0.85
    let result = croppedDataUrl
    while (result.length >= 300_000 && q > 0.1) {
      result = await reEncode(result, q)
      q -= 0.1
    }
    try { await uploadUserBanner(result) } catch (e) {
      console.error('[MinePage] Banner upload failed:', e)
    }
  }

  const handleBannerCropCancel = () => setBannerCropFile(null)

  const handleAvatarSelect = (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setAvatarCropFile(file)
    e.target.value = ''
  }

  const handleAvatarCropConfirm = async (croppedDataUrl) => {
    setAvatarCropFile(null)
    try {
      await fetchWithTimeout('/api/auth/avatar', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ avatar_data: croppedDataUrl }),
      })
      useAppStore.setState({ userAvatar: croppedDataUrl })
    } catch (e) {
      console.error('[MinePage] Avatar upload failed:', e)
    }
  }

  const handleAvatarCropCancel = () => setAvatarCropFile(null)

  const tabs = [
    { key: 'characters', label: '角色', icon: <Theater size={15} /> },
    { key: 'books', label: '书籍', icon: <Book size={15} /> },
    { key: 'posts', label: '动态', icon: <MessageSquare size={15} /> },
    { key: 'following', label: '关注', icon: <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg> },
    { key: 'messages', label: '私信', icon: <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg> },
  ]

  if (loading) return (
    <div className="mine-page-v2" style={{ padding: 20 }}>
      <div className="market-grid-v2">
        {[1, 2, 3, 4].map((i) => <SkeletonCard key={i} />)}
      </div>
    </div>
  )

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
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg> 更换封面
        </button>
        <input ref={bannerInputRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={handleBannerSelect} />
      </div>

      {/* ── Profile Section ── */}
      <div className="mine-profile-section">
        <div className="mine-profile-row">
          <div className="mine-profile-left">
            <div className="mine-avatar-wrap">
              <Avatar name={authUser?.username || '?'} src={userAvatar} size={60} onClick={() => avatarInputRef.current?.click()} />
              <div className="mine-avatar-overlay" onClick={() => avatarInputRef.current?.click()}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M23 19a2 2 0 01-2 2H3a2 2 0 01-2-2V8a2 2 0 012-2h4l2-3h6l2 3h4a2 2 0 012 2z"/><circle cx="12" cy="13" r="4"/></svg>
              </div>
              <input ref={avatarInputRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={handleAvatarSelect} />
            </div>
          </div>
          <div className="mine-profile-mid">
            <div className="mine-name-row">
              <h2 className="mine-profile-name">{authUser?.username}</h2>
              <button className="mine-edit-icon" onClick={() => setView('settings')} title="编辑资料">✏️</button>
            </div>
            {bioEditing ? (
              <input
                className="mine-bio-input"
                value={bioDraft}
                onChange={e => setBioDraft(e.target.value)}
                onBlur={async () => {
                  setBioEditing(false)
                  const bio = bioDraft.trim()
                  if (bio !== (authUser?.bio || '')) {
                    try {
                      await fetchWithTimeout('/api/auth/bio', {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ bio }),
                      })
                      useAppStore.setState({ authUser: { ...authUser, bio } })
                    } catch {}
                  }
                }}
                onKeyDown={e => {
                  if (e.key === 'Enter') e.target.blur()
                  if (e.key === 'Escape') { setBioEditing(false); setBioDraft(authUser?.bio || '') }
                }}
                autoFocus
                placeholder="写一句话介绍自己…"
                maxLength={200}
              />
            ) : (
              <div className="mine-bio-display" onClick={() => { setBioDraft(authUser?.bio || ''); setBioEditing(true) }}>
                {authUser?.bio ? authUser.bio : <span className="mine-bio-placeholder">点击添加个人简介</span>}
              </div>
            )}
          </div>
          <div className="mine-profile-stats">
            {cards.length}角色 · {texts.length}书籍 · {followersCount}粉丝 · {followingCount}关注
          </div>
        </div>
      </div>

      {/* ── Tab 栏 ── */}
      <div className="mine-tab-bar">
        {tabs.map(t => (
          <button
            key={t.key}
            type="button"
            className={`mine-tab${tab === t.key ? ' active' : ''}`}
            onClick={() => t.key === 'messages' ? setView('messages') : setTab(t.key)}
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
                const cardData = parseCardJson(card)
                const name = cardData.name || card.name || '?'
                const identity = cardData.identity || ''
                const isNew = card.created_at && (Date.now() - new Date(card.created_at).getTime()) < 3 * 24 * 60 * 60 * 1000
                const isPublic = card.visibility === 'public'
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
                          <span className="market-card-v2-fallback-letter">{name.charAt(0).toUpperCase()}</span>
                        </div>
                      )}
                      <div className="market-card-v2-badges">
                        {isNew && <span className="card-badge-new">新</span>}
                        {isPublic && <span className="card-badge-public" title="已公开到市场" />}
                      </div>
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
              <h3 className="mine-onboard-title">还没有上传文本</h3>
              <p className="mine-onboard-desc">上传小说或聊天记录，AI 会自动识别并蒸馏角色</p>
              <button type="button" className="btn-primary" onClick={() => setView('text')}>
                去上传
              </button>
            </div>
          ) : (
            <div className="mine-books-grid">
              {texts.map(text => {
                const progress = readingProgress[text.id]
                const pct = progress ? Math.round((progress.progress || 0) * 100) : 0
                const [g1, g2] = getCoverGradient(text.id)
                const title = text.title || text.filename || '未命名'
                return (
                  <div
                    key={text.id}
                    className="mine-book-cover-card"
                    onClick={() => {
                      useAppStore.getState().setReaderTextId(text.id)
                      setView('reader')
                    }}
                  >
                    <div
                      className="mine-book-cover-bg"
                      style={{ background: `linear-gradient(135deg, ${g1}, ${g2})` }}
                    >
                      <span className="mine-book-cover-title">{title}</span>
                    </div>
                    {pct > 0 && (
                      <div className="mine-book-cover-progress">
                        <div className="mine-book-cover-progress-bar" style={{ width: `${pct}%` }} />
                      </div>
                    )}
                    <span className="mine-book-cover-label">{pct > 0 ? `${pct}%` : '开始阅读'}</span>
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
                      await fetchWithTimeout('/api/market/author/posts', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
                        body: JSON.stringify({ content: postContent, visibility: 'public' }),
                      })
                      setPostContent('')
                      const res = await fetchWithTimeout(`/api/market/author/${authUser.id}/posts`)
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
                <p className="mine-onboard-desc">发一条让别人认识你</p>
                <button type="button" className="btn-primary" onClick={() => document.querySelector('.mine-composer-input')?.focus()}>
                  发动态
                </button>
              </div>
            ) : (
              <div className="mine-posts-list">
                {posts.map(p => (
                  <PostCard
                    key={p.id}
                    post={p}
                    onLike={async (id) => {
                      await fetchWithTimeout(`/api/market/post/${id}/like`, { method: 'POST', headers: getAuthHeaders() })
                      const res = await fetchWithTimeout(`/api/market/author/${authUser.id}/posts`)
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
                去市场看看
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

      <BannerCropModal
        file={bannerCropFile}
        onConfirm={handleBannerCropConfirm}
        onCancel={handleBannerCropCancel}
      />
      <ImageCropModal
        file={avatarCropFile}
        onConfirm={handleAvatarCropConfirm}
        onCancel={handleAvatarCropCancel}
      />
    </div>
  )
}
