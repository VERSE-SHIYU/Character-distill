import { useCallback, useEffect, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout, getAuthHeaders } from '../api/client'
import Avatar from './common/Avatar'
import { MessageSquare, Theater, Book } from './common/Icon'
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
  const [deleteConfirmId, setDeleteConfirmId] = useState(null)

  const isOwnProfile = authUser?.id === authorUserId

  // Own profile now handled by MinePage — redirect
  useEffect(() => {
    if (isOwnProfile && !embedded) {
      setView('mine')
    }
  }, [isOwnProfile, embedded, setView])

  if (isOwnProfile && !embedded) return null

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
      ) : (
        <p style={{ textAlign: 'center', color: 'var(--text-dim)', padding: 40 }}>用户不存在</p>
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
