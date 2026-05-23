import { useCallback, useEffect, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { getAuthHeaders, fetchWithTimeout } from '../api/client'
import Avatar from './common/Avatar'
import Loading from './common/Loading'
import ErrorBox from './common/ErrorBox'

const PAGE_SIZE = 20

export default function MarketPage() {
  const startChat = useAppStore((s) => s.startChat)
  const loadCards = useAppStore((s) => s.loadCards)
  const currentTextId = useAppStore((s) => s.currentTextId)

  const [cards, setCards] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [sort, setSort] = useState('new')
  const [query, setQuery] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [forkingId, setForkingId] = useState(null)

  const fetchCards = useCallback(async (p, s, q) => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({ page: p, page_size: PAGE_SIZE, sort: s })
      const url = q
        ? `/api/market/search?q=${encodeURIComponent(q)}&${params}`
        : `/api/market/list?${params}`
      const res = await fetchWithTimeout(url)
      const data = await res.json()
      setCards(data.cards || [])
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

  const handleSearch = (e) => {
    e.preventDefault()
    setPage(1)
    fetchCards(1, sort, searchInput.trim())
  }

  const handleClearSearch = () => {
    setSearchInput('')
    setQuery('')
    setPage(1)
    fetchCards(1, sort, '')
  }

  const totalPages = Math.ceil(total / PAGE_SIZE)

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

  const handleUse = async (card) => {
    setForkingId(card.id)
    try {
      const res = await fetchWithTimeout(`/api/market/${card.id}/fork`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text_id: currentTextId || '' }),
      })
      const data = await res.json()
      // Refresh cards so the forked card appears in character management
      if (currentTextId) {
        await loadCards(currentTextId)
      }
      startChat(data.card)
    } catch (err) {
      console.error('[Market] Fork failed:', err)
    } finally {
      setForkingId(null)
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
          <div className="market-grid">
            {cards.map((c) => {
              const cardData = typeof c.card_json === 'string'
                ? JSON.parse(c.card_json)
                : c.card_json || {}
              const charName = cardData.name || c.name || '?'
              const identity = cardData.identity || ''
              return (
                <div key={c.id} className="market-card">
                  <Avatar name={charName} size={56} />
                  <div className="market-card-body">
                    <div className="market-card-name">{charName}</div>
                    {identity && <div className="market-card-identity">{identity}</div>}
                    <div className="market-card-meta">
                      <span className="market-card-author">{'\u{1F464}'} {c.author_name || '匿名'}</span>
                      <span className="market-card-likes">
                        <button
                          type="button"
                          className={`market-like-btn${c.liked_by_me ? ' liked' : ''}`}
                          onClick={() => handleLike(c.id)}
                          disabled={forkingId === c.id}
                        >
                          {c.liked_by_me ? '❤️' : '\u{1F90D}'}
                        </button>
                        {c.likes || 0}
                      </span>
                    </div>
                  </div>
                  <button
                    type="button"
                    className="btn-primary market-use-btn"
                    onClick={() => handleUse(c)}
                    disabled={forkingId === c.id}
                  >
                    {forkingId === c.id ? '添加中…' : '使用'}
                  </button>
                </div>
              )
            })}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="market-pagination">
              <button
                type="button"
                className="btn-ghost"
                disabled={page <= 1}
                onClick={() => fetchCards(page - 1, sort, query)}
              >
                上一页
              </button>
              <span className="market-page-info">{page} / {totalPages}</span>
              <button
                type="button"
                className="btn-ghost"
                disabled={page >= totalPages}
                onClick={() => fetchCards(page + 1, sort, query)}
              >
                下一页
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
