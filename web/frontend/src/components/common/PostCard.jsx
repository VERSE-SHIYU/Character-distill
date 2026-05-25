import { useState, useRef, useEffect } from 'react'
import { fetchWithTimeout, getAuthHeaders } from '../../api/client'
import Avatar from './Avatar'

/* ── Expandable text ── */
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
    <div className="post-card-content-wrap">
      <p
        ref={ref}
        className="post-card-content"
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
        <button type="button" className="post-card-expand-btn" onClick={() => setExpanded(true)}>
          展开全文
        </button>
      )}
    </div>
  )
}

/* ── Image grid ── */
function ImageGrid({ images, onImageClick }) {
  let imgs
  try { imgs = typeof images === 'string' ? JSON.parse(images) : images } catch { return null }
  if (!imgs || imgs.length === 0) return null

  const count = imgs.length
  let className = 'post-card-images'
  if (count === 1) className += ' post-card-images-1'
  else if (count === 2) className += ' post-card-images-2'
  else className += ' post-card-images-n'

  return (
    <div className={className}>
      {imgs.map((src, i) => (
        <img key={i} src={src} alt="" className="post-card-img" onClick={() => onImageClick?.(src)} loading="lazy" />
      ))}
    </div>
  )
}

/* ── PostCard ── */
export default function PostCard({ post, onLike, onAuthorClick, onDelete, showDelete = false, showAuthor = true }) {
  const [showComments, setShowComments] = useState(false)
  const [comments, setComments] = useState([])
  const [commentsLoading, setCommentsLoading] = useState(false)
  const [commentText, setCommentText] = useState('')
  const [commentSending, setCommentSending] = useState(false)
  const [previewImg, setPreviewImg] = useState(null)
  const [animating, setAnimating] = useState(false)

  /* Escape key dismisses image preview */
  useEffect(() => {
    if (!previewImg) return
    const handler = (e) => { if (e.key === 'Escape') setPreviewImg(null) }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [previewImg])

  const loadComments = async () => {
    setCommentsLoading(true)
    try {
      const res = await fetchWithTimeout(`/api/market/post/${post.id}/comments`)
      const data = await res.json()
      setComments(data.comments || [])
    } catch {} finally { setCommentsLoading(false) }
  }

  const handleSendComment = async () => {
    if (!commentText.trim()) return
    setCommentSending(true)
    try {
      await fetchWithTimeout(`/api/market/post/${post.id}/comments`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ content: commentText.trim() }),
      })
      setCommentText('')
      await loadComments()
    } catch {} finally { setCommentSending(false) }
  }

  const handleLikeClick = () => {
    setAnimating(true)
    onLike?.(post.id)
    setTimeout(() => setAnimating(false), 400)
  }

  const toggleComments = () => {
    const next = !showComments
    if (next && comments.length === 0) loadComments()
    setShowComments(next)
  }

  return (
    <div className="post-card">
      {/* Author header */}
      {showAuthor && (
        <div className="post-card-author">
          <button type="button" className="post-card-author-link" onClick={() => onAuthorClick?.(post.user_id)}>
            <Avatar name={post.author_name || '?'} size={36} />
            <span className="post-card-author-name">{post.author_name || '匿名'}</span>
          </button>
          {post.visibility === 'private' && <span className="post-card-private">{'\u{1F512}'} 私密</span>}
          <span className="post-card-time">{post.created_at?.slice(0, 16).replace('T', ' ')}</span>
        </div>
      )}

      {/* Text content */}
      <ExpandableText text={post.content} />

      {/* Image grid */}
      <ImageGrid images={post.images} onImageClick={setPreviewImg} />

      {/* Card reference */}
      {post.card_id && (
        <div className="post-card-ref">{'\u{1F916}'} 关联角色</div>
      )}

      {/* Action bar */}
      <div className="post-card-actions">
        <button
          type="button"
          className={'post-card-action-btn' + (post.liked_by_me ? ' liked' : '') + (animating ? ' animating' : '')}
          onClick={handleLikeClick}
        >
          {post.liked_by_me ? '❤️' : '\u{1F90D}'} {post.likes || 0}
        </button>
        <button
          type="button"
          className="post-card-action-btn"
          onClick={toggleComments}
        >
          {'\u{1F4AC}'} {post.comment_count || 0}
        </button>
        {showDelete && (
          <button type="button" className="post-card-delete" onClick={() => onDelete?.(post.id)}>
            删除
          </button>
        )}
      </div>

      {/* Comments section */}
      {showComments && (
        <div className="post-card-comments">
          {commentsLoading ? (
            <div className="post-card-comments-status">加载中…</div>
          ) : comments.length === 0 ? (
            <div className="post-card-comments-status">暂无评论</div>
          ) : (
            comments.map((c) => (
              <div key={c.id} className="post-card-comment">
                <Avatar name={c.username} size={24} />
                <div className="post-card-comment-body">
                  <span className="post-card-comment-user">{c.username}</span>
                  <p className="post-card-comment-text">{c.content}</p>
                </div>
              </div>
            ))
          )}
          <div className="post-card-comment-input-row">
            <input
              className="post-card-comment-input"
              placeholder="写下你的评论…"
              value={commentText}
              onChange={(e) => setCommentText(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSendComment()}
              disabled={commentSending}
            />
            <button
              className="post-card-comment-send"
              onClick={handleSendComment}
              disabled={!commentText.trim() || commentSending}
            >
              {commentSending ? '…' : '发送'}
            </button>
          </div>
        </div>
      )}

      {/* Image fullscreen preview */}
      {previewImg && (
        <div className="img-preview-overlay" onClick={() => setPreviewImg(null)}>
          <img src={previewImg} alt="" className="img-preview-full" />
          <button type="button" className="img-preview-close" onClick={(e) => { e.stopPropagation(); setPreviewImg(null) }}>{'✕'}</button>
        </div>
      )}
    </div>
  )
}
