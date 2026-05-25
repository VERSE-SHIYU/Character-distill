import { useCallback, useEffect, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout, getAuthHeaders } from '../api/client'
import Avatar from './common/Avatar'
import Loading from './common/Loading'
import ErrorBox from './common/ErrorBox'
import ConfirmModal from './common/ConfirmModal'

function fmtTime(iso) {
  if (!iso) return ''
  try {
    let s = iso
    if (!s.includes('T')) s = s.replace(' ', 'T')
    if (!s.endsWith('Z') && !s.includes('+')) s += 'Z'
    const date = new Date(s)
    if (isNaN(date.getTime())) return ''
    const now = new Date()
    const diff = Math.floor((now - date) / 1000)
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
    if (date.getFullYear() === now.getFullYear()) return date.toLocaleDateString('zh-CN', { month: 'long', day: 'numeric' })
    return date.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' })
  } catch {
    return ''
  }
}

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
  const [error, setError] = useState(null)
  const [deleteConfirmId, setDeleteConfirmId] = useState(null)
  const [activeTab, setActiveTab] = useState('detail')
  const [versions, setVersions] = useState([])
  const [versionsLoading, setVersionsLoading] = useState(false)
  const [forks, setForks] = useState([])
  const [forksLoading, setForksLoading] = useState(false)

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
      .catch((err) => {
        console.error('[MarketCardDetail] load failed:', err)
        useAppStore.getState().setCurrentMarketCardId(null)
        setView('market')
      })
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

  const loadVersions = useCallback(async () => {
    if (!cardId) return
    setVersionsLoading(true)
    try {
      const res = await fetchWithTimeout(`/api/market/${cardId}/versions`)
      const data = await res.json()
      setVersions(data.versions || [])
    } catch {} finally { setVersionsLoading(false) }
  }, [cardId])

  const loadForks = useCallback(async () => {
    if (!cardId) return
    setForksLoading(true)
    try {
      const res = await fetchWithTimeout(`/api/market/${cardId}/forks`)
      const data = await res.json()
      setForks(data.forks || [])
    } catch {} finally { setForksLoading(false) }
  }, [cardId])

  useEffect(() => {
    if (activeTab === 'versions') loadVersions()
    if (activeTab === 'forks') loadForks()
  }, [activeTab, loadVersions, loadForks])

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
    setDeleteConfirmId(null)
    try {
      await fetchWithTimeout(`/api/market/${cardId}`, {
        method: 'DELETE',
        headers: { ...getAuthHeaders() },
      })
      setView('market')
    } catch (err) {
      setError(err.message || '删除失败')
    }
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
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5m7-7-7 7 7 7"/></svg>
          返回
        </button>
        <h1 className="panel-title">角色详情</h1>
        {(authUser?.is_admin || card.user_id === authUser?.id) && (
          <button type="button" className="btn-ghost" style={{ marginLeft: 'auto' }} onClick={() => setDeleteConfirmId(card.id)} title="删除">
            🗑
          </button>
        )}
      </header>

      {error && <ErrorBox message={error} onDismiss={() => setError(null)} />}

      <div className="market-detail-scroll">
        {/* Hero: cover image + name */}
        <div className="market-detail-hero">
          {card.avatar_data
            ? <Avatar name={charName} src={card.avatar_data} size={96} />
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

        {/* Tabs: Detail | Version History | Forks */}
        <div className="market-detail-tabs">
          <button
            type="button"
            className={`market-detail-tab${activeTab === 'detail' ? ' active' : ''}`}
            onClick={() => setActiveTab('detail')}
          >
            {'\u{1F4AC}'} 评论 ({comments.length})
          </button>
          <button
            type="button"
            className={`market-detail-tab${activeTab === 'versions' ? ' active' : ''}`}
            onClick={() => setActiveTab('versions')}
          >
            {'\u{1F4CB}'} 版本历史
          </button>
          <button
            type="button"
            className={`market-detail-tab${activeTab === 'forks' ? ' active' : ''}`}
            onClick={() => setActiveTab('forks')}
          >
            {'\u{1F331}'} 衍生角色
          </button>
        </div>

        {activeTab === 'detail' && <div className="market-detail-comments">
          <h3 className="market-detail-section-title">{'\u{1F4AC}'} 评论 ({comments.length})</h3>
          {commentsLoading ? (
            <Loading text="加载评论…" />
          ) : comments.length === 0 ? (
            <p className="market-detail-empty">暂无评论，来写第一条吧</p>
          ) : (
            <div className="market-detail-comment-list">
              {comments.map((c) => (
                <div key={c.id} className="market-detail-comment-item">
                  <button
                    type="button"
                    className="market-detail-comment-avatar-btn"
                    onClick={() => { setAuthorUserId(c.user_id); setView('author') }}
                  >
                    <Avatar name={c.username} size={32} />
                  </button>
                  <div className="market-detail-comment-body">
                    <div className="market-detail-comment-head">
                      <button
                        type="button"
                        className="market-detail-comment-name"
                        onClick={() => { setAuthorUserId(c.user_id); setView('author') }}
                      >
                        {c.username}
                      </button>
                      <span className="market-detail-comment-time">{fmtTime(c.created_at)}</span>
                    </div>
                    <p className="market-detail-comment-text">{c.content}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      }

      {activeTab === 'versions' && (
        <div className="market-detail-versions">
          <h3 className="market-detail-section-title">{'\u{1F4CB}'} 版本历史</h3>
          {versionsLoading ? (
            <Loading text="加载版本历史…" />
          ) : versions.length === 0 ? (
            <p className="market-detail-empty">暂无版本记录</p>
          ) : (
            <div className="version-list">
              {versions.map((v) => (
                <div key={v.id} className="version-item">
                  <div className="version-num">v{v.version_num}</div>
                  <div className="version-info">
                    <p className="version-message">{v.publish_message || '无说明'}</p>
                    <span className="version-time">{fmtTime(v.created_at)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {activeTab === 'forks' && (
        <div className="market-detail-forks">
          <h3 className="market-detail-section-title">{'\u{1F331}'} 衍生角色 ({forks.length})</h3>
          {forksLoading ? (
            <Loading text="加载衍生角色…" />
          ) : forks.length === 0 ? (
            <p className="market-detail-empty">暂无衍生角色</p>
          ) : (
            <div className="fork-list">
              {forks.map((f) => {
                const forkData = typeof f.card_json === 'string' ? JSON.parse(f.card_json) : f.card_json || {}
                return (
                  <div key={f.id} className="fork-item">
                    <Avatar name={forkData.name || f.name || '?'} size={40} />
                    <div className="fork-info">
                      <span className="fork-name">{forkData.name || f.name || '?'}</span>
                      <span className="fork-author">by {f.author_name || '匿名'}</span>
                    </div>
                    <span className="fork-likes">{'\u{2764}\u{FE0F}'} {f.likes || 0}</span>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}
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

      <ConfirmModal
        isOpen={!!deleteConfirmId}
        title="删除角色"
        message="确定删除该角色？此操作将移入回收站。"
        confirmText="删除"
        onConfirm={handleDelete}
        onCancel={() => setDeleteConfirmId(null)}
        danger
      />

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
