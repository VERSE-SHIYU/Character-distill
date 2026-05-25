import { useCallback, useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout } from '../api/client'
import PostCard from './common/PostCard'
import Loading from './common/Loading'

const PAGE_SIZE = 20

export default function FeedPage() {
  const setView = useAppStore((s) => s.setView)
  const setAuthorUserId = useAppStore((s) => s.setAuthorUserId)

  const [posts, setPosts] = useState([])
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [hasMore, setHasMore] = useState(true)
  const [error, setError] = useState(null)
  const sentinelRef = useRef(null)

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
            <PostCard
              key={p.id}
              post={p}
              onLike={handleLike}
              onAuthorClick={(userId) => { setAuthorUserId(userId); setView('author') }}
            />
          ))}
        </div>
      )}

      {hasMore && <div ref={sentinelRef} style={{ height: 1 }} />}
      {loading && posts.length > 0 && <div className="feed-loading-more">加载更多…</div>}
    </div>
  )
}
