import { useCallback, useEffect, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout, getAuthHeaders } from '../api/client'
import Avatar from './common/Avatar'
import Loading from './common/Loading'
import ErrorBox from './common/ErrorBox'
import ConfirmModal from './common/ConfirmModal'

export default function AuthorPage({ embedded = false }) {
  const setView = useAppStore((s) => s.setView)
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
        body: JSON.stringify({ content: postContent.trim(), visibility: postVisibility }),
      })
      setPostContent('')
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
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <button
                    type="button"
                    className={`btn-sm ${postVisibility === 'public' ? 'btn-primary' : 'btn-ghost'}`}
                    onClick={() => setPostVisibility(postVisibility === 'public' ? 'private' : 'public')}
                  >
                    {postVisibility === 'public' ? '\u{1F30D} 公开' : '\u{1F512} 私密'}
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
                {posts.map((post) => (
                  <div key={post.id} className="author-post-card">
                    <div className="author-post-header">
                      <Avatar name={author.username || '?'} size={40} />
                      <div className="author-post-header-text">
                        <span className="author-post-username">{author.username}</span>
                        {post.visibility === 'private' && (
                          <span className="author-post-private">{'\u{1F512}'} 私密</span>
                        )}
                      </div>
                      <span className="author-post-time">{fmtTime(post.created_at)}</span>
                    </div>
                    <div className="author-post-body">
                      {post.content}
                    </div>
                    <div className="author-post-footer">
                      <div className="author-post-stats" />
                      {isOwnProfile && (
                        <button type="button" className="author-post-delete" onClick={() => setDeleteConfirmId(post.id)}>
                          删除
                        </button>
                      )}
                    </div>
                  </div>
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
