import { useCallback, useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout, getAuthHeaders } from '../api/client'
import Avatar from './common/Avatar'
import Loading from './common/Loading'

const PAGE_SIZE = 20

function ExpandableText({ text, maxLines = 6 }) {
  const [expanded, setExpanded] = useState(false)
  const [clamped, setClamped] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (ref.current) {
      setClamped(ref.current.scrollHeight > ref.current.clientHeight)
    }
  }, [text])

  return (
    <div>
      <p
        ref={ref}
        className="feed-post-content"
        style={{
          WebkitLineClamp: expanded ? 'unset' : maxLines,
          display: '-webkit-box',
          WebkitBoxOrient: 'vertical',
          overflow: 'hidden',
        }}
      >
        {text}
      </p>
      {clamped && !expanded && (
        <button type="button" className="feed-expand-btn" onClick={() => setExpanded(true)}>
          展开全文
        </button>
      )}
    </div>
  )
}

function ImageGrid({ images }) {
  let imgs
  try { imgs = typeof images === 'string' ? JSON.parse(images) : images } catch { return null }
  if (!imgs || imgs.length === 0) return null

  const count = imgs.length

  if (count === 1) {
    return (
      <div className="feed-img-grid feed-img-grid-1">
        <img src={imgs[0]} alt="" className="feed-img" />
      </div>
    )
  }
  if (count === 2) {
    return (
      <div className="feed-img-grid feed-img-grid-2">
        {imgs.map((src, i) => <img key={i} src={src} alt="" className="feed-img" />)}
      </div>
    )
  }
  return (
    <div className="feed-img-grid feed-img-grid-n">
      {imgs.map((src, i) => <img key={i} src={src} alt="" className="feed-img" />)}
    </div>
  )
}

