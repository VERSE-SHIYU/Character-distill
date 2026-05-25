import { useCallback, useEffect, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout } from '../api/client'
import Avatar from './common/Avatar'
import Loading from './common/Loading'

export default function MarketCardDetail() {
  const setView = useAppStore((s) => s.setView)
  const cardId = useAppStore((s) => s.currentMarketCardId)
  const setAuthorUserId = useAppStore((s) => s.setAuthorUserId)
  const setMessageTargetUserId = useAppStore((s) => s.setMessageTargetUserId)
  const setMessageTargetUsername = useAppStore((s) => s.setMessageTargetUsername)
  const authUser = useAppStore((s) => s.authUser)
  const startChat = useAppStore((s) => s.startChat)
  const loadStandaloneCards = useAppStore((s) => s.loadStandaloneCards)
  const currentTextId = useAppStore((s) => s.currentTextId)

  const [card, setCard] = useState(null)
  const [loading, setLoading] = useState(true)
  const [comments, setComments] = useState([])
  const [commentsLoading, setCommentsLoading] = useState(false)
  const [commentText, setCommentText] = useState('')
  const [commentSending, setCommentSending] = useState(false)
  const [liked, setLiked] = useState(false)
  const [likes, setLikes] = useState(0)
  const [forking, setForking] = useState(false)
  const [showForkChoice, setShowForkChoice] = useState(false)

  useEffect(() => {
    if (!cardId) { setView('market'); return }
    setLoading(true)
    fetchWithTimeout(`/api/market/card/${cardId}`)
      .then((r) => r.json())
      .then((data) => {
        setCard(data)
        setLiked(data.liked_by_me || false)
        setLikes(data.likes || 0)
      })
      .catch(() => setView('market'))
      .finally(() => setLoading(false))
  }, [cardId, setView])

  const loadComments = useCallback(async () => {
    if (!cardId) return
    setCommentsLoading(true)
    try {
      const res = await fetchWithTimeout(`/api/market/${cardId}/comments`)
      const data = await res.json()
      setComments(data.comments || [])
    } catch {} finally { setCommentsLoading(false) }
  }, [cardId])

  useEffect(() => { loadComments() }, [loadComments])

  const handleLike = async () => {
    try {
      const res = await fetchWithTimeout(`/api/market/${cardId}/like`, { method: 'POST' })
      const data = await res.json()
      setLiked(data.liked)
      setLikes(data.likes)
    } catch {}
  }

  const handleComment = async () => {
    if (!commentText.trim() || commentSending) return
    setCommentSending(true)
    try {
      await fetchWithTimeout(`/api/market/${cardId}/comments`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: commentText.trim() }),
      })
      setCommentText('')
      await loadComments()
    } catch {} finally { setCommentSending(false) }
  }

  const doFork = async (textId) => {
    if (!card) return
    setShowForkChoice(false)
    setForking(true)
    try {
      const res = await fetchWithTimeout(`/api/market/${cardId}/fork`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text_id: textId }),
      })
      const data = await res.json()
      if (data.card) {
        if (textId) await useAppStore.getState().loadCards(textId)
        else await loadStandaloneCards()
        startChat(data.card)
      }
    } catch {} finally { setForking(false) }
  }

  const handleFork = () => {
    if (currentTextId) {
      setShowForkChoice(true)
    } else {
      doFork('')
    }
  }

  const handleDelete = async () => {
    if (!confirm('确定删除该角色？')) return
    await fetchWithTimeout(`/api/market/posts/${cardId}`, { method: 'DELETE' })
    setView('market')
  }

  if (loading) return <div className="panel"><Loading text="加载角色详情…" /></div>
  if (!card) return null

  const cardData = typeof card.card_json === 'string' ? JSON.parse(card.card_json) : card.card_json || {}
  const charName = cardData.name || card.name || '?'
  const identity = cardData.identity || ''
  const background = cardData.background || ''

  return (
    <div className="panel market-detail-page">
      <header className="panel-header">
        <button type="button" className="chat-back-btn" onClick={() => setView('market')} title="返回">
          {'\u{25C0}'}
        </button>
        <h1 className="panel-title">角色详情</h1>
        {(authUser?.is_admin || card.user_id === authUser?.id) && (
          <button type="button" className="btn-ghost" style={{ marginLeft: 'auto' }} onClick={handleDelete} title="删除">
            🗑
          </button>
        )}
      </header>

      <div className="market-detail-scroll">
        {/* Hero: cover image + name */}
        <div className="market-detail-hero">
          {card.avatar_data
            ? <img src={card.avatar_data} alt={charName} className="market-detail-cover-img" />
            : <Avatar name={charName} size={96} />
          }
          <h2 className="market-detail-name">{charName}</h2>
          {identity && <p className="market-detail-identity">{identity}</p>}
        </div>

        {/* Author bar: avatar + name + message */}
        <div className="market-detail-author-bar">
          <Avatar name={card.author_name || '匿名'} size={36} />
          <button
            type="button"
            className="market-detail-author-name"
            onClick={(e) => { e.stopPropagation(); setAuthorUserId(card.user_id); setView('author') }}
          >
            {card.author_name || '匿名'}
          </button>
          {card.user_id && card.user_id !== authUser?.id && (
            <div className="market-detail-author-actions">
              <button
                type="button"
                className="btn-sm btn-outline"
                onClick={(e) => {
                  e.stopPropagation()
                  setMessageTargetUserId(card.user_id)
                  setMessageTargetUsername(card.author_name || '匿名')
                  setView('messages')
                }}
              >
                私信
              </button>
            </div>
          )}
        </div>

        {/* Background / description */}
        {card.text_title && <span className="market-detail-tag">{'\u{1F4D6}'} {card.text_title}</span>}
        {background && <p className="market-detail-background">{background}</p>}

        {/* Stats + use button */}
        <div className="market-detail-stats">
          <button
            type="button"
            className={`market-detail-like-btn${liked ? ' liked' : ''}`}
            onClick={handleLike}
          >
            {liked ? '❤️' : '\u{1F90D}'} {likes}
          </button>
          <span className="market-detail-comment-count">{'\u{1F4AC}'} {comments.length}</span>
          <div className="market-detail-actions" style={{ marginLeft: 'auto' }}>
            <button type="button" className="btn-primary" onClick={handleFork} disabled={forking}>
              {forking ? '添加中…' : '使用角色'}
            </button>
          </div>
        </div>

        {/* Comments list */}
        <div className="market-detail-comments">
          <h3 className="market-detail-section-title">{'\u{1F4AC}'} 评论 ({comments.length})</h3>
          {commentsLoading ? (
            <Loading text="加载评论…" />
          ) : comments.length === 0 ? (
            <p className="market-detail-empty">暂无评论，来写第一条吧</p>
          ) : (
            <div className="market-detail-comment-list">
              {comments.map((c) => (
                <div key={c.id} className="market-detail-comment-item">
                  <Avatar name={c.username} size={32} />
                  <div className="market-detail-comment-body">
                    <div className="market-detail-comment-head">
                      <span className="market-detail-comment-name">{c.username}</span>
                      <span className="market-detail-comment-time">{c.created_at?.slice(0, 10)}</span>
                    </div>
                    <p className="market-detail-comment-text">{c.content}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Fixed bottom: comment input */}
      <div className="market-detail-fixed-input">
        <input
          type="text"
          className="market-detail-comment-field"
          placeholder="写下你的评论…"
          value={commentText}
          onChange={(e) => setCommentText(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleComment()}
          disabled={commentSending}
        />
        <button
          type="button"
          className="btn-primary btn-sm"
          onClick={handleComment}
          disabled={!commentText.trim() || commentSending}
        >
          {commentSending ? '…' : '发送'}
        </button>
      </div>

      {/* Fork choice modal */}
      {showForkChoice && (
        <div className="modal-overlay" onClick={() => setShowForkChoice(false)}>
          <div className="modal-card" style={{ maxWidth: 400 }} onClick={(e) => e.stopPropagation()}>
            <h3 className="modal-title">选择使用方式</h3>
            <div className="modal-body">
              <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 16, lineHeight: 1.6 }}>
                决定如何放置这个角色：
              </p>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <button className="btn-primary" onClick={() => doFork(currentTextId)}>
                  {'\u{1F4D6}'} 挂载到当前文本
                </button>
                <button className="btn-secondary" onClick={() => doFork('')}>
                  {'\u{1F30D}'} 新建独立空间
                </button>
              </div>
              <button className="btn-ghost mt-12 w-full" onClick={() => setShowForkChoice(false)}>
                取消
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
