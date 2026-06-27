import { useCallback, useEffect, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout, getAuthHeaders } from '../api/client'
import Avatar from './common/Avatar'
import Loading from './common/Loading'
import ErrorBox from './common/ErrorBox'
import ConfirmModal from './common/ConfirmModal'
import { Heart } from './common/Icon'
import { formatRelativeTime } from '../utils/time'
import { displayName } from '../utils/displayName'

export default function TextDetailPage() {
  const setView = useAppStore((s) => s.setView)
  const currentTextDetailId = useAppStore((s) => s.currentTextDetailId)
  const authUser = useAppStore((s) => s.authUser)

  const [textInfo, setTextInfo] = useState(null)
  const [textLoading, setTextLoading] = useState(true)
  const [error, setError] = useState(null)

  // Comments
  const [comments, setComments] = useState([])
  const [totalComments, setTotalComments] = useState(0)
  const [commentsLoading, setCommentsLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [hasMore, setHasMore] = useState(false)
  const PAGE_SIZE = 20

  // New comment input
  const [newComment, setNewComment] = useState('')
  const [submitting, setSubmitting] = useState(false)

  // Reply state — keyed by comment id
  const [replyTo, setReplyTo] = useState(null) // comment id being replied to
  const [replyContent, setReplyContent] = useState('')
  const [replySubmitting, setReplySubmitting] = useState(false)

  // Expanded replies — track which comments have all replies shown
  const [expandedReplies, setExpandedReplies] = useState({})

  // Delete confirm
  const [deleteConfirmId, setDeleteConfirmId] = useState(null)

  const textId = currentTextDetailId

  const loadTextInfo = useCallback(async () => {
    if (!textId) return
    setTextLoading(true)
    try {
      const res = await fetchWithTimeout(`/api/text/${textId}/detail`)
      const data = await res.json()
      setTextInfo(data.text)
    } catch (err) {
      setError(err.message)
    } finally {
      setTextLoading(false)
    }
  }, [textId])

  const loadComments = useCallback(async (pageNum = 1, append = false) => {
    if (!textId) return
    setCommentsLoading(true)
    try {
      const res = await fetchWithTimeout(`/api/text/${textId}/comments?page=${pageNum}&page_size=${PAGE_SIZE}`)
      const data = await res.json()
      if (append) {
        setComments((prev) => [...prev, ...(data.comments || [])])
      } else {
        setComments(data.comments || [])
      }
      setTotalComments(data.total || 0)
      setHasMore((data.comments || []).length === PAGE_SIZE)
      setPage(pageNum)
    } catch {
      // ignore
    } finally {
      setCommentsLoading(false)
    }
  }, [textId])

  useEffect(() => {
    if (!textId) { setView('text'); return }
    loadTextInfo()
    loadComments(1)
  }, [textId])

  const handleSubmitComment = async () => {
    if (!newComment.trim() || submitting) return
    setSubmitting(true)
    try {
      const res = await fetchWithTimeout(`/api/text/${textId}/comments`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ content: newComment.trim() }),
      })
      const data = await res.json()
      if (data.comment) {
        setComments((prev) => [data.comment, ...prev])
        setTotalComments((c) => c + 1)
        setNewComment('')
      }
    } catch (err) {
      console.error('Submit comment failed:', err)
    } finally {
      setSubmitting(false)
    }
  }

  const handleReply = async (parentId) => {
    if (!replyContent.trim() || replySubmitting) return
    setReplySubmitting(true)
    try {
      const res = await fetchWithTimeout(`/api/text/${textId}/comments`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ content: replyContent.trim(), parent_id: parentId }),
      })
      const data = await res.json()
      if (data.comment) {
        setComments((prev) =>
          prev.map((c) =>
            c.id === parentId
              ? { ...c, replies: [...(c.replies || []), data.comment] }
              : c,
          ),
        )
        setTotalComments((c) => c + 1)
        setReplyContent('')
        setReplyTo(null)
      }
    } catch (err) {
      console.error('Reply failed:', err)
    } finally {
      setReplySubmitting(false)
    }
  }

  const handleLike = async (commentId) => {
    try {
      const res = await fetchWithTimeout(`/api/text/comments/${commentId}/like`, {
        method: 'POST',
        headers: { ...getAuthHeaders() },
      })
      const data = await res.json()
      // Update both top-level and replies
      const update = (items) =>
        items.map((c) => {
          if (c.id === commentId) {
            return { ...c, liked_by_me: data.liked, likes: data.likes }
          }
          if (c.replies) {
            return { ...c, replies: update(c.replies) }
          }
          return c
        })
      setComments((prev) => update(prev))
    } catch (err) {
      console.error('Like failed:', err)
    }
  }

  const handleDelete = async () => {
    const id = deleteConfirmId
    setDeleteConfirmId(null)
    try {
      await fetchWithTimeout(`/api/text/comments/${id}`, {
        method: 'DELETE',
        headers: { ...getAuthHeaders() },
      })
      // Remove from state (could be top-level or reply)
      const removeNested = (items) =>
        items
          .filter((c) => c.id !== id)
          .map((c) => {
            if (c.replies) return { ...c, replies: removeNested(c.replies) }
            return c
          })
      setComments((prev) => removeNested(prev))
      setTotalComments((c) => Math.max(0, c - 1))
    } catch (err) {
      console.error('Delete comment failed:', err)
    }
  }

  const loadMore = () => {
    if (!commentsLoading && hasMore) {
      loadComments(page + 1, true)
    }
  }

  const toggleExpandReplies = (commentId) => {
    setExpandedReplies((prev) => ({ ...prev, [commentId]: !prev[commentId] }))
  }

  return (
    <div className="panel">
      <header className="panel-header">
        <button type="button" className="chat-back-btn" onClick={() => setView('text')} title="返回文本列表">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5m7-7-7 7 7 7"/></svg>
          返回
        </button>
        <h1 className="panel-title">书籍详情</h1>
      </header>

      {error && <ErrorBox message={error} onDismiss={() => setError(null)} />}

      {textLoading ? (
        <Loading text="加载书籍信息…" />
      ) : textInfo ? (
        <>
          {/* Book info */}
          <div className="market-card" style={{ marginBottom: 16, alignItems: 'flex-start' }}>
            <div className="market-card-body">
              <h2 style={{ fontSize: 18, fontWeight: 700, marginBottom: 4 }}>{textInfo.title || '未命名'}</h2>
              {textInfo.description && (
                <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 4 }}>{textInfo.description}</p>
              )}
              <div style={{ fontSize: 12, color: 'var(--text-dim)', display: 'flex', gap: 12 }}>
                <span>类型：{textInfo.text_type === 'chat' ? '聊天记录' : '小说/故事/剧本'}</span>
                <span>{totalComments} 条评论</span>
                {textInfo.created_at && <span>发布于 {fmtDate(textInfo.created_at)}</span>}
              </div>
            </div>
          </div>

          {/* Comment input */}
          <div className="modal-body" style={{ padding: 0, marginBottom: 16 }}>
            <textarea
              className="modal-textarea"
              placeholder="写下你的评论…"
              rows={3}
              value={newComment}
              onChange={(e) => setNewComment(e.target.value)}
              style={{ marginBottom: 8 }}
            />
            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <button
                type="button"
                className="btn-primary btn-sm"
                disabled={!newComment.trim() || submitting}
                onClick={handleSubmitComment}
              >
                {submitting ? '发送中…' : '发送'}
              </button>
            </div>
          </div>

          {/* Comments list */}
          <h3 style={{ fontSize: 15, fontWeight: 600, marginBottom: 12 }}>
            全部评论 ({totalComments})
          </h3>

          {comments.length === 0 && !commentsLoading ? (
            <p style={{ textAlign: 'center', color: 'var(--text-dim)', padding: 40, fontSize: 13 }}>暂无评论，来写第一条吧</p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {comments.map((comment) => (
                <CommentItem
                  key={comment.id}
                  comment={comment}
                  authUser={authUser}
                  replyTo={replyTo}
                  replyContent={replyContent}
                  replySubmitting={replySubmitting}
                  expandedReplies={expandedReplies}
                  onSetReplyTo={setReplyTo}
                  onReplyContentChange={setReplyContent}
                  onReply={handleReply}
                  onLike={handleLike}
                  onDelete={setDeleteConfirmId}
                  onToggleExpand={toggleExpandReplies}
                />
              ))}
            </div>
          )}

          {/* Load more */}
          {hasMore && (
            <div style={{ textAlign: 'center', marginTop: 16 }}>
              <button
                type="button"
                className="btn-ghost"
                style={{ fontSize: 13 }}
                onClick={loadMore}
                disabled={commentsLoading}
              >
                {commentsLoading ? '加载中…' : '加载更多'}
              </button>
            </div>
          )}
        </>
      ) : (
        <p style={{ textAlign: 'center', color: 'var(--text-dim)', padding: 40, fontSize: 13 }}>书籍不存在</p>
      )}

      <ConfirmModal
        isOpen={!!deleteConfirmId}
        title="删除评论"
        message="确定删除该评论？"
        confirmText="删除"
        onConfirm={handleDelete}
        onCancel={() => setDeleteConfirmId(null)}
        danger
      />
    </div>
  )
}

