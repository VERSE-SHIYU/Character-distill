import { useCallback, useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { getAuthHeaders, fetchWithTimeout } from '../api/client'
import Avatar from './common/Avatar'
import Loading from './common/Loading'
import ErrorBox from './common/ErrorBox'

const PAGE_SIZE = 20

export default function MarketPage() {
  const startChat = useAppStore((s) => s.startChat)
  const loadCards = useAppStore((s) => s.loadCards)
  const loadStandaloneCards = useAppStore((s) => s.loadStandaloneCards)
  const currentTextId = useAppStore((s) => s.currentTextId)
  const setAuthorUserId = useAppStore((s) => s.setAuthorUserId)
  const setView = useAppStore((s) => s.setView)
  const authUser = useAppStore((s) => s.authUser)

  const [cards, setCards] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [sort, setSort] = useState('new')
  const [query, setQuery] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [forkingId, setForkingId] = useState(null)
  const [forkCard, setForkCard] = useState(null)
  const [commentCardId, setCommentCardId] = useState(null)
  const [comments, setComments] = useState([])
  const [commentsLoading, setCommentsLoading] = useState(false)
  const [commentText, setCommentText] = useState('')
  const [commentSending, setCommentSending] = useState(false)
  const [hasMore, setHasMore] = useState(true)
  const sentinelRef = useRef(null)

  const fetchCards = useCallback(async (p, s, q, append = false) => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({ page: p, page_size: PAGE_SIZE, sort: s })
      const url = q
        ? `/api/market/search?q=${encodeURIComponent(q)}&${params}`
        : `/api/market/list?${params}`
      const res = await fetchWithTimeout(url)
      const data = await res.json()
      if (append) {
        setCards(prev => [...prev, ...(data.cards || [])])
      } else {
        setCards(data.cards || [])
      }
      setHasMore((data.cards || []).length >= PAGE_SIZE)
      setTotal(data.total || 0)
      setPage(data.page || 1)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchCards(1, sort, '')
  }, [sort, fetchCards])

  // Infinite scroll: monitor sentinel
  useEffect(() => {
    if (!sentinelRef.current || !hasMore || loading) return
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        setPage(p => p + 1)
      }
    }, { threshold: 0.1 })
    observer.observe(sentinelRef.current)
    return () => observer.disconnect()
  }, [hasMore, loading])

  // Fetch next page when page > 1
  useEffect(() => {
    if (page > 1) fetchCards(page, sort, query, true)
  }, [page]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleSearch = (e) => {
    e.preventDefault()
    const q = searchInput.trim()
    setQuery(q)
    setPage(1)
    fetchCards(1, sort, q)
  }

  const handleClearSearch = () => {
    setSearchInput('')
    setQuery('')
    setPage(1)
    fetchCards(1, sort, '')
  }

  const totalPages = Math.ceil(total / PAGE_SIZE)

  const handleDeletePost = async (postId) => {
    if (!confirm('确定删除该角色？')) return
    try {
      await fetchWithTimeout(`/api/market/posts/${postId}`, { method: 'DELETE' })
      setCards((prev) => prev.filter((c) => c.id !== postId))
    } catch (err) {
      console.error('[Market] Delete failed:', err)
    }
  }

  const handleLike = async (cardId) => {
    try {
      const res = await fetchWithTimeout(`/api/market/${cardId}/like`, { method: 'POST' })
      const data = await res.json()
      setCards((prev) =>
        prev.map((c) =>
          c.id === cardId ? { ...c, liked_by_me: data.liked, likes: data.likes } : c,
        ),
      )
    } catch (err) {
      console.error('[Market] Like failed:', err)
    }
  }

  const loadComments = async (cardId) => {
    setCommentsLoading(true)
    try {
      const res = await fetchWithTimeout(`/api/market/${cardId}/comments`)
      const data = await res.json()
      setComments(data.comments || [])
    } catch (err) {
      console.error('[Market] Load comments failed:', err)
    } finally {
      setCommentsLoading(false)
    }
  }

  const openComments = (cardId) => {
    setCommentCardId(cardId)
    setCommentText('')
    loadComments(cardId)
  }

  const handleSendComment = async () => {
    if (!commentText.trim() || !commentCardId) return
    setCommentSending(true)
    try {
      await fetchWithTimeout(`/api/market/${commentCardId}/comments`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ content: commentText.trim() }),
      })
      setCommentText('')
      await loadComments(commentCardId)
    } catch (err) {
      console.error('[Market] Send comment failed:', err)
    } finally {
      setCommentSending(false)
    }
  }

  const doFork = async (card, textId) => {
    setForkingId(card.id)
    try {
      const res = await fetchWithTimeout(`/api/market/${card.id}/fork`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text_id: textId }),
      })
      const data = await res.json()
      if (textId) {
        await loadCards(textId)
      } else {
        await loadStandaloneCards()
      }
      startChat(data.card)
    } catch (err) {
      console.error('[Market] Fork failed:', err)
    } finally {
      setForkingId(null)
    }
  }

  const handleUse = (card) => {
    if (currentTextId) {
      setForkCard(card)
    } else {
      doFork(card, '')
    }
  }

  return (
    <div className="panel">
      <header className="panel-header">
        <h1 className="panel-title">角色市场</h1>
        <p className="panel-desc">浏览其他用户分享的角色卡</p>
      </header>

      {/* Search + sort toolbar */}
      <div className="market-toolbar">
        <form className="market-search-form" onSubmit={handleSearch}>
          <input
            type="text"
            className="market-search-input"
            placeholder="搜索角色名…"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
          />
          <button type="submit" className="btn-primary market-search-btn">搜索</button>
          {query && (
            <button type="button" className="btn-ghost" onClick={handleClearSearch}>清除</button>
          )}
        </form>
        <div className="market-sort-tabs">
          <button
            type="button"
            className={`market-sort-btn${sort === 'new' ? ' active' : ''}`}
            onClick={() => { setSort('new'); setQuery('') }}
          >
            最新
          </button>
          <button
            type="button"
            className={`market-sort-btn${sort === 'hot' ? ' active' : ''}`}
            onClick={() => { setSort('hot'); setQuery('') }}
          >
            最热
          </button>
        </div>
      </div>

      {/* Error */}
      {error && <ErrorBox message={error} />}

      {/* Loading */}
      {loading && <Loading text="加载角色市场…" />}

      {/* Empty */}
      {!loading && !error && cards.length === 0 && (
        <div className="shell-placeholder">
          <div className="shell-placeholder-inner">
            <div className="shell-placeholder-icon">{'\u{1F50D}'}</div>
            <div className="shell-placeholder-title">
              {query ? '未找到匹配角色' : '角色市场还是空的'}
            </div>
            <div className="shell-placeholder-sub">
              {query ? '试试其他关键词' : '快去分享你的角色卡吧'}
            </div>
          </div>
        </div>
      )}

      {/* Card grid */}
      {!loading && cards.length > 0 && (
        <>
          <div className="market-grid-v2">
            {cards.map((c) => {
              const cardData = typeof c.card_json === 'string'
                ? JSON.parse(c.card_json)
                : c.card_json || {}
              const charName = cardData.name || c.name || '?'
              const identity = cardData.identity || ''
              return (
                <div key={c.id} className="market-card-v2" onClick={(e) => { e.stopPropagation(); useAppStore.getState().setCurrentMarketCardId(c.id); setView('marketCardDetail') }}>
                  <div className="market-card-v2-cover">
                    {c.avatar_data
                      ? <img src={c.avatar_data} alt={charName} className="market-card-v2-cover-img" />
                      : <Avatar name={charName} size={64} />
                    }
                  </div>
                  <div className="market-card-v2-info">
                    <div className="market-card-v2-name">{charName}</div>
                    {identity && <div className="market-card-v2-identity">{identity}</div>}
                    <div className="market-card-v2-bottom">
                      <span className="market-card-v2-author">{c.author_name || '匿名'}</span>
                      <span className="market-card-v2-stats">
                        <button
                          type="button"
                          className={`market-like-btn${c.liked_by_me ? ' liked' : ''}`}
                          onClick={(e) => { e.stopPropagation(); handleLike(c.id) }}
                        >
                          {c.liked_by_me ? '❤️' : '🤍'} {c.likes || 0}
                        </button>
                        <span>💬 {c.comment_count || 0}</span>
                      </span>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>

          {/* Infinite scroll sentinel */}
          {hasMore && <div ref={sentinelRef} style={{ height: 1 }} />}
          {loading && cards.length > 0 && <div className="market-loading-more">加载更多…</div>}
        </>
      )}

      {/* Comment drawer */}
      {commentCardId && (
        <div className="modal-overlay" onClick={() => setCommentCardId(null)}>
          <div className="modal-card" style={{ maxWidth: 480, maxHeight: '70vh', display: 'flex', flexDirection: 'column' }} onClick={(e) => e.stopPropagation()}>
            <div className="modal-title" style={{ flexShrink: 0 }}>
              评论
              <button type="button" className="btn-ghost fr" onClick={() => setCommentCardId(null)}>✕</button>
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

      {/* Fork type selection modal */}
      {forkCard && (
        <div className="modal-overlay" onClick={() => setForkCard(null)}>
          <div className="modal-card" style={{ maxWidth: 400 }} onClick={(e) => e.stopPropagation()}>
            <h3 className="modal-title">选择使用方式</h3>
            <div className="modal-body">
              <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 16, lineHeight: 1.6 }}>
                决定如何放置这个角色：
              </p>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <button className="btn-primary" onClick={() => {
                  const card = forkCard
                  setForkCard(null)
                  doFork(card, currentTextId)
                }}>
                  {'\u{1F4D6}'} 挂载到当前文本
                </button>
                <button className="btn-secondary" onClick={() => {
                  const card = forkCard
                  setForkCard(null)
                  doFork(card, '')
                }}>
                  {'\u{1F30D}'} 新建独立空间
                </button>
              </div>
              <button className="btn-ghost mt-12 w-full" onClick={() => setForkCard(null)}>
                取消
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
