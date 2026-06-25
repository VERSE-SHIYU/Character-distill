import { useState, useEffect, useRef, useCallback } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout, getAuthHeaders, exportCard } from '../api/client'
import Avatar from './common/Avatar'
import Loading from './common/Loading'
import { SkeletonCard } from './common/Skeleton'
import PostCard from './common/PostCard'
import BannerCropModal from './common/BannerCropModal'
import ImageCropModal from './common/ImageCropModal'
import ConfirmModal from './common/ConfirmModal'
import { Theater, Book, MessageSquare } from './common/Icon'
import { parseCardJson } from '../utils/card'
import { formatChatTime } from '../utils/time'
import { getCoverGradient } from './BookReader'

/* ── MineCardMenu ── */
function MineCardMenu({ card, onRefresh }) {
  const [open, setOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState(null)

  const handleTogglePublic = async (e) => {
    e.stopPropagation()
    try {
      await fetchWithTimeout(`/api/market/${card.id}/visibility`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ visibility: card.visibility === 'public' ? 'private' : 'public' }),
      })
      onRefresh()
    } catch {}
    setOpen(false)
  }

  const handleDelete = async (e) => {
    e.stopPropagation()
    const cardData = parseCardJson(card)
    const name = cardData.name || card.name || '?'
    setDeleteTarget(name)
  }

  const handleConfirmDelete = async () => {
    setDeleteTarget(null)
    try {
      await fetchWithTimeout(`/api/cards/${card.id}`, {
        method: 'DELETE',
        headers: getAuthHeaders(),
      })
      onRefresh()
    } catch (err) {
      console.error('[MinePage] Delete failed:', err)
    }
    setOpen(false)
  }

  return (
    <div className="mine-card-menu-wrap">
      <button
        type="button"
        className="mine-card-menu-btn"
        onClick={(e) => { e.stopPropagation(); setOpen(!open) }}
      >⋯</button>
      {open && (
        <>
          <div className="mine-card-menu-backdrop" onClick={(e) => { e.stopPropagation(); setOpen(false) }} />
          <div className="mine-card-menu">
            <button type="button" onClick={handleTogglePublic}>
              {card.visibility === 'public' ? '取消公开' : '公开到市场'}
            </button>
            <button type="button" onClick={(e) => {
              e.stopPropagation()
              useAppStore.getState().setCurrentMarketCardId(card.id)
              useAppStore.getState().setView('marketCardDetail')
              setOpen(false)
            }}>编辑</button>
            <button type="button" onClick={(e) => {
              e.stopPropagation()
              setOpen(false)
              exportCard(card.id, 'raw').catch(err => alert('导出失败：' + err.message))
            }}>下载</button>
            <button type="button" className="mine-card-menu-danger" onClick={handleDelete}>删除</button>
          </div>
        </>
      )}
    </div>
      <ConfirmModal
        isOpen={!!deleteTarget}
        title="移入回收站"
        message={`确定删除「${deleteTarget || ''}」？将移入回收站，可在回收站中恢复。`}
        confirmText="移入回收站"
        onConfirm={handleConfirmDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  )
}