function CommentItem({
  comment,
  authUser,
  replyTo,
  replyContent,
  replySubmitting,
  expandedReplies,
  onSetReplyTo,
  onReplyContentChange,
  onReply,
  onLike,
  onDelete,
  onToggleExpand,
}) {
  const replies = comment.replies || []
  const isExpanded = expandedReplies[comment.id]
  const displayReplies = isExpanded ? replies : replies.slice(0, 2)
  const hiddenCount = replies.length - 2

  return (
    <div className="market-card" style={{ alignItems: 'flex-start', flexDirection: 'column' }}>
      <div style={{ display: 'flex', gap: 10, width: '100%' }}>
        <Avatar name={displayName(comment) || '?'} size={36} />
        <div className="market-card-body">
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
            <span style={{ fontWeight: 600, fontSize: 13 }}>{displayName(comment)}</span>
            <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>{formatRelativeTime(comment.created_at)}</span>
          </div>
          <p style={{ fontSize: 13, lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-word', marginBottom: 6 }}>
            {comment.content}
          </p>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 12 }}>
            <button
              type="button"
              className="btn-ghost"
              style={{ padding: '2px 6px', fontSize: 12, color: comment.liked_by_me ? '#e53e3e' : 'var(--text-dim)' }}
              onClick={() => onLike(comment.id)}
            >
              {comment.liked_by_me ? <Heart size={12} fill="currentColor" /> : <Heart size={12} />} {comment.likes || 0}
            </button>
            <button
              type="button"
              className="btn-ghost"
              style={{ padding: '2px 6px', fontSize: 12, color: 'var(--text-dim)' }}
              onClick={() => onSetReplyTo(replyTo === comment.id ? null : comment.id)}
            >
              回复
            </button>
            {authUser?.id === comment.user_id && (
              <button
                type="button"
                className="btn-ghost"
                style={{ padding: '2px 6px', fontSize: 12, color: 'var(--text-dim)' }}
                onClick={() => onDelete(comment.id)}
              >
                删除
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Reply input */}
      {replyTo === comment.id && (
        <div style={{ marginLeft: 46, marginTop: 8, width: '100%' }}>
          <textarea
            className="modal-textarea"
            placeholder={`回复 ${comment.username}…`}
            rows={2}
            value={replyContent}
            onChange={(e) => onReplyContentChange(e.target.value)}
            style={{ marginBottom: 6, fontSize: 13 }}
          />
          <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
            <button type="button" className="btn-ghost btn-sm" onClick={() => { onSetReplyTo(null); onReplyContentChange('') }}>
              取消
            </button>
            <button
              type="button"
              className="btn-primary btn-sm"
              disabled={!replyContent.trim() || replySubmitting}
              onClick={() => onReply(comment.id)}
            >
              {replySubmitting ? '…' : '回复'}
            </button>
          </div>
        </div>
      )}

      {/* Replies */}
      {replies.length > 0 && (
        <div style={{ marginLeft: 46, marginTop: 8, width: '100%' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {displayReplies.map((reply) => (
              <div key={reply.id} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                <Avatar name={displayName(reply) || '?'} size={28} />
                <div className="market-card-body">
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
                    <span style={{ fontWeight: 600, fontSize: 12 }}>{displayName(reply)}</span>
                    <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>{formatRelativeTime(reply.created_at)}</span>
                  </div>
                  <p style={{ fontSize: 13, lineHeight: 1.5, whiteSpace: 'pre-wrap', wordBreak: 'break-word', marginBottom: 4 }}>
                    {reply.content}
                  </p>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
                    <button
                      type="button"
                      className="btn-ghost"
                      style={{ padding: '1px 4px', fontSize: 11, color: reply.liked_by_me ? '#e53e3e' : 'var(--text-dim)' }}
                      onClick={() => onLike(reply.id)}
                    >
                      {reply.liked_by_me ? <Heart size={12} fill="currentColor" /> : <Heart size={12} />} {reply.likes || 0}
                    </button>
                    {authUser?.id === reply.user_id && (
                      <button
                        type="button"
                        className="btn-ghost"
                        style={{ padding: '1px 4px', fontSize: 11, color: 'var(--text-dim)' }}
                        onClick={() => onDelete(reply.id)}
                      >
                        删除
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
          {hiddenCount > 0 && !isExpanded && (
            <button
              type="button"
              className="btn-ghost"
              style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 4, padding: '4px 0' }}
              onClick={() => onToggleExpand(comment.id)}
            >
              展开更多回复 ({hiddenCount} 条)
            </button>
          )}
        </div>
      )}
    </div>
  )
}

function fmtDate(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return d.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
  } catch {
    return ''
  }
}
