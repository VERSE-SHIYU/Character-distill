import { useState, useRef, useEffect } from 'react'
import { fetchWithTimeout, getAuthHeaders } from '../../api/client'
import useAppStore from '../../store/useAppStore'
import Avatar from './Avatar'
import { Heart, MessageSquare, Trash2 } from './Icon'
import { parseCardJson } from '../../utils/card'

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

/* ── Time formatting ── */
function fmtTime(iso) {
  if (!iso) return ''
  try {
    // 后端返回 "2026-05-25 08:00:46" (UTC, 无时区标记)
    // 统一加 Z 后缀当 UTC 解析
    let s = iso
    if (!s.includes('T')) s = s.replace(' ', 'T')
    if (!s.endsWith('Z') && !s.includes('+')) s += 'Z'
    const date = new Date(s)
    if (isNaN(date.getTime())) return ''

    const now = new Date()
    const diff = Math.floor((now - date) / 1000) // 秒

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
    if (date.getFullYear() === now.getFullYear()) {
      return date.toLocaleDateString('zh-CN', { month: 'long', day: 'numeric' })
    }
    return date.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' })
  } catch {
    return ''
  }
}

/* ── PostCard ── */
export default function PostCard({ post, onLike, onAuthorClick, onDelete, showDelete = false, showAuthor = true }) {
  const setView = useAppStore((s) => s.setView)
  const setAuthorUserId = useAppStore((s) => s.setAuthorUserId)
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
            <Avatar name={post.author_name || '?'} src={post.author_avatar || null} size={36} />
            <span className="post-card-author-name">{post.author_name || '匿名'}</span>
          </button>
          {post.visibility === 'private' && <span className="post-card-private">{'\u{1F512}'} 私密</span>}
          <span className="post-card-time">{fmtTime(post.created_at)}</span>
        </div>
      )}

      {/* Text content */}
      <ExpandableText text={post.content} />

      {/* Image grid */}
      <ImageGrid images={post.images} onImageClick={setPreviewImg} />

      {/* Card reference */}
      {post.card_id && (() => {
        const cardData = post.card_json ? parseCardJson(post) : null
        const cardName = post.card_name || '关联角色'
        const cardIdentity = cardData?.identity || ''
        const parseDate = (iso) => iso ? new Date(iso.includes('T') && !iso.endsWith('Z') && !iso.includes('+') ? iso + 'Z' : iso) : null
        const cardUpdated = parseDate(post.card_updated_at)
        const postCreated = parseDate(post.created_at)
        const showModified = cardUpdated && postCreated && (cardUpdated - postCreated > 60000)
        return (
          <button type="button" className="post-card-ref" onClick={() => {
            useAppStore.getState().setCurrentMarketCardId(post.card_id)
            setView('marketCardDetail')
          }}>
            <Avatar name={cardName} src={post.card_avatar_data || null} size={24} />
            <span className="post-card-ref-name">{cardName}</span>
            {cardIdentity && <span className="post-card-ref-identity">{cardIdentity}</span>}
            {showModified && <span className="post-card-ref-modified">已修改 {cardUpdated.toLocaleString('zh-CN')}</span>}
          </button>
        )
      })()}

      {/* Location */}
      {post.location && <div className="post-card-location">{'\u{1F4CD}'} {post.location}</div>}

      {/* Action bar */}
      <div className="post-card-actions">
        <button
          type="button"
          className={'post-card-action-btn' + (post.liked_by_me ? ' liked' : '') + (animating ? ' animating' : '')}
          onClick={handleLikeClick}
        >
          {post.liked_by_me ? <Heart size={14} fill="currentColor" /> : <Heart size={14} />} {post.likes || 0}
        </button>
        <button
          type="button"
          className="post-card-action-btn"
          onClick={toggleComments}
        >
          <MessageSquare size={14} /> {post.comment_count || 0}
        </button>
        {showDelete && (
          <button type="button" className="post-card-delete" onClick={() => onDelete?.(post.id)}>
            <Trash2 size={13} /> 删除
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
                <button
                  type="button"
                  className="post-card-comment-avatar-btn"
                  onClick={() => { setAuthorUserId(c.user_id); setView('author') }}
                >
                  <Avatar name={c.username} src={c.avatar_data} size={28} />
                </button>
                <div className="post-card-comment-body">
                  <div className="post-card-comment-head">
                    <button
                      type="button"
                      className="post-card-comment-user"
                      onClick={() => { setAuthorUserId(c.user_id); setView('author') }}
                    >
                      {c.username}
                    </button>
                    {c.ip_location && <span className="post-card-comment-ip">IP属地: {c.ip_location}</span>}
                    <span className="post-card-comment-time">{c.created_at ? fmtTime(c.created_at) : ''}</span>
                  </div>
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
