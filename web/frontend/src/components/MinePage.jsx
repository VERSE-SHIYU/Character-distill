import { useState, useEffect } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout } from '../api/client'
import AuthorPage from './AuthorPage'
import Avatar from './common/Avatar'

export default function MinePage() {
  const setView = useAppStore((s) => s.setView)
  const setAuthorUserId = useAppStore((s) => s.setAuthorUserId)
  const authUser = useAppStore((s) => s.authUser)
  const [tab, setTab] = useState('home')
  const [following, setFollowing] = useState([])
  const [followingLoading, setFollowingLoading] = useState(false)

  useEffect(() => {
    if (authUser?.id) setAuthorUserId(authUser.id)
  }, [authUser?.id, setAuthorUserId])

  useEffect(() => {
    if (tab === 'following') {
      setFollowingLoading(true)
      fetchWithTimeout('/api/market/my/following')
        .then((r) => r.json())
        .then((data) => setFollowing(data.users || []))
        .catch(() => {})
        .finally(() => setFollowingLoading(false))
    }
  }, [tab])

  const handleTabChange = (newTab) => {
    if (newTab === 'home') setAuthorUserId(authUser?.id)
    setTab(newTab)
  }

  return (
    <div className="mine-page">
      <div className="mine-top-tabs">
        <button type="button" className={`mine-top-tab${tab === 'home' ? ' active' : ''}`} onClick={() => handleTabChange('home')}>
          {'\u{1F3E0}'} 主页
        </button>
        <button type="button" className={`mine-top-tab${tab === 'following' ? ' active' : ''}`} onClick={() => handleTabChange('following')}>
          {'\u{2B50}'} 关注
        </button>
      </div>
      <div className="mine-content">
        {tab === 'home' && <AuthorPage embedded />}
        {tab === 'following' && (
          <div className="panel" style={{ padding: 20 }}>
            <h2 className="profile-section-title">我的关注</h2>
            {followingLoading ? (
              <p style={{ fontSize: 13, color: 'var(--text-dim)' }}>加载中…</p>
            ) : following.length === 0 ? (
              <p style={{ fontSize: 13, color: 'var(--text-dim)' }}>还没有关注任何人</p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {following.map((u) => (
                  <button key={u.id} type="button" className="market-card" onClick={() => { setAuthorUserId(u.id); setView('author') }}>
                    <Avatar name={u.username} size={40} />
                    <div className="market-card-body">
                      <div className="market-card-name">{u.username}</div>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

    </div>
  )
}
