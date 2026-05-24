import { useState, useEffect } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout } from '../api/client'

export default function MinePage() {
  const setView = useAppStore((s) => s.setView)
  const setAuthorUserId = useAppStore((s) => s.setAuthorUserId)
  const authUser = useAppStore((s) => s.authUser)
  const [unreadCount, setUnreadCount] = useState(0)
  const [following, setFollowing] = useState([])
  const [followingLoading, setFollowingLoading] = useState(false)

  useEffect(() => {
    fetchWithTimeout('/api/messages/unread-count')
      .then((r) => r.json())
      .then((d) => setUnreadCount(d.count ?? 0))
      .catch(() => {})
  }, [])

  useEffect(() => {
    setFollowingLoading(true)
    fetchWithTimeout('/api/market/my/following')
      .then((r) => r.json())
      .then((data) => setFollowing(data.users || []))
      .catch(() => {})
      .finally(() => setFollowingLoading(false))
  }, [])

  return (
    <div className="profile-page">
      <header className="panel-header">
        <h1 className="panel-title">我的</h1>
      </header>

      <div className="profile-grid profile-grid-3">
        <button className="profile-grid-item" onClick={() => setView('messages')}>
          <span className="profile-grid-icon">{'\u{1F4E8}'}</span>
          <span className="profile-grid-label">私信</span>
          {unreadCount > 0 && <span className="profile-grid-badge">{unreadCount}</span>}
        </button>
        <button className="profile-grid-item" onClick={() => { setAuthorUserId(authUser?.id); setView('author') }}>
          <span className="profile-grid-icon">{'\u{1F464}'}</span>
          <span className="profile-grid-label">作者主页</span>
        </button>
        <button className="profile-grid-item profile-grid-item-placeholder">
          <span className="profile-grid-icon">{'\u{2B50}'}</span>
          <span className="profile-grid-label">我的关注</span>
        </button>
      </div>

      {/* 关注列表直接展示在下方 */}
      <div className="profile-card">
        <h2 className="profile-section-title">我的关注</h2>
        {followingLoading ? (
          <p style={{ fontSize: 13, color: 'var(--text-dim)' }}>加载中…</p>
        ) : following.length === 0 ? (
          <p style={{ fontSize: 13, color: 'var(--text-dim)' }}>还没有关注任何人</p>
        ) : (
          <div className="market-grid" style={{ marginTop: 8 }}>
            {following.map((u) => (
              <button key={u.id} type="button" className="market-card" onClick={() => { setAuthorUserId(u.id); setView('author') }}>
                <div className="market-card-body">
                  <div className="market-card-name">{u.username}</div>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
