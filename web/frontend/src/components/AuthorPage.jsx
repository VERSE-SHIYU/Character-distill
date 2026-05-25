import { useCallback, useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout, getAuthHeaders } from '../api/client'
import Avatar from './common/Avatar'
import { Eye, EyeOff, MessageSquare, Theater, Sparkles, Book } from './common/Icon'
import PostCard from './common/PostCard'
import Loading from './common/Loading'
import ErrorBox from './common/ErrorBox'
import ConfirmModal from './common/ConfirmModal'

export default function AuthorPage({ embedded = false }) {
  const setView = useAppStore((s) => s.setView)
  const setAuthorUserId = useAppStore((s) => s.setAuthorUserId)
  const setMessageTargetUserId = useAppStore((s) => s.setMessageTargetUserId)
  const setMessageTargetUsername = useAppStore((s) => s.setMessageTargetUsername)
  const setCurrentTextDetailId = useAppStore((s) => s.setCurrentTextDetailId)
  const authorUserId = useAppStore((s) => s.authorUserId)
  const authUser = useAppStore((s) => s.authUser)
  const userAvatar = useAppStore((s) => s.userAvatar)
  const startChat = useAppStore((s) => s.startChat)

  const [author, setAuthor] = useState(null)
  const [cards, setCards] = useState([])
  const [texts, setTexts] = useState([])
  const [isFollowing, setIsFollowing] = useState(false)
  const [followersCount, setFollowersCount] = useState(0)
  const [followingCount, setFollowingCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [showFollowers, setShowFollowers] = useState(false)
  const [showFollowing, setShowFollowing] = useState(false)
  const [followersList, setFollowersList] = useState([])
  const [followingList, setFollowingList] = useState([])
  const [statsVisible, setStatsVisible] = useState(true)

  // Posts
  const [posts, setPosts] = useState([])
  const [postsLoading, setPostsLoading] = useState(false)
  const [postContent, setPostContent] = useState('')
  const [postVisibility, setPostVisibility] = useState('public')
  const [posting, setPosting] = useState(false)
  const [deleteConfirmId, setDeleteConfirmId] = useState(null)

  // Image upload
  const [postImages, setPostImages] = useState([])
  const [linkedCardId, setLinkedCardId] = useState('')
  const [showCardPicker, setShowCardPicker] = useState(false)
  const fileInputRef = useRef(null)

  async function compressImage(file) {
    return new Promise((resolve) => {
      const reader = new FileReader()
      reader.onload = () => {
        const img = new Image()
        img.onload = () => {
          let w = img.width, h = img.height
          const maxDim = 1200
          if (w > maxDim || h > maxDim) {
            const ratio = Math.min(maxDim / w, maxDim / h)
            w = Math.round(w * ratio)
            h = Math.round(h * ratio)
          }
          const canvas = document.createElement('canvas')
          canvas.width = w
          canvas.height = h
          const ctx = canvas.getContext('2d')
          ctx.drawImage(img, 0, 0, w, h)
          const tryQuality = (q) => {
            const data = canvas.toDataURL('image/jpeg', q)
            if (data.length < 204800 || q <= 0.1) resolve(data)
            else tryQuality(q - 0.1)
          }
          tryQuality(0.8)
        }
        img.src = reader.result
      }
      reader.readAsDataURL(file)
    })
  }

  const handleFileChange = async (e) => {
    const files = Array.from(e.target.files || [])
    const remaining = 9 - postImages.length
    if (remaining <= 0) return
    const toProcess = files.slice(0, remaining)
    const compressed = await Promise.all(toProcess.map(compressImage))
    setPostImages(prev => [...prev, ...compressed])
    e.target.value = ''
  }

  const removeImage = (idx) => {
    setPostImages(prev => prev.filter((_, i) => i !== idx))
  }

  const isOwnProfile = authUser?.id === authorUserId

  const loadPosts = useCallback(async () => {
    if (!authorUserId) return
    setPostsLoading(true)
    try {
      const res = await fetchWithTimeout(`/api/market/author/${authorUserId}/posts`, {
        headers: { ...getAuthHeaders() },
      })
      const data = await res.json()
      setPosts(data.posts || [])
    } catch {
      // ignore
    } finally {
      setPostsLoading(false)
    }
  }, [authorUserId])

  useEffect(() => {
    if (!authorUserId || authorUserId.trim() === '') { if (!embedded) setView('market'); return }
    ;(async () => {
      setLoading(true)
      try {
        const res = await fetchWithTimeout(`/api/market/author/${authorUserId}`)
        if (!res.ok) throw new Error(res.status === 404 ? '用户不存在' : '加载失败')
        const data = await res.json()
        setAuthor(data.author)
        setCards(data.cards || [])
        setTexts(data.texts || [])
        setIsFollowing(data.is_following || false)
        setFollowersCount(data.followers_count || 0)
        setFollowingCount(data.following_count || 0)
        setStatsVisible(data.stats_visible !== false)
      } catch (err) {
        setError(err.message.includes('不存在') ? '该用户不存在或已注销' : err.message)
      } finally {
        setLoading(false)
      }
    })()
  }, [authorUserId])

  useEffect(() => {
    loadPosts()
  }, [loadPosts])

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

  const handlePostSubmit = async () => {
    if (!postContent.trim() || posting) return
    setPosting(true)
    try {
      await fetchWithTimeout('/api/market/author/posts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({
          content: postContent.trim(),
          visibility: postVisibility,
          images: JSON.stringify(postImages),
          card_id: linkedCardId,
        }),
      })
      setPostContent('')
      setPostImages([])
      setLinkedCardId('')
      await loadPosts()
    } catch (err) {
      setError(err.message || '发布失败，请重试')
    } finally {
      setPosting(false)
    }
  }

  const handleDeletePost = async () => {
    const id = deleteConfirmId
    setDeleteConfirmId(null)
    try {
      await fetchWithTimeout(`/api/market/posts/${id}`, {
        method: 'DELETE',
        headers: { ...getAuthHeaders() },
      })
      setPosts((prev) => prev.filter((p) => p.id !== id))
    } catch (err) {
      console.error('Delete post failed:', err)
    }
  }

  const handleLike = async (postId) => {
    try {
      const res = await fetchWithTimeout(`/api/market/post/${postId}/like`, { method: 'POST' })
      const data = await res.json()
      setPosts(prev =>
        prev.map(p =>
          p.id === postId ? { ...p, liked_by_me: data.liked, likes: data.likes } : p,
        ),
      )
    } catch {}
  }

  const getCharName = (card) => {
    const cd = typeof card.card_json === 'string' ? JSON.parse(card.card_json) : card.card_json || {}
    return cd.name || card.name || '?'
  }

  const getCharIdentity = (card) => {
    const cd = typeof card.card_json === 'string' ? JSON.parse(card.card_json) : card.card_json || {}
    return cd.identity || ''
  }

  const getCharBackground = (card) => {
    const cd = typeof card.card_json === 'string' ? JSON.parse(card.card_json) : card.card_json || {}
    return cd.background || ''
  }

  const toggleFollowers = useCallback(async () => {
    if (showFollowers) { setShowFollowers(false); return }
    try {
      const res = await fetchWithTimeout(`/api/market/author/${authorUserId}/followers`, {
        headers: { ...getAuthHeaders() },
      })
      const data = await res.json()
      setFollowersList(data.followers || data.users || [])
      setShowFollowers(true)
    } catch { /* ignore */ }
  }, [authorUserId, showFollowers])

  const toggleFollowing = useCallback(async () => {
    if (showFollowing) { setShowFollowing(false); return }
    try {
      const res = await fetchWithTimeout('/api/market/my/following', {
        headers: { ...getAuthHeaders() },
      })
      const data = await res.json()
      setFollowingList(data.following || data.users || [])
      setShowFollowing(true)
    } catch { /* ignore */ }
  }, [showFollowing])

  const scrollToChars = () => {
    const el = document.querySelector('.author-chars-widget') || document.querySelector('.author-cards-grid')?.closest('.author-section')
    el?.scrollIntoView({ behavior: 'smooth' })
  }

  const toggleStatsVisibility = async () => {
    const next = !statsVisible
    try {
      await fetchWithTimeout('/api/market/author/visibility', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ stats_visible: next }),
      })
      setStatsVisible(next)
    } catch { /* ignore */ }
  }

  /* ── Premium SVG: Morandi book + dove (灰紫 & 淡绿) ── */
  const EmptyIllustration = () => (
    <svg className="author-empty-illustration" viewBox="0 0 160 120" fill="none" xmlns="http://www.w3.org/2000/svg">
      {/* Book */}
      <path d="M44 40 L80 32 L116 40 L116 92 L80 84 L44 92 Z" fill="#C8E6C9" fillOpacity="0.35" stroke="#C8E6C9" strokeWidth="1" strokeOpacity="0.5" />
      <path d="M80 32 L80 84" stroke="#C8E6C9" strokeWidth="1.2" strokeOpacity="0.4" />
      <path d="M56 48 L80 42 L104 48" stroke="#C8E6C9" strokeWidth="0.8" strokeOpacity="0.3" strokeLinecap="round" />
      <path d="M56 56 L80 50 L104 56" stroke="#C8E6C9" strokeWidth="0.8" strokeOpacity="0.3" strokeLinecap="round" />
      <path d="M56 64 L80 58 L104 64" stroke="#C8E6C9" strokeWidth="0.8" strokeOpacity="0.3" strokeLinecap="round" />
      <path d="M56 72 L80 66 L104 72" stroke="#C8E6C9" strokeWidth="0.8" strokeOpacity="0.3" strokeLinecap="round" />
      {/* Dove */}
      <g transform="translate(86, 20) scale(0.85)">
        <path d="M28 52 C28 52 18 38 22 28 C26 18 36 14 42 18 C48 22 50 34 42 42C38 46 34 48 28 52Z" fill="#D1C4E9" fillOpacity="0.25" stroke="#D1C4E9" strokeWidth="1" strokeOpacity="0.4" />
        <path d="M22 28 L14 22 L20 26" stroke="#D1C4E9" strokeWidth="0.8" strokeOpacity="0.3" fill="none" strokeLinecap="round" />
        <path d="M42 18 L52 8 L46 16" stroke="#D1C4E9" strokeWidth="0.8" strokeOpacity="0.3" fill="none" strokeLinecap="round" />
        <circle cx="38" cy="21" r="1.5" fill="#D1C4E9" fillOpacity="0.3" />
      </g>
      {/* Decorative circles */}
      <circle cx="28" cy="82" r="4" fill="#D1C4E9" fillOpacity="0.12" />
      <circle cx="138" cy="38" r="5" fill="#C8E6C9" fillOpacity="0.15" />
      <circle cx="126" cy="96" r="3" fill="#D1C4E9" fillOpacity="0.1" />
    </svg>
  )

  return (
    <div className="panel author-page">
      <header className="panel-header">
        {!embedded && (
          <button type="button" className="chat-back-btn" onClick={() => setView('market')} title="返回">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5m7-7-7 7 7 7"/></svg>
          返回
        </button>
        )}
        <h1 className="panel-title">{embedded || isOwnProfile ? '我的主页' : '作者主页'}</h1>
      </header>

      {error && <ErrorBox message={error} onDismiss={() => setError(null)} />}

      {loading ? (
        <Loading text="加载作者信息…" />
      ) : author ? (
        <>
          {isOwnProfile ? (
            /* ════════════════════════════════════════════
               PREMIUM LAYOUT — own profile
               ════════════════════════════════════════════ */
            <div className="author-split-layout">
              {/* ──── LEFT: FEED COLUMN ──── */}
              <div className="author-feed-column">

                {/* Profile Header Card — vertical layout */}
                <div className="author-profile-card author-profile-card--vertical">
                  <Avatar name={author.username || '?'} src={userAvatar} size={88} />
                  <div className="author-profile-info">
                    <h2 className="author-profile-name">{author.username}</h2>
                    <div className="author-profile-stats">
                      <button type="button" className="stat-btn" onClick={toggleFollowers}><strong>{followersCount}</strong> 粉丝</button>
                      <span className="stat-dot">·</span>
                      <button type="button" className="stat-btn" onClick={toggleFollowing}><strong>{followingCount}</strong> 关注</button>
                      <span className="stat-dot">·</span>
                      <button type="button" className="stat-btn" onClick={scrollToChars}><strong>{cards.length}</strong> 角色</button>
                      <span className="stat-dot">·</span>
                      <button type="button" className="stat-btn" onClick={() => setView('text')}><strong>{texts.length}</strong> 书籍</button>
                      {isOwnProfile && (
                        <button type="button" className="stat-visibility-toggle" onClick={toggleStatsVisibility} title={statsVisible ? '对其他人隐藏统计数据' : '对其他人显示统计数据'}>
                          {statsVisible ? <Eye size={16} /> : <EyeOff size={16} />}
                        </button>
                      )}
                    </div>
                    {isOwnProfile && (
                      <button className="author-edit-profile-btn" onClick={() => setView('settings')}>
                        编辑资料
                      </button>
                    )}
                    {showFollowers && (
                      <div className="stat-follow-popup">
                        {followersList.length === 0 ? (
                          <div className="stat-follow-empty">暂无粉丝</div>
                        ) : (
                          followersList.map((f) => (
                            <div key={f.id || f.user_id} className="stat-follow-item">
                              <button type="button" className="stat-follow-user-btn" onClick={() => { setAuthorUserId(f.id || f.user_id); if (!embedded) setView('author') }}>
                                <Avatar name={f.username || '?'} src={f.avatar_data || null} size={28} />
                                <span>{f.username}</span>
                              </button>
                              {authUser?.id !== (f.id || f.user_id) && (
                                <button type="button" className="btn-sm btn-outline stat-follow-msg-btn" onClick={() => { setMessageTargetUserId(f.id || f.user_id); setMessageTargetUsername(f.username); setView('messages') }}>
                                  私信
                                </button>
                              )}
                            </div>
                          ))
                        )}
                      </div>
                    )}
                    {showFollowing && (
                      <div className="stat-follow-popup">
                        {followingList.length === 0 ? (
                          <div className="stat-follow-empty">暂无关注</div>
                        ) : (
                          followingList.map((f) => (
                            <div key={f.id || f.user_id} className="stat-follow-item">
                              <button type="button" className="stat-follow-user-btn" onClick={() => { setAuthorUserId(f.id || f.user_id); if (!embedded) setView('author') }}>
                                <Avatar name={f.username || '?'} src={f.avatar_data || null} size={28} />
                                <span>{f.username}</span>
                              </button>
                              {authUser?.id !== (f.id || f.user_id) && (
                                <button type="button" className="btn-sm btn-outline stat-follow-msg-btn" onClick={() => { setMessageTargetUserId(f.id || f.user_id); setMessageTargetUsername(f.username); setView('messages') }}>
                                  私信
                                </button>
                              )}
                            </div>
                          ))
                        )}
                      </div>
                    )}
                  </div>
                </div>

                {/* Status Composer */}
                <div className="author-status-card">
                  <textarea
                    className="author-status-input"
                    placeholder="写点什么…"
                    rows={3}
                    value={postContent}
                    onChange={(e) => setPostContent(e.target.value)}
                  />

                  {/* Image preview */}
                  {postImages.length > 0 && (
                    <div className="author-post-img-preview">
                      {postImages.map((src, i) => (
                        <div key={i} className="author-post-img-thumb">
                          <img src={src} alt="" />
                          <button type="button" className="author-post-img-del" onClick={() => removeImage(i)}>✕</button>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Linked card tag */}
                  {linkedCardId && (() => {
                    const linked = cards.find(c => c.id === linkedCardId)
                    const linkedName = linked ? getCharName(linked) : ''
                    return (
                      <div className="author-post-linked-card">
                        {'\u{1F916}'} {linkedName}
                        <button type="button" className="author-post-img-del" onClick={() => setLinkedCardId('')}>✕</button>
                      </div>
                    )
                  })()}

                  {/* Toolbar */}
                  <div className="author-status-toolbar">
                    <div className="author-status-toolbar-left">
                      <input
                        ref={fileInputRef}
                        type="file"
                        accept="image/*"
                        multiple
                        style={{ display: 'none' }}
                        onChange={handleFileChange}
                      />
                      <button
                        type="button"
                        className="author-status-toolbar-btn"
                        onClick={() => fileInputRef.current?.click()}
                        disabled={postImages.length >= 9}
                        title="添加图片"
                      >
                        {'\u{1F5BC}'}
                      </button>
                      <button
                        type="button"
                        className="author-status-toolbar-btn"
                        onClick={() => setShowCardPicker(true)}
                        title="关联角色"
                      >
                        {'\u{1F916}'}
                      </button>
                      <button
                        type="button"
                        className="author-status-toolbar-btn author-status-visibility-btn"
                        onClick={() => setPostVisibility(postVisibility === 'public' ? 'private' : 'public')}
                        title={postVisibility === 'public' ? '公开' : '私密'}
                      >
                        {postVisibility === 'public' ? '\u{1F30D}' : '\u{1F512}'}
                      </button>
                    </div>
                    <button
                      type="button"
                      className="author-status-publish-btn"
                      disabled={!postContent.trim() || posting}
                      onClick={handlePostSubmit}
                    >
                      {posting ? '发布中…' : '发布'}
                    </button>
                  </div>
                </div>

                {/* Posts Feed */}
                <div className="author-feed-section">
                  {postsLoading ? (
                    <Loading text="加载动态…" />
                  ) : posts.length === 0 ? (
                    <div className="author-empty-state">
                      <EmptyIllustration />
                      <p className="author-empty-text">开始你的第一条动态吧</p>
                      <p className="author-empty-sub">分享你的想法、角色或创作</p>
                    </div>
                  ) : (
                    <div className="author-posts-list">
                      {posts.map((p) => (
                        <PostCard
                          key={p.id}
                          post={p}
                          onLike={handleLike}
                          onAuthorClick={(userId) => { setAuthorUserId(userId); setView('author') }}
                          onDelete={(id) => setDeleteConfirmId(id)}
                          showDelete={isOwnProfile}
                        />
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* ──── RIGHT: CHARACTERS WIDGET ──── */}
              <div className="author-chars-column">
                <div className="author-chars-widget">
                  <div className="author-chars-widget-title-row">
                    <h3 className="author-chars-widget-title"><Theater size={16} /> 公开角色 ({cards.length})</h3>
                    {cards.length > 0 && <span className="author-chars-widget-tag"><Sparkles size={14} /> 已公开</span>}
                  </div>
                  {cards.length === 0 ? (
                    <p className="author-chars-widget-empty">暂无公开角色</p>
                  ) : (
                    <div className="author-chars-widget-list">
                      {cards.slice(0, 15).map((card) => {
                        const name = getCharName(card)
                        const identity = getCharIdentity(card)
                        return (
                          <div key={card.id} className="author-widget-char-card">
                            <Avatar name={name} src={card.avatar_data || null} size={44} />
                            <div className="author-widget-char-info">
                              <div className="author-widget-char-name">{name}</div>
                              {identity && <div className="author-widget-char-identity">{identity}</div>}
                            </div>
                            <button
                              type="button"
                              className="author-widget-char-btn"
                              onClick={async (e) => {
                                e.stopPropagation()
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
                        )
                      })}
                    </div>
                  )}
                  {cards.length > 15 && (
                    <p className="author-chars-widget-more">还有 {cards.length - 15} 个角色…</p>
                  )}
                </div>
              </div>
            </div>
          ) : (
            /* ════════════════════════════════════════════
               EXISTING LAYOUT — other author profile
               ════════════════════════════════════════════ */
            <>
              {/* ── Section 1: Profile hero ── */}
              <div className="author-hero">
                <Avatar name={author.username || '?'} src={author.avatar_data} size={72} />
                <div className="author-hero-text">
                  <h2 className="author-name">{author.username}</h2>
                  <div className="author-stats">
                    {statsVisible ? (<>
                      <button type="button" className="stat-btn" onClick={toggleFollowers}><strong>{followersCount}</strong> 粉丝</button>
                      <button type="button" className="stat-btn" onClick={toggleFollowing}><strong>{followingCount}</strong> 关注</button>
                      <button type="button" className="stat-btn" onClick={scrollToChars}><strong>{cards.length}</strong> 角色</button>
                      <button type="button" className="stat-btn" onClick={() => setView('text')}><strong>{texts.length}</strong> 书籍</button>
                    </>) : (
                      <span className="author-stats-hidden">统计数据已隐藏</span>
                    )}
                  </div>
                  {showFollowers && (
                    <div className="stat-follow-popup">
                      {followersList.length === 0 ? (
                        <div className="stat-follow-empty">暂无粉丝</div>
                      ) : (
                        followersList.map((f) => (
                          <div key={f.id || f.user_id} className="stat-follow-item">
                            <button type="button" className="stat-follow-user-btn" onClick={() => { setAuthorUserId(f.id || f.user_id); setView('author') }}>
                              <Avatar name={f.username || '?'} src={f.avatar_data || null} size={28} />
                              <span>{f.username}</span>
                            </button>
                            {authUser?.id !== (f.id || f.user_id) && (
                              <button type="button" className="btn-sm btn-outline stat-follow-msg-btn" onClick={() => { setMessageTargetUserId(f.id || f.user_id); setMessageTargetUsername(f.username); setView('messages') }}>
                                私信
                              </button>
                            )}
                          </div>
                        ))
                      )}
                    </div>
                  )}
                  {showFollowing && (
                    <div className="stat-follow-popup">
                      {followingList.length === 0 ? (
                        <div className="stat-follow-empty">暂无关注</div>
                      ) : (
                        followingList.map((f) => (
                          <div key={f.id || f.user_id} className="stat-follow-item">
                            <button type="button" className="stat-follow-user-btn" onClick={() => { setAuthorUserId(f.id || f.user_id); setView('author') }}>
                              <Avatar name={f.username || '?'} src={f.avatar_data || null} size={28} />
                              <span>{f.username}</span>
                            </button>
                            {authUser?.id !== (f.id || f.user_id) && (
                              <button type="button" className="btn-sm btn-outline stat-follow-msg-btn" onClick={() => { setMessageTargetUserId(f.id || f.user_id); setMessageTargetUsername(f.username); setView('messages') }}>
                                私信
                              </button>
                            )}
                          </div>
                        ))
                      )}
                    </div>
                  )}
                </div>
                {!isOwnProfile && (
                  <>
                    <button
                      type="button"
                      className="btn-ghost"
                      style={{ marginLeft: 'auto' }}
                      onClick={() => { setMessageTargetUserId(authorUserId); setMessageTargetUsername(author.username); setView('messages') }}
                    >
                      发私信
                    </button>
                    <button
                      type="button"
                      className={`btn-primary${isFollowing ? ' btn-secondary' : ''}`}
                      onClick={handleFollow}
                    >
                      {isFollowing ? '已关注' : '关注'}
                    </button>
                  </>
                )}
              </div>

              {/* ── Section 2: Bookshelf ── */}
              <div className="author-section">
                <h3 className="author-section-title"><Book size={16} /> 书架 ({texts.length})</h3>
                {texts.length === 0 ? (
                  <p style={{ color: 'var(--text-dim)', fontSize: 13, textAlign: 'center', padding: 20 }}>
                    暂无公开书籍
                  </p>
                ) : (
                  texts.map((t) => (
                    <button key={t.id} className="author-book-card"
                      onClick={() => { setCurrentTextDetailId(t.id); setView('textDetail') }}>
                      <span style={{ fontSize: 28, lineHeight: 1 }}><Book size={28} /></span>
                      <div className="author-book-info">
                        <div className="author-book-title">{t.title || '未命名'}</div>
                        {t.description && <div className="author-book-desc">{t.description}</div>}
                      </div>
                      <div className="author-book-meta">
                        {t.char_count?.toLocaleString()} 字
                      </div>
                    </button>
                  ))
                )}
              </div>

              {/* ── Section 3: Posts ── */}
              <div className="author-section">
                <h3 className="author-section-title"><MessageSquare size={14} /> 动态</h3>

                {postsLoading ? (
                  <p style={{ fontSize: 13, color: 'var(--text-dim)' }}>加载中…</p>
                ) : posts.length === 0 ? (
                  <p style={{ textAlign: 'center', color: 'var(--text-dim)', padding: 20 }}>暂无动态</p>
                ) : (
                  <div className="author-posts-list">
                    {posts.map((p) => (
                      <PostCard
                        key={p.id}
                        post={p}
                        onLike={handleLike}
                        onAuthorClick={(userId) => { setAuthorUserId(userId); setView('author') }}
                        onDelete={(id) => setDeleteConfirmId(id)}
                        showDelete={false}
                      />
                    ))}
                  </div>
                )}
              </div>

              {/* ── Section 4: Public cards ── */}
              <div className="author-section">
                <h3 className="author-section-title"><Theater size={16} /> 公开角色 ({cards.length})</h3>
                {cards.length === 0 ? (
                  <p style={{ textAlign: 'center', color: 'var(--text-dim)', padding: 40 }}>暂无公开角色</p>
                ) : (
                  <div className="author-cards-grid">
                    {cards.map((card) => {
                      const name = getCharName(card)
                      const identity = getCharIdentity(card)
                      const background = getCharBackground(card)
                      return (
                        <div key={card.id} className="author-char-card">
                          <Avatar name={name} src={card.avatar_data || null} size={56} />
                          <h4 className="author-char-name">{name}</h4>
                          {identity && <p className="author-char-identity">{identity}</p>}
                          {background && (
                            <ExpandableText text={background} maxLines={3} />
                          )}
                          <div className="author-char-footer">
                            <span className="author-char-likes">{'\u{2764}'} {card.likes || 0}</span>
                            <button type="button" className="btn-primary btn-sm" onClick={async () => {
                              const res = await fetchWithTimeout(`/api/market/${card.id}/fork`, {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
                                body: JSON.stringify({ text_id: '' }),
                              })
                              const data = await res.json()
                              if (data.card) startChat(data.card)
                            }}>
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
          )}
        </>
      ) : (
        <p style={{ textAlign: 'center', color: 'var(--text-dim)', padding: 40 }}>用户不存在</p>
      )}

      {/* Card picker modal (shared) */}
      {showCardPicker && (
        <div className="modal-overlay" onClick={() => setShowCardPicker(false)}>
          <div className="modal-card" style={{ maxWidth: 360, maxHeight: '60vh', display: 'flex', flexDirection: 'column' }} onClick={(e) => e.stopPropagation()}>
            <div className="modal-title" style={{ flexShrink: 0 }}>
              选择关联角色
              <button type="button" className="btn-ghost fr" onClick={() => setShowCardPicker(false)}>✕</button>
            </div>
            <div style={{ flex: 1, overflowY: 'auto', padding: '0 16px' }}>
              {cards.length === 0 ? (
                <p style={{ textAlign: 'center', color: 'var(--text-dim)', padding: 20, fontSize: 13 }}>暂无公开角色</p>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {cards.map((c) => {
                    const name = getCharName(c)
                    const sel = linkedCardId === c.id
                    return (
                      <button
                        key={c.id}
                        type="button"
                        className={`author-card-picker-item${sel ? ' selected' : ''}`}
                        onClick={() => { setLinkedCardId(c.id); setShowCardPicker(false) }}
                      >
                        <Avatar name={name} size={28} />
                        <span>{name}</span>
                        {sel && <span style={{ marginLeft: 'auto', color: 'var(--accent)' }}>✓</span>}
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      <ConfirmModal
        isOpen={!!deleteConfirmId}
        title="删除动态"
        message="确定删除该动态？"
        confirmText="删除"
        onConfirm={handleDeletePost}
        onCancel={() => setDeleteConfirmId(null)}
        danger
      />
    </div>
  )
}

function ExpandableText({ text, maxLines = 3 }) {
  const [expanded, setExpanded] = useState(false)
  return (
    <div className="expandable-text-wrap">
      <p className={`expandable-text${expanded ? ' expanded' : ''}`} style={{ WebkitLineClamp: expanded ? 'unset' : maxLines }}>
        {text}
      </p>
      {text.length > 80 && (
        <button type="button" className="expandable-text-toggle" onClick={() => setExpanded(!expanded)}>
          {expanded ? '收起' : '展开'}
        </button>
      )}
    </div>
  )
}
