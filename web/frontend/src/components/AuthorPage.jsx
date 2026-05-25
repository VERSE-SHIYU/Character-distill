import { useCallback, useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout, getAuthHeaders } from '../api/client'
import Avatar from './common/Avatar'
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
      const res = await fetchWithTimeout(`/api/market/author/${authorUserId}/posts`)
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
      console.error('Post failed:', err)
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

  return (
    <div className="panel author-page">
      <header className="panel-header">
        {!embedded && (
          <button type="button" className="chat-back-btn" onClick={() => setView('market')} title="返回">
            {'\u{25C0}'}
          </button>
        )}
        <h1 className="panel-title">{embedded ? '我的主页' : '作者主页'}</h1>
      </header>

      {error && <ErrorBox message={error} onDismiss={() => setError(null)} />}

      {loading ? (
        <Loading text="加载作者信息…" />
      ) : author ? (
        <>
          {/* ── Section 1: Profile hero ── */}
          <div className="author-hero">
            <Avatar name={author.username || '?'} src={isOwnProfile ? userAvatar : author.avatar_data} size={72} />
            <div className="author-hero-text">
              <h2 className="author-name">{author.username}</h2>
              <div className="author-stats">
                <span><strong>{followersCount}</strong> 粉丝</span>
                <span><strong>{followingCount}</strong> 关注</span>
                <span><strong>{cards.length}</strong> 角色</span>
                <span><strong>{texts.length}</strong> 书籍</span>
              </div>
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
            <h3 className="author-section-title">{'\u{1F4D6}'} 书架 ({texts.length})</h3>
            {texts.length === 0 ? (
              <p style={{ color: 'var(--text-dim)', fontSize: 13, textAlign: 'center', padding: 20 }}>
                {isOwnProfile ? '还没有公开的书籍，去文本管理中公开吧' : '暂无公开书籍'}
              </p>
            ) : (
              texts.map((t) => (
                <button key={t.id} className="author-book-card"
                  onClick={() => { setCurrentTextDetailId(t.id); setView('textDetail') }}>
                  <span style={{ fontSize: 28 }}>{'\u{1F4D6}'}</span>
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
            <h3 className="author-section-title">{'\u{1F4AC}'} 动态</h3>

            {isOwnProfile && (
              <div className="modal-body" style={{ marginBottom: 16, padding: 0 }}>
                <textarea
                  className="modal-textarea"
                  placeholder="写点什么…"
                  rows={3}
                  value={postContent}
                  onChange={(e) => setPostContent(e.target.value)}
                  style={{ marginBottom: 8 }}
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
                  const linkedName = linked
                    ? (JSON.parse(linked.card_json || '{}').name || linked.name)
                    : ''
                  return (
                    <div className="author-post-linked-card">
                      {'\u{1F916}'} {linkedName}
                      <button type="button" className="author-post-img-del" onClick={() => setLinkedCardId('')}>✕</button>
                    </div>
                  )
                })()}

                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <button
                    type="button"
                    className={`btn-sm ${postVisibility === 'public' ? 'btn-primary' : 'btn-ghost'}`}
                    onClick={() => setPostVisibility(postVisibility === 'public' ? 'private' : 'public')}
                  >
                    {postVisibility === 'public' ? '\u{1F30D} 公开' : '\u{1F512} 私密'}
                  </button>
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
                    className="btn-sm btn-ghost"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={postImages.length >= 9}
                  >
                    {'\u{1F5BC}'} 图片{postImages.length > 0 ? ` (${postImages.length}/9)` : ''}
                  </button>
                  <button
                    type="button"
                    className="btn-sm btn-ghost"
                    onClick={() => setShowCardPicker(true)}
                  >
                    {'\u{1F916}'} 关联角色
                  </button>
                  <button
                    type="button"
                    className="btn-primary btn-sm"
                    disabled={!postContent.trim() || posting}
                    onClick={handlePostSubmit}
                  >
                    {posting ? '发布中…' : '发布'}
                  </button>
                </div>
              </div>
            )}

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
                    showDelete={isOwnProfile}
                  />
                ))}
              </div>
            )}
          </div>

          {/* ── Section 4: Public cards ── */}
          <div className="author-section">
            <h3 className="author-section-title">{'\u{1F3AD}'} 公开角色 ({cards.length})</h3>
            {cards.length === 0 ? (
              <p style={{ textAlign: 'center', color: 'var(--text-dim)', padding: 40 }}>暂无公开角色</p>
            ) : (
              <div className="author-cards-grid">
                {cards.map((card) => {
                  const cardData = typeof card.card_json === 'string'
                    ? JSON.parse(card.card_json)
                    : card.card_json || {}
                  const name = cardData.name || card.name || '?'
                  const identity = cardData.identity || ''
                  const background = cardData.background || ''
                  return (
                    <div key={card.id} className="author-char-card">
                      <Avatar name={name} size={56} />
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

      {/* Card picker modal */}
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
                    const cd = typeof c.card_json === 'string' ? JSON.parse(c.card_json) : c.card_json || {}
                    const name = cd.name || c.name || '?'
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