export default function FeedPage() {
  const setView = useAppStore((s) => s.setView)
  const setAuthorUserId = useAppStore((s) => s.setAuthorUserId)
  const authUser = useAppStore((s) => s.authUser)

  const [posts, setPosts] = useState([])
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [hasMore, setHasMore] = useState(true)
  const [error, setError] = useState(null)
  const sentinelRef = useRef(null)

  // Comment modal state
  const [commentPostId, setCommentPostId] = useState(null)
  const [comments, setComments] = useState([])
  const [commentsLoading, setCommentsLoading] = useState(false)
  const [commentText, setCommentText] = useState('')
  const [commentSending, setCommentSending] = useState(false)

  const fetchPosts = useCallback(async (p, append = false) => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({ page: p, page_size: PAGE_SIZE })
      const res = await fetchWithTimeout(`/api/market/feed?${params}`)
      const data = await res.json()
      const fetched = data.posts || []
      if (append) {
        setPosts(prev => [...prev, ...fetched])
      } else {
        setPosts(fetched)
      }
      setHasMore(fetched.length >= PAGE_SIZE)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchPosts(1)
  }, [fetchPosts])

  // Infinite scroll
  useEffect(() => {
    if (!sentinelRef.current || !hasMore || loading) return
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) setPage(p => p + 1)
    }, { threshold: 0.1 })
    observer.observe(sentinelRef.current)
    return () => observer.disconnect()
  }, [hasMore, loading])

  useEffect(() => {
    if (page > 1) fetchPosts(page, true)
  }, [page]) // eslint-disable-line react-hooks/exhaustive-deps

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

  const loadComments = async (postId) => {
    setCommentsLoading(true)
    try {
      const res = await fetchWithTimeout(`/api/market/post/${postId}/comments`)
      const data = await res.json()
      setComments(data.comments || [])
    } catch {} finally { setCommentsLoading(false) }
  }

  const openComments = (postId) => {
    setCommentPostId(postId)
    setCommentText('')
    loadComments(postId)
  }

  const handleSendComment = async () => {
    if (!commentText.trim() || !commentPostId) return
    setCommentSending(true)
    try {
      await fetchWithTimeout(`/api/market/post/${commentPostId}/comments`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ content: commentText.trim() }),
      })
      setCommentText('')
      await loadComments(commentPostId)
    } catch {} finally { setCommentSending(false) }
  }

  return (
    <div className="panel">
      <header className="panel-header">
        <h1 className="panel-title">动态</h1>
        <p className="panel-desc">关注的人的最新动态</p>
      </header>

      {error && <div className="error-box">{error}</div>}

      {loading && posts.length === 0 && <Loading text="加载动态…" />}

      {!loading && !error && posts.length === 0 && (
        <div className="shell-placeholder">
          <div className="shell-placeholder-inner">
            <div className="shell-placeholder-icon">{'\u{1F4AA}'}</div>
            <div className="shell-placeholder-title">还没有动态</div>
            <div className="shell-placeholder-sub">
              去关注一些创作者，他们的动态会显示在这里
            </div>
          </div>
        </div>
      )}

      {posts.length > 0 && (
        <div className="feed-list">
          {posts.map((p) => (
            <div key={p.id} className="feed-card">
              {/* Author row */}
              <div className="feed-post-author">
                <button
                  type="button"
                  className="feed-author-link"
                  onClick={() => { setAuthorUserId(p.user_id); setView('author') }}
                >
                  <Avatar name={p.author_name || '?'} size={36} />
                  <span className="feed-author-name">{p.author_name || '匿名'}</span>
                </button>
                <span className="feed-post-time">{p.created_at?.slice(0, 16).replace('T', ' ')}</span>
              </div>

              {/* Content */}
              <ExpandableText text={p.content} />

              {/* Images */}
              <ImageGrid images={p.images} />

              {/* Card reference */}
              {p.card_id && (
                <div className="feed-card-ref">
                  {'\u{1F916}'} 关联角色
                </div>
              )}

              {/* Actions */}
              <div className="feed-post-actions">
                <button
                  type="button"
                  className={`feed-action-btn${p.liked_by_me ? ' liked' : ''}`}
                  onClick={() => handleLike(p.id)}
                >
                  {p.liked_by_me ? '❤️' : '🤍'} {p.likes || 0}
                </button>
                <button
                  type="button"
                  className="feed-action-btn"
                  onClick={() => openComments(p.id)}
                >
                  {'\u{1F4AC}'} {p.comment_count || 0}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {hasMore && <div ref={sentinelRef} style={{ height: 1 }} />}
      {loading && posts.length > 0 && <div className="feed-loading-more">加载更多…</div>}

      {/* Comment modal */}
      {commentPostId && (
        <div className="modal-overlay" onClick={() => setCommentPostId(null)}>
          <div className="modal-card" style={{ maxWidth: 480, maxHeight: '70vh', display: 'flex', flexDirection: 'column' }} onClick={(e) => e.stopPropagation()}>
            <div className="modal-title" style={{ flexShrink: 0 }}>
              评论
              <button type="button" className="btn-ghost fr" onClick={() => setCommentPostId(null)}>✕</button>
            </div>
            <div style={{ flex: 1, overflowY: 'auto', padding: '0 20px', minHeight: 0 }}>
              {commentsLoading ? (
                <Loading text="加载评论…" />
              ) : comments.length === 0 ? (
                <p style={{ textAlign: 'center', color: 'var(--text-dim)', padding: 20, fontSize: 13 }}>暂无评论</p>
              ) : (
                comments.map((c) => (
                  <div key={c.id} style={{ padding: '10px 0', borderBottom: '1px solid var(--glass-border)' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                      <Avatar name={c.username} size={24} />
                      <span style={{ fontSize: 12, fontWeight: 600 }}>{c.username}</span>
                      <span style={{ fontSize: 11, color: 'var(--text-dim)', marginLeft: 'auto' }}>{c.created_at?.slice(0, 10)}</span>
                    </div>
                    <p style={{ fontSize: 13, margin: 0, lineHeight: 1.5 }}>{c.content}</p>
                  </div>
                ))
              )}
            </div>
            <div style={{ display: 'flex', gap: 8, padding: '12px 20px', borderTop: '1px solid var(--glass-border)' }}>
              <input
                className="modal-input"
                style={{ flex: 1 }}
                placeholder="写下你的评论…"
                value={commentText}
                onChange={(e) => setCommentText(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSendComment()}
                disabled={commentSending}
              />
              <button className="btn-primary" onClick={handleSendComment} disabled={!commentText.trim() || commentSending}>
                {commentSending ? '发送中…' : '发送'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