/* ── MinePage ── */
export default function MinePage() {
  const authUser = useAppStore((s) => s.authUser)
  const authorUserId = useAppStore((s) => s.authorUserId)
  const setAuthorUserId = useAppStore((s) => s.setAuthorUserId)
  const userAvatar = useAppStore((s) => s.userAvatar)
  const userBanner = useAppStore((s) => s.userBanner)
  const uploadUserBanner = useAppStore((s) => s.uploadUserBanner)
  const fetchUserBanner = useAppStore((s) => s.fetchUserBanner)
  const currentView = useAppStore((s) => s.currentView)
  const setView = useAppStore((s) => s.setView)
  const setPreviousView = useAppStore((s) => s.setPreviousView)
  const setMessageTargetUserId = useAppStore((s) => s.setMessageTargetUserId)
  const setMessageTargetUsername = useAppStore((s) => s.setMessageTargetUsername)

  // Store fetched author data when viewing others
  const [profileAuthor, setProfileAuthor] = useState(null)
  const [isFollowing, setIsFollowing] = useState(false)
  const [followsMe, setFollowsMe] = useState(false)
  const followLabel = isFollowing && followsMe ? '互相关注' : isFollowing ? '已关注' : followsMe ? '回关' : '关注'

  const isMe = currentView === 'mine' || (!!authorUserId && authorUserId === authUser?.id)

  const goToMessages = (userId, username) => {
    setPreviousView(isMe ? 'mine' : 'author', isMe ? null : { authorUserId })
    setMessageTargetUserId(userId)
    if (username) setMessageTargetUsername(username)
    setView('messages')
  }
  const userId = isMe ? authUser?.id : authorUserId
  const prof = isMe ? authUser : profileAuthor
  const username = prof?.username || '?'
  const avatarSrc = isMe ? userAvatar : profileAuthor?.avatar_data

  // Clear authorUserId when navigating to "mine" view (same component, no remount)
  useEffect(() => {
    if (currentView === 'mine') setAuthorUserId(null)
  }, [currentView]) // eslint-disable-line react-hooks/exhaustive-deps

  // NOTE: no unmount cleanup for authorUserId — the next page may have already
  // set a new value, and the stale cleanup would wipe it (race condition).

  const [tab, setTab] = useState('characters')
  const [loading, setLoading] = useState(true)
  const [cards, setCards] = useState([])
  const [texts, setTexts] = useState([])
  const [bioEditing, setBioEditing] = useState(false)
  const [bioDraft, setBioDraft] = useState('')
  const [followersCount, setFollowersCount] = useState(0)
  const [followingCount, setFollowingCount] = useState(0)
  const [refreshKey, setRefreshKey] = useState(0)
  const [deletePostId, setDeletePostId] = useState(null)

  // Posts state
  const [posts, setPosts] = useState([])
  const [postsLoading, setPostsLoading] = useState(false)
  const [postContent, setPostContent] = useState('')
  const [posting, setPosting] = useState(false)
  const [postLocation, setPostLocation] = useState('')
  const [locationQuery, setLocationQuery] = useState('')
  const [locationLoading, setLocationLoading] = useState(false)
  const [locationCoords, setLocationCoords] = useState(null)
  const [locationSuggestions, setLocationSuggestions] = useState([])
  const locSearchTimer = useRef(null)
  const locationCoordsRef = useRef(null)

  const handleAddLocation = () => {
    if (!navigator.geolocation) {
      alert('您的浏览器不支持定位功能')
      return
    }
    setLocationLoading(true)
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        try {
          const { latitude, longitude } = pos.coords
          const coords = { lat: latitude, lng: longitude }
          setLocationCoords(coords)
          locationCoordsRef.current = coords
          const res = await fetch(
            `https://nominatim.openstreetmap.org/reverse?format=json&lat=${latitude}&lon=${longitude}&zoom=17&addressdetails=1&accept-language=zh`
          )
          const data = await res.json()
          const a = data.address || {}
          const parts = [
            a.amenity || a.building || a.leisure || a.shop || a.office,
            a.suburb || a.neighbourhood || a.city_district,
            a.city || a.town || a.county,
          ].filter(Boolean)
          const address = parts.slice(0, 2).join(', ')
            || data.display_name?.split(',').slice(0, 2).join(',')
            || `${latitude.toFixed(4)}, ${longitude.toFixed(4)}`
          setPostLocation(address)
        } catch {
          const { latitude, longitude } = pos.coords
          const coords = { lat: latitude, lng: longitude }
          setLocationCoords(coords)
          locationCoordsRef.current = coords
          setPostLocation(`${latitude.toFixed(4)}, ${longitude.toFixed(4)}`)
        } finally {
          setLocationLoading(false)
        }
      },
      () => {
        alert('无法获取位置，请检查浏览器定位权限')
        setLocationLoading(false)
      },
      { enableHighAccuracy: true, timeout: 10000 }
    )
  }

  const handleLocInputChange = (value) => {
    if (locSearchTimer.current) clearTimeout(locSearchTimer.current)
    setLocationQuery(value)
    setLocationSuggestions([])
    const coords = locationCoordsRef.current
    if (!value || !coords) return
    locSearchTimer.current = setTimeout(async () => {
      const { lat, lng } = coords
      const d = 0.05
      const url = `https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(value)}&viewbox=${lng-d},${lat-d},${lng+d},${lat+d}&limit=8&accept-language=zh`
      console.log('[LOC] searching:', url)
      try {
        const res = await fetch(url)
        console.log('[LOC] status:', res.status)
        const data = await res.json()
        console.log('[LOC] results:', data?.length, data)
        if (Array.isArray(data)) {
          setLocationSuggestions(data.map(r => ({
            name: r.display_name.split(',').slice(0, 2).join(','),
            full: r.display_name,
          })))
        }
      } catch (e) { console.log('[LOC] error:', e) }
    }, 500)
  }

  const handleSuggestionSelect = (name) => {
    setPostLocation(name)
    setLocationQuery('')
    setLocationSuggestions([])
  }

  // Cleanup debounce on unmount
  useEffect(() => {
    return () => {
      if (locSearchTimer.current) clearTimeout(locSearchTimer.current)
    }
  }, [])

  // Following state
  const [following, setFollowing] = useState([])
  const [followingLoading, setFollowingLoading] = useState(false)
  const [followingLocked, setFollowingLocked] = useState(false)

  // Online status (self always visible)
  const [selfOnline, setSelfOnline] = useState(null)
  const [selfLastActive, setSelfLastActive] = useState('')
  const fetchSelfOnline = useCallback(async () => {
    if (!authUser?.id) return
    try {
      const res = await fetchWithTimeout(`/api/auth/user/${authUser.id}/online`)
      const data = await res.json()
      setSelfOnline(data.online)
      setSelfLastActive(data.last_active_at || '')
    } catch { /* ignore */ }
  }, [authUser?.id])

  useEffect(() => { fetchSelfOnline() }, [fetchSelfOnline])
  // Followers state
  const [followers, setFollowers] = useState([])
  const [followersLoading, setFollowersLoading] = useState(false)

  // Reading progress
  const [readingProgress, setReadingProgress] = useState({})

  const bannerInputRef = useRef(null)
  const [bannerCropFile, setBannerCropFile] = useState(null)
  const avatarInputRef = useRef(null)
  const [avatarCropFile, setAvatarCropFile] = useState(null)
  const bookCoverInputRef = useRef(null)
  const [bookCoverCropFile, setBookCoverCropFile] = useState(null)
  const [bookCoverTargetId, setBookCoverTargetId] = useState(null)
  const [showEmoji, setShowEmoji] = useState(false)
  const textareaRef = useRef(null)
  const emojiRef = useRef(null)

  // Emoji data
  const EMOJI_LIST = [
    { label: '常用', items: ['😀','😂','🤣','😊','😍','🥰','😘','😭','😢','😤','😡','🥺','😱','😴','🤔','🤗','🤩','😎','🙄','😏','😈','👻','💀','🤡','👍','👎','👏','🙏','💪','❤️','🔥','⭐','🎉','🎈','💯','✨','🌈','🌸','🍀'] },
    { label: '人物', items: ['👋','✌️','🤞','🤟','🤘','👌','🤏','👈','👉','👆','👇','☝️','✋','🤚','🖐️','🖖','👊','✊','🤛','🤜','🫶','🤝','💅','🧑‍💻','👨‍💻','👩‍💻','🧑‍🎨','💃','🕺'] },
    { label: '自然', items: ['🐶','🐱','🐭','🐹','🐰','🦊','🐻','🐼','🐨','🐯','🦁','🐮','🐷','🐸','🐵','🐔','🐧','🐦','🦋','🌻','🌹','🌺','🍎','🍕','🍔','🍟','🍦','☕','🍰','🧁'] },
  ]

  const insertEmoji = (emoji) => {
    const ta = textareaRef.current
    if (!ta) { setPostContent(prev => prev + emoji); return }
    const start = ta.selectionStart
    const end = ta.selectionEnd
    const text = postContent
    const newText = text.slice(0, start) + emoji + text.slice(end)
    setPostContent(newText)
    requestAnimationFrame(() => {
      ta.selectionStart = ta.selectionEnd = start + emoji.length
      ta.focus()
    })
  }

  useEffect(() => {
    const handler = (e) => {
      if (emojiRef.current && !emojiRef.current.contains(e.target)) setShowEmoji(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const loadData = () => {
    if (!userId) return
    setFollowers([])
    setFollowing([])
    setFollowingLocked(false)
    setLoading(true)
    Promise.all([
      fetchWithTimeout(`/api/market/author/${userId}`).then(r => r.json()),
      !isMe ? Promise.resolve(null) : fetchUserBanner?.(),
    ]).then(([authorData]) => {
      setCards(authorData.cards || [])
      setTexts(authorData.texts || [])
      setFollowersCount(authorData.followers_count || 0)
      setFollowingCount(authorData.following_count || 0)
      if (!isMe) {
        if (authorData.author) setProfileAuthor(authorData.author)
        setIsFollowing(authorData.is_following || false)
        setFollowsMe(authorData.follows_me || false)
      }
    }).catch(() => {})
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadData() }, [userId, refreshKey]) // eslint-disable-line react-hooks/exhaustive-deps

  // Tab data lazy loading
  useEffect(() => {
    if (tab === 'following') {
      setFollowingLoading(true)
      setFollowingLocked(false)
      const url = isMe
        ? '/api/market/my/following'
        : `/api/market/author/${userId}/following`
      fetchWithTimeout(url)
        .then(r => r.json())
        .then(data => {
          if (data.locked) {
            setFollowingLocked(true)
            setFollowing([])
          } else {
            setFollowing(data.following || data.users || [])
          }
        })
        .catch(() => {})
        .finally(() => setFollowingLoading(false))
    }
    if (tab === 'followers') {
      setFollowersLoading(true)
      fetchWithTimeout(`/api/market/author/${userId}/followers`)
        .then(r => r.json())
        .then(data => setFollowers(data.followers || []))
        .catch(() => {})
        .finally(() => setFollowersLoading(false))
    }
    if (tab === 'posts') {
      setPostsLoading(true)
      fetchWithTimeout(`/api/market/author/${userId}/posts`)
        .then(r => r.json())
        .then(data => setPosts(data.posts || []))
        .catch(() => {})
        .finally(() => setPostsLoading(false))
    }
    if (tab === 'books') {
      fetchWithTimeout('/api/text/reading-progress/all')
        .then(r => r.json())
        .then(data => {
          const map = {}
          ;(data || []).forEach(p => { map[p.text_id] = p })
          setReadingProgress(map)
        })
        .catch(() => {})
    }
  }, [tab, userId]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleRefresh = () => setRefreshKey(k => k + 1)

  const handleFollow = async () => {
    if (!authorUserId) return
    try {
      const res = await fetchWithTimeout(`/api/market/author/${authorUserId}/follow`, {
        method: 'POST',
        headers: { ...getAuthHeaders() },
      })
      const data = await res.json()
      setIsFollowing(data.following)
    } catch {}
  }

  const handleBannerSelect = (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setBannerCropFile(file)
    e.target.value = ''
  }

  const handleBannerCropConfirm = async (croppedDataUrl) => {
    setBannerCropFile(null)
    // Helper: re-encode at given quality
    const reEncode = (dataUrl, q) => new Promise((resolve) => {
      const img = new Image()
      img.onload = () => {
        const c = document.createElement('canvas')
        c.width = img.width
        c.height = img.height
        c.getContext('2d').drawImage(img, 0, 0)
        resolve(c.toDataURL('image/jpeg', q))
      }
      img.src = dataUrl
    })
    // Step down quality until under 300 KB
    let q = 0.85
    let result = croppedDataUrl
    while (result.length >= 300_000 && q > 0.1) {
      result = await reEncode(result, q)
      q -= 0.1
    }
    try { await uploadUserBanner(result) } catch (e) {
      console.error('[MinePage] Banner upload failed:', e)
    }
  }

  const handleBannerCropCancel = () => setBannerCropFile(null)

  const handleAvatarSelect = (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setAvatarCropFile(file)
    e.target.value = ''
  }

  const handleAvatarCropConfirm = async (croppedDataUrl) => {
    setAvatarCropFile(null)
    try {
      await fetchWithTimeout('/api/auth/avatar', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ avatar_data: croppedDataUrl }),
      })
      useAppStore.setState({ userAvatar: croppedDataUrl })
    } catch (e) {
      console.error('[MinePage] Avatar upload failed:', e)
    }
  }

  const handleAvatarCropCancel = () => setAvatarCropFile(null)

  const handleBookCoverSelect = (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setBookCoverCropFile(file)
    e.target.value = ''
  }

  const handleBookCoverCropConfirm = async (croppedDataUrl) => {
    const targetId = bookCoverTargetId
    setBookCoverCropFile(null)
    setBookCoverTargetId(null)
    // Re-encode at decreasing quality until under 300 KB
    const reEncode = (dataUrl, q) => new Promise((resolve) => {
      const img = new Image()
      img.onload = () => {
        const c = document.createElement('canvas')
        c.width = img.width
        c.height = img.height
        c.getContext('2d').drawImage(img, 0, 0)
        resolve(c.toDataURL('image/jpeg', q))
      }
      img.src = dataUrl
    })
    let q = 0.85
    let result = croppedDataUrl
    while (result.length >= 300_000 && q > 0.1) {
      result = await reEncode(result, q)
      q -= 0.1
    }
    try {
      await fetchWithTimeout(`/api/text/${targetId}/cover`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cover_data: result }),
      })
      // Update local state to trigger re-render
      setTexts(prev => prev.map(t =>
        t.id === targetId ? { ...t, cover_data: result } : t
      ))
    } catch (e) {
      console.error('[MinePage] Book cover upload failed:', e)
    }
  }

  const handleBookCoverCropCancel = () => { setBookCoverCropFile(null); setBookCoverTargetId(null) }

  const tabs = [
    { key: 'characters', label: '角色', icon: <Theater size={15} /> },
    { key: 'books', label: '书籍', icon: <Book size={15} /> },
    { key: 'posts', label: '动态', icon: <MessageSquare size={15} /> },
    { key: 'followers', label: '粉丝', icon: <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="19" y1="8" x2="19" y2="14"/><line x1="22" y1="11" x2="16" y2="11"/></svg> },
    { key: 'following', label: '关注', icon: <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg> },
    { key: 'messages', label: '私信', icon: <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg> },
  ]

  if (loading) return (
    <div className="mine-page-v2" style={{ padding: 20 }}>
      <div className="market-grid-v2">
        {[1, 2, 3, 4].map((i) => <SkeletonCard key={i} />)}
      </div>
    </div>
  )

  return (
    <div className="mine-page-v2">
      {/* ── Banner ── */}
      <div className="mine-banner">
        {(isMe ? userBanner : profileAuthor?.banner_data) ? (
          <>
            <img src={isMe ? userBanner : profileAuthor?.banner_data} alt="" className="mine-banner-blur" aria-hidden="true" />
            <img src={isMe ? userBanner : profileAuthor?.banner_data} alt="" className="mine-banner-img" />
          </>
        ) : (
          <div className="mine-banner-fallback" />
        )}
        <div className="mine-banner-overlay" />
        {isMe && (
          <button type="button" className="mine-banner-upload" onClick={() => bannerInputRef.current?.click()} title="更换封面">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg> 更换封面
          </button>
        )}
        {isMe && <input ref={bannerInputRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={handleBannerSelect} />}
        {isMe && <input ref={bookCoverInputRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={handleBookCoverSelect} />}
      </div>

      {/* ── Profile Section ── */}
      <div className="mine-profile-section">
        <div className="mine-profile-row">
          <div className="mine-profile-left">
            <div className="mine-avatar-wrap">
              <Avatar name={username} src={avatarSrc} size={60} onClick={isMe ? () => avatarInputRef.current?.click() : undefined} />
              {isMe && (
                <>
                  <div className="mine-avatar-overlay" onClick={() => avatarInputRef.current?.click()}>
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M23 19a2 2 0 01-2 2H3a2 2 0 01-2-2V8a2 2 0 012-2h4l2-3h6l2 3h4a2 2 0 012 2z"/><circle cx="12" cy="13" r="4"/></svg>
                  </div>
                  <input ref={avatarInputRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={handleAvatarSelect} />
                </>
              )}
            </div>
          </div>
          <div className="mine-profile-mid">
            <div className="mine-name-row">
              <h2 className="mine-profile-name">
                {username}
                {selfOnline !== null && (
                  <span style={{ fontSize: 12, fontWeight: 400, marginLeft: 8, color: selfOnline ? '#22c55e' : 'var(--text-dim)', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                    <span style={{ width: 6, height: 6, borderRadius: '50%', background: selfOnline ? '#22c55e' : 'var(--text-dim)', display: 'inline-block', flexShrink: 0 }} />
                    {selfOnline ? '在线' : formatChatTime(selfLastActive)}
                  </span>
                )}
              </h2>
              {isMe && <button className="mine-edit-icon" onClick={() => setView('settings')} title="编辑资料">✏️</button>}
            </div>
            {isMe ? (
              bioEditing ? (
                <input
                  className="mine-bio-input"
                  value={bioDraft}
                  onChange={e => setBioDraft(e.target.value)}
                  onBlur={async () => {
                    setBioEditing(false)
                    const bio = bioDraft.trim()
                    if (bio !== (authUser?.bio || '')) {
                      try {
                        await fetchWithTimeout('/api/auth/bio', {
                          method: 'PUT',
                          headers: { 'Content-Type': 'application/json' },
                          body: JSON.stringify({ bio }),
                        })
                        useAppStore.setState({ authUser: { ...authUser, bio } })
                      } catch {}
                    }
                  }}
                  onKeyDown={e => {
                    if (e.key === 'Enter') e.target.blur()
                    if (e.key === 'Escape') { setBioEditing(false); setBioDraft(authUser?.bio || '') }
                  }}
                  autoFocus
                  placeholder="写一句话介绍自己…"
                  maxLength={200}
                />
              ) : (
                <div className="mine-bio-display" onClick={() => { setBioDraft(authUser?.bio || ''); setBioEditing(true) }}>
                  {authUser?.bio ? authUser.bio : <span className="mine-bio-placeholder">点击添加个人简介</span>}
                </div>
              )
            ) : (
              <div className="mine-bio-display" style={{ cursor: 'default' }}>
                {profileAuthor?.bio || ''}
              </div>
            )}
          </div>
          <div className="mine-profile-stats">
            <button type="button" className="mine-stat-btn" onClick={() => setTab('characters')}>
              <span className="mine-stat-num">{cards.length}</span>角色
            </button>
            <span className="mine-stat-dot">·</span>
            <button type="button" className="mine-stat-btn" onClick={() => setTab('books')}>
              <span className="mine-stat-num">{texts.length}</span>书籍
            </button>
            <span className="mine-stat-dot">·</span>
            <button type="button" className="mine-stat-btn" onClick={() => setTab('followers')}>
              <span className="mine-stat-num">{followersCount}</span>粉丝
            </button>
            <span className="mine-stat-dot">·</span>
            <button type="button" className="mine-stat-btn" onClick={() => setTab('following')}>
              <span className="mine-stat-num">{followingCount}</span>关注
            </button>
          </div>
          {!isMe && (
            <div className="mine-profile-actions">
              <button
                type="button"
                className="btn-primary btn-sm"
                onClick={() => goToMessages(authorUserId, profileAuthor?.username || '')}
              >
                发私信
              </button>
              <button
                type="button"
                className={`btn-sm ${isFollowing ? 'btn-secondary' : 'btn-primary'}`}
                onClick={handleFollow}
              >
                {followLabel}
              </button>
            </div>
          )}
        </div>
      </div>

      {/* ── Tab 栏 ── */}
      <div className="mine-tab-bar">
        {tabs.filter(t => isMe || t.key !== 'messages').map(t => (
          <button
            key={t.key}
            type="button"
            className={`mine-tab${tab === t.key ? ' active' : ''}`}
            onClick={() => t.key === 'messages' ? (setPreviousView(isMe ? 'mine' : 'author', isMe ? null : { authorUserId }), setView('messages')) : setTab(t.key)}
          >
            {t.icon} {t.label}
          </button>
        ))}
      </div>

      {/* ── Tab 内容 ── */}
      <div className="mine-tab-content">
        {/* 角色 tab */}
        {tab === 'characters' && (
          cards.length === 0 ? (
            isMe ? (
              <div className="mine-onboard-card">
                <h3 className="mine-onboard-title">创建你的第一个角色</h3>
                <div className="mine-onboard-steps">
                  <div className="mine-onboard-step">
                    <span className="mine-onboard-step-num">1</span>
                    <span>上传小说或聊天记录</span>
                  </div>
                  <div className="mine-onboard-step">
                    <span className="mine-onboard-step-num">2</span>
                    <span>AI 自动蒸馏角色卡</span>
                  </div>
                  <div className="mine-onboard-step">
                    <span className="mine-onboard-step-num">3</span>
                    <span>公开分享到角色市场</span>
                  </div>
                </div>
                <button type="button" className="btn-primary" onClick={() => setView('text')}>
                  去上传文本
                </button>
              </div>
            ) : (
              <div className="mine-onboard-card">
                <h3 className="mine-onboard-title">暂无公开角色</h3>
                <p className="mine-onboard-desc">该用户还没有公开的角色卡</p>
              </div>
            )
          ) : (
            <div className="market-grid-v2">
              {cards.map(card => {
                const cardData = parseCardJson(card)
                const name = cardData.name || card.name || '?'
                const identity = cardData.identity || ''
                const isNew = card.created_at && (Date.now() - new Date(card.created_at).getTime()) < 3 * 24 * 60 * 60 * 1000
                const isPublic = card.visibility === 'public'
                return (
                  <div key={card.id} className="market-card-v2" onClick={() => {
                    useAppStore.getState().setCurrentMarketCardId(card.id)
                    setView('marketCardDetail')
                  }}>
                    <div className="market-card-v2-cover">
                      {card.avatar_data ? (
                        <>
                          <img src={card.avatar_data} alt="" className="market-card-v2-cover-blur" aria-hidden="true" />
                          <img src={card.avatar_data} alt={name} className="market-card-v2-cover-img" />
                        </>
                      ) : (
                        <div className="market-card-v2-cover-fallback">
                          <span className="market-card-v2-fallback-letter">{name.charAt(0).toUpperCase()}</span>
                        </div>
                      )}
                      <div className="market-card-v2-badges">
                        {isNew && <span className="card-badge-new">新创建</span>}
                        {isPublic && <span className="card-badge-public" title="已公开到市场" />}
                      </div>
                    </div>
                    <div className="market-card-v2-glass-info">
                      <div className="market-card-v2-name">{name}</div>
                      {identity && <div className="market-card-v2-identity">{identity}</div>}
                      <div className="market-card-v2-stats">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>
                        {card.likes ?? 0}
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                        {card.chat_count ?? 0}
                        {card.visibility === 'public' ? (
                          <><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>公开</>
                        ) : (
                          <><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>私有</>
                        )}
                      </div>
                      {card.text_title && (
                        <div className="market-card-v2-source">来自《{card.text_title}》</div>
                      )}
                    </div>
                    {isMe && <MineCardMenu card={card} onRefresh={handleRefresh} />}
                  </div>
                )
              })}
            </div>
          )
        )}

        {/* 书籍 tab */}
        {tab === 'books' && (
          texts.length === 0 ? (
            <div className="mine-onboard-card">
              <h3 className="mine-onboard-title">{isMe ? '还没有上传文本' : '暂无公开书籍'}</h3>
              <p className="mine-onboard-desc">{isMe ? '上传小说或聊天记录，AI 会自动识别并蒸馏角色' : '该用户还没有公开的书籍'}</p>
              {isMe && <button type="button" className="btn-primary" onClick={() => setView('text')}>
                去上传
              </button>}
            </div>
          ) : (
            <div className="mine-books-grid">
              {texts.map(text => {
                const progress = readingProgress[text.id]
                const pct = progress ? Math.round((progress.progress || 0) * 100) : 0
                const [g1, g2] = getCoverGradient(text.id)
                const title = text.title || text.filename || '未命名'
                return (
                  <div
                    key={text.id}
                    className="mine-book-cover-card"
                    onClick={() => {
                      useAppStore.getState().setReaderTextId(text.id)
                      setView('reader')
                    }}
                  >
                    <div className="mine-book-cover-bg">
                      {text.cover_data ? (
                        <img className="mine-book-cover-img" src={text.cover_data} alt={title} />
                      ) : (
                        <div className="mine-book-cover-gradient" style={{ background: `linear-gradient(135deg, ${g1}, ${g2})` }} />
                      )}
                      <span className="mine-book-cover-title">{title}</span>
                      {isMe && (
                        <button
                          type="button"
                          className="mine-book-cover-edit"
                          onClick={(e) => {
                            e.stopPropagation()
                            setBookCoverTargetId(text.id)
                            bookCoverInputRef.current?.click()
                          }}
                          title="更换封面"
                        >
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>
                          换封面
                        </button>
                      )}
                    </div>
                    {pct > 0 && (
                      <div className="mine-book-cover-progress">
                        <div className="mine-book-cover-progress-bar" style={{ width: `${pct}%` }} />
                      </div>
                    )}
                    <span className="mine-book-cover-label">{pct > 0 ? `${pct}%` : '开始阅读'}</span>
                  </div>
                )
              })}
            </div>
          )
        )}

        {/* 动态 tab */}
        {tab === 'posts' && (
          <>
            {isMe && (
              <div className="mine-composer">
                <textarea
                  ref={textareaRef}
                  className="mine-composer-input"
                  placeholder="写点什么…"
                  rows={3}
                  value={postContent}
                  onChange={(e) => setPostContent(e.target.value)}
                />
                <div className="mine-composer-toolbar">
                  <div className="mine-composer-emoji-wrap" ref={emojiRef}>
                    <button
                      type="button"
                      className="mine-composer-emoji-btn"
                      onClick={() => setShowEmoji(prev => !prev)}
                      title="表情"
                    >
                      😊
                    </button>
                    {showEmoji && (
                      <div className="mine-composer-emoji-panel">
                        {EMOJI_LIST.map(group => (
                          <div key={group.label} className="emoji-group">
                            <div className="emoji-group-label">{group.label}</div>
                            <div className="emoji-group-grid">
                              {group.items.map(em => (
                                <button
                                  key={em}
                                  type="button"
                                  className="emoji-item"
                                  onClick={() => insertEmoji(em)}
                                >
                                  {em}
                                </button>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                  {postLocation ? (
                    <div className="mine-loc-active-wrap">
                      <div className="mine-loc-selected">
                        <svg className="mine-loc-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>
                        <span className="mine-loc-selected-text">{postLocation}</span>
                        <button
                          type="button"
                          className="mine-composer-loc-clear"
                          onClick={() => { setPostLocation(''); setLocationQuery(''); setLocationSuggestions([]) }}
                          title="取消位置"
                        >
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                        </button>
                      </div>
                      <div className="mine-loc-search-box">
                        <input
                          type="text"
                          className="mine-loc-input"
                          value={locationQuery}
                          onChange={(e) => handleLocInputChange(e.target.value)}
                          placeholder="搜索附近地点"
                        />
                        {locationSuggestions.length > 0 && (
                          <div className="mine-loc-suggestions">
                            {locationSuggestions.map((s, i) => (
                              <button
                                key={i}
                                type="button"
                                className="mine-loc-suggestion-item"
                                onClick={() => handleSuggestionSelect(s.name)}
                                title={s.full}
                              >
                                {s.name}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  ) : (
                    <button
                      type="button"
                      className="mine-composer-loc-btn"
                      onClick={handleAddLocation}
                      disabled={locationLoading}
                      title="添加位置"
                    >
                      {locationLoading ? (
                        <>
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="spin-icon"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
                          定位中…
                        </>
                      ) : (
                        <>
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>
                          添加位置
                        </>
                      )}
                    </button>
                  )}
                  <div style={{ flex: 1 }} />
                  <button
                    type="button"
                    className="btn-primary btn-sm"
                    disabled={!postContent.trim() || posting}
                    onClick={async () => {
                      setPosting(true)
                      try {
                        await fetchWithTimeout('/api/market/author/posts', {
                          method: 'POST',
                          headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
                          body: JSON.stringify({ content: postContent, visibility: 'public', location: postLocation }),
                        })
                        setPostContent('')
                        setPostLocation('')
                        const res = await fetchWithTimeout(`/api/market/author/${userId}/posts`)
                        const data = await res.json()
                        setPosts(data.posts || [])
                      } catch {}
                      finally { setPosting(false) }
                    }}
                  >
                    {posting ? '发布中…' : '发布'}
                  </button>
                </div>
              </div>
            )}

            {postsLoading ? (
              <Loading text="加载动态…" />
            ) : posts.length === 0 ? (
              <div className="mine-onboard-card">
                <h3 className="mine-onboard-title">{isMe ? '还没有动态' : '暂无动态'}</h3>
                <p className="mine-onboard-desc">{isMe ? '发一条让别人认识你' : '该用户还没有发布动态'}</p>
                {isMe && <button type="button" className="btn-primary" onClick={() => document.querySelector('.mine-composer-input')?.focus()}>
                  发动态
                </button>}
              </div>
            ) : (
              <div className="mine-posts-list">
                {posts.map(p => (
                  <PostCard
                    key={p.id}
                    post={p}
                    onLike={async (id) => {
                      await fetchWithTimeout(`/api/market/post/${id}/like`, { method: 'POST', headers: getAuthHeaders() })
                      const res = await fetchWithTimeout(`/api/market/author/${userId}/posts`)
                      const data = await res.json()
                      setPosts(data.posts || [])
                    }}
                    onAuthorClick={(userId) => { setAuthorUserId(userId); setView('author') }}
                    onDelete={(id) => setDeletePostId(id)}
                    showDelete={isMe}
                  />
                ))}
              </div>
            )}
          </>
        )}

        {/* 关注 tab */}
        {/* 粉丝 tab */}
        {tab === 'followers' && (
          followersLoading ? (
            <Loading text="加载粉丝…" />
          ) : followers.length === 0 ? (
            <div className="mine-onboard-card">
              <h3 className="mine-onboard-title">{isMe ? '还没有粉丝' : '暂无粉丝'}</h3>
              <p className="mine-onboard-desc">{isMe ? '发布优质角色卡和动态来吸引粉丝' : '该用户还没有粉丝'}</p>
            </div>
          ) : (
            <div className="mine-follow-list">
              {followers.map(f => (
                <div key={f.id} className="mine-follow-card">
                  <button type="button" className="mine-follow-card-main" onClick={() => { setAuthorUserId(f.id); setView('author') }}>
                    <Avatar name={f.username || '?'} size={44} src={f.avatar_data} />
                    <div className="mine-follow-info">
                      <span className="mine-follow-name">{f.username}</span>
                      {f.bio && <span className="mine-follow-bio">{f.bio}</span>}
                    </div>
                  </button>
                  <div className="mine-follow-actions">
                    {isMe && (
                      <button
                        type="button"
                        className="btn-sm btn-outline"
                        onClick={() => goToMessages(f.id, f.username)}
                      >
                        发私信
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )
        )}
        {tab === 'following' && (
          followingLoading ? (
            <Loading text="加载中…" />
          ) : followingLocked ? (
            <div className="mine-onboard-card">
              <h3 className="mine-onboard-title">🔒 关注列表未公开</h3>
              <p className="mine-onboard-desc">该用户未公开关注列表</p>
            </div>
          ) : following.length === 0 ? (
            <div className="mine-onboard-card">
              <h3 className="mine-onboard-title">还没有关注任何人</h3>
              <p className="mine-onboard-desc">去角色市场发现有趣的创作者</p>
              <button type="button" className="btn-primary" onClick={() => setView('market')}>
                去市场看看
              </button>
            </div>
          ) : (
            <div className="mine-following-list">
              {following.map(u => (
                <div key={u.id} className="mine-following-card">
                  <Avatar name={u.username || '?'} src={u.avatar_data} size={44} />
                  <div className="mine-following-info">
                    <button
                      type="button"
                      className="mine-following-name"
                      onClick={() => { setAuthorUserId(u.id); setView('author') }}
                    >
                      {u.username}
                    </button>
                    <span className="mine-following-meta">
                      {u.cards_count ?? 0} 个角色
                    </span>
                  </div>
                  <div className="mine-following-actions">
                    <button
                      type="button"
                      className="btn-sm btn-outline"
                      onClick={() => goToMessages(u.id, u.username)}
                    >
                      私信
                    </button>
                    <button
                      type="button"
                      className="btn-sm btn-secondary"
                      onClick={async () => {
                        await fetchWithTimeout(`/api/market/author/${u.id}/follow`, {
                          method: 'POST',
                          headers: getAuthHeaders(),
                        })
                        setFollowing(prev => prev.filter(f => f.id !== u.id))
                        setFollowingCount(prev => Math.max(0, prev - 1))
                      }}
                    >
                      取消关注
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )
        )}
      </div>

      <BannerCropModal
        file={bannerCropFile}
        onConfirm={handleBannerCropConfirm}
        onCancel={handleBannerCropCancel}
      />
      <BannerCropModal
        aspect={3 / 4}
        file={bookCoverCropFile}
        onConfirm={handleBookCoverCropConfirm}
        onCancel={handleBookCoverCropCancel}
      />
      <ImageCropModal
        file={avatarCropFile}
        onConfirm={handleAvatarCropConfirm}
        onCancel={handleAvatarCropCancel}
      />
      <ConfirmModal
        isOpen={!!deletePostId}
        title="删除动态"
        message="确定删除这条动态？"
        confirmText="删除"
        onConfirm={async () => {
          const id = deletePostId
          setDeletePostId(null)
          await fetchWithTimeout(`/api/market/posts/${id}`, { method: 'DELETE', headers: getAuthHeaders() })
          setPosts(prev => prev.filter(p => p.id !== id))
        }}
        onCancel={() => setDeletePostId(null)}
      />
    </div>
  )
}
