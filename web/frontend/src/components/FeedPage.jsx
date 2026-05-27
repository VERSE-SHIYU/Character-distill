import { useCallback, useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout } from '../api/client'
import PostCard from './common/PostCard'
import Loading from './common/Loading'

const PAGE_SIZE = 20

function getDateKey(iso) {
  if (!iso) return ''
  let s = iso
  if (!s.includes('T')) s = s.replace(' ', 'T')
  if (!s.endsWith('Z') && !s.includes('+')) s += 'Z'
  const date = new Date(s)
  if (isNaN(date.getTime())) return ''
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

function getDateLabel(iso) {
  if (!iso) return ''
  let s = iso
  if (!s.includes('T')) s = s.replace(' ', 'T')
  if (!s.endsWith('Z') && !s.includes('+')) s += 'Z'
  const date = new Date(s)
  if (isNaN(date.getTime())) return ''

  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const target = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  const dayDiff = Math.floor((today - target) / 86400000)

  if (dayDiff === 0) return '今天'
  if (dayDiff === 1) return '昨天'
  if (date.getFullYear() === now.getFullYear()) {
    return `${date.getMonth() + 1}月${date.getDate()}日`
  }
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`
}

export default function FeedPage() {
  const setView = useAppStore((s) => s.setView)
  const setAuthorUserId = useAppStore((s) => s.setAuthorUserId)

  const [posts, setPosts] = useState([])
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [hasMore, setHasMore] = useState(true)
  const [error, setError] = useState(null)
  const [showBackTop, setShowBackTop] = useState(false)
  const sentinelRef = useRef(null)
  const loadingRef = useRef(false)

  useEffect(() => {
    const onScroll = () => setShowBackTop(window.scrollY > 800)
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

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
    if (!sentinelRef.current || !hasMore) return
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting && !loadingRef.current) setPage(p => p + 1)
    }, { threshold: 0.1 })
    observer.observe(sentinelRef.current)
    return () => observer.disconnect()
  }, [hasMore])

  useEffect(() => {
    loadingRef.current = loading
  }, [loading])

  useEffect(() => {
    if (page > 1) fetchPosts(page, true)
  }, [page]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleRefresh = () => {
    setPage(1)
    fetchPosts(1)
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

  // Build rendered list interleaving date dividers
  let lastDateKey = null
  const rendered = []
  posts.forEach((p) => {
    const dk = getDateKey(p.created_at)
    if (dk && dk !== lastDateKey) {
      rendered.push({ type: 'divider', key: `date-${dk}`, label: getDateLabel(p.created_at) })
      lastDateKey = dk
    }
    rendered.push({ type: 'post', key: p.id, data: p })
  })

  return (
    <div className="panel">
      <header className="panel-header">
        <div className="feed-header-row">
          <h1 className="panel-title">动态</h1>
          <button type="button" className="feed-refresh-btn" onClick={handleRefresh} disabled={loading}>
            刷新
          </button>
        </div>
      </header>

      {error && <div className="error-box">{error}</div>}

      {loading && posts.length === 0 && <Loading text="加载动态…" />}

      {!loading && !error && posts.length === 0 && (
        <div className="shell-placeholder">
          <div className="shell-placeholder-inner">
            <div className="shell-placeholder-icon">
              <svg className="shell-empty-svg" viewBox="0 0 64 64" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 36l52-28-14 28-14 4" />
                <path d="M16 38l14 20 6-24" />
              </svg>
            </div>
            <div className="shell-placeholder-title">还没有动态</div>
            <div className="shell-placeholder-sub">
              去关注一些创作者，他们的动态会显示在这里
            </div>
          </div>
        </div>
      )}

      {posts.length > 0 && (
        <div className="feed-list">
          {rendered.map((item) =>
            item.type === 'divider' ? (
              <div key={item.key} className="feed-date-divider">{item.label}</div>
            ) : (
              <PostCard
                key={item.key}
                post={item.data}
                onLike={handleLike}
                onAuthorClick={(userId) => { setAuthorUserId(userId); setView('author') }}
              />
            )
          )}
        </div>
      )}

      {hasMore && <div ref={sentinelRef} style={{ height: 1 }} />}
      {loading && posts.length > 0 && <div className="feed-loading-more">加载更多…</div>}

      {showBackTop && (
        <button type="button" className="feed-back-top" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}>
          ↑
        </button>
      )}
    </div>
  )
}
