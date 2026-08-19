import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useAutoScroll } from '../hooks/useAutoScroll'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout, getAuthHeaders } from '../api/client'
import { formatChatTime } from '../utils/time'
import SplitOrFullscreen from './common/SplitOrFullscreen'
import ChatHistoryPanel from './common/ChatHistoryPanel'
import Avatar from './common/Avatar'
import {
  ArrowLeft, Clock, MessageCircle, Heart, Check, Shield, RefreshCw, AlertTriangle, MessageSquare, Wifi,
} from './common/Icon'
import { displayName } from '../utils/displayName'
import { mergeMessages } from '../utils/mergeMessages'
import ChatInputBar from './common/ChatInputBar'
import useIsMobile from '../hooks/useIsMobile'
const POLL_INTERVAL = 5000
const PAGE_SIZE = 30
const GROUP_GAP = 5 * 60 * 1000
const QUICK_EMOJIS = ['👍', '❤️', '😂', '😮', '😢', '🔥']

// Parse naive backend timestamps the same way utils/time.js does (treat as UTC).
const parseTS = (ts) => {
  if (!ts) return null
  let s = ts
  if (!s.includes('T')) s = s.replace(' ', 'T')
  if (!s.endsWith('Z') && !s.includes('+')) s += 'Z'
  const d = new Date(s)
  return isNaN(d.getTime()) ? null : d
}
const pad = (n) => String(n).padStart(2, '0')
const toHM = (ts) => {
  const d = parseTS(ts)
  return d ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : ''
}
const dayLabel = (ts) => {
  const d = parseTS(ts)
  if (!d) return ''
  const now = new Date()
  const t0 = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const t1 = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  const diff = Math.round((t0 - t1) / 86400000)
  if (diff <= 0) return '今天'
  if (diff === 1) return '昨天'
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日`
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`
}
const isRetractedMsg = (m) => m.retracted === 1 || m.retracted === true

const CheckDouble = ({ size = 12 }) => (
  <span className="dm-check-double">
    <Check size={size} />
    <Check size={size} className="dm-check-dup" />
  </span>
)

export default function PrivateMessageChat({ otherUserId, otherUsername }) {
  const authUser = useAppStore((s) => s.authUser)
  const userAvatar = useAppStore((s) => s.userAvatar)
  const currentCard = useAppStore((s) => s.currentCard)
  const affinity = useAppStore((s) => s.affinity)
  const fetchAffinity = useAppStore((s) => s.fetchAffinity)
  const refreshUnread = useAppStore((s) => s.refreshUnread)

  const [messages, setMessages] = useState([])
  const [loading, setLoading] = useState(true)
  const [inputText, setInputText] = useState('')
  const [page, setPage] = useState(1)
  const [hasMore, setHasMore] = useState(true)
  const [isOnline, setIsOnline] = useState(navigator.onLine)
  const [consent, setConsent] = useState(null) // { target_region, receiver_username } waiting for cross-border consent
  const [innerVoiceOpen, setInnerVoiceOpen] = useState(false)

  const listRef = useRef(null)
  const messagesEndRef = useRef(null)
  const prependAnchor = useRef(null) // scroll anchor recorded before loading older page
  const autoSendRef = useRef(false)
  const [otherAvatar, setOtherAvatar] = useState(null)
  const [otherHomeRegion, setOtherHomeRegion] = useState('')
  const [myHomeRegion, setMyHomeRegion] = useState('')
  const [otherOnline, setOtherOnline] = useState(null) // null=loading, true, false
  const [otherOnlineHidden, setOtherOnlineHidden] = useState(false)
  const [otherLastActive, setOtherLastActive] = useState('')
  const [reactions, setReactions] = useState({})

  const [historyOpen, setHistoryOpen] = useState(false)
  const [copiedId, setCopiedId] = useState(null)
  const [emojiPickerId, setEmojiPickerId] = useState(null)
  const isMobile = useIsMobile()

  const peerName = otherUsername || '对方'
  // home_region is not exposed via login/_user_response; both sides are read
  // through the public author endpoint, which returns the raw user dict.
  const isCrossRegion = !!(myHomeRegion && otherHomeRegion && myHomeRegion !== otherHomeRegion)
  // Inner voice reuses the active character session's affinity — only meaningful
  // when the DM peer owns the character we are currently talking to. Identity
  // gate only; the overlay shows a placeholder while affinity is null (no data)
  // or inner_voice is empty. Real numbers only render when affinity is non-null.
  const canShowInnerVoice = !!currentCard && currentCard.user_id === otherUserId

  // Load messages
  const loadMessages = useCallback(async (pageNum = 1, append = false) => {
    if (!otherUserId) return
    setLoading(true)
    try {
      const res = await fetchWithTimeout(`/api/messages/with/${otherUserId}?page=${pageNum}&page_size=${PAGE_SIZE}`)
      const data = await res.json()
      const msgs = data.messages || []
      if (append) {
        setMessages((prev) => [...msgs, ...prev])
      } else {
        setMessages(msgs)
      }
      setHasMore(msgs.length === PAGE_SIZE)
      setPage(pageNum)
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }, [otherUserId])

  // Merge poll results into local state: keep optimistic (temp / _status) and
  // older loaded pages, refresh server copy by id, preserve oldest→newest order.
  const mergePollMessages = useCallback((serverMsgs) => {
    setMessages((prev) => mergeMessages(prev, serverMsgs))
  }, [])

  // Poll only refreshes the newest page without touching pagination state
  const pollNewMessages = useCallback(async () => {
    if (!otherUserId) return
    try {
      const res = await fetchWithTimeout(`/api/messages/with/${otherUserId}?page=1&page_size=${PAGE_SIZE}`)
      const data = await res.json()
      mergePollMessages(data.messages || [])
    } catch {}
  }, [otherUserId, mergePollMessages])

  // Fetch reactions for the current conversation
  const fetchReactions = useCallback(async () => {
    if (!otherUserId) return
    try {
      const res = await fetchWithTimeout(`/api/messages/with/${otherUserId}/reactions`)
      const d = await res.json()
      setReactions(d.reactions || {})
    } catch {}
  }, [otherUserId])

  // React to a DM: optimistic local toggle, silent rollback to server truth on failure
  const handleReact = useCallback(async (messageId, emoji) => {
    const uid = authUser?.id
    if (!uid) return
    setReactions((prev) => {
      const list = prev[messageId] || []
      const existing = list.find((r) => r.emoji === emoji)
      let next
      if (!existing) {
        next = [...list, { emoji, count: 1, users: [uid] }]
      } else {
        const mine = existing.users.includes(uid)
        const users = mine ? existing.users.filter((u) => u !== uid) : [...existing.users, uid]
        const count = mine ? existing.count - 1 : existing.count + 1
        next = count <= 0 ? list.filter((r) => r.emoji !== emoji) : list.map((r) => r.emoji === emoji ? { ...r, count, users } : r)
      }
      return { ...prev, [messageId]: next }
    })
    try {
      const res = await fetchWithTimeout(`/api/messages/${messageId}/react`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ emoji }),
      })
      const d = await res.json().catch(() => ({}))
      if (!res.ok || d.ok !== true) throw new Error('react rejected')
    } catch {
      // silent rollback: refetch authoritative state
      fetchReactions()
    }
  }, [otherUserId, authUser?.id, fetchReactions])

  // Mark messages as read
  const markRead = useCallback(async () => {
    if (!otherUserId) return
    try {
      await fetchWithTimeout(`/api/messages/read/${otherUserId}`, {
        method: 'POST',
        headers: { ...getAuthHeaders() },
      })
      refreshUnread()
    } catch {
      // ignore
    }
  }, [otherUserId, refreshUnread])

  // ── Send (handles cross-border 409 consent) ──
  // Uses plain fetch (not fetchWithTimeout) because the 409 consent response must
  // be read here; fetchWithTimeout throws on any non-2xx before the caller can.
  const performSend = useCallback(async (content, tempId) => {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 30000)
    try {
      const res = await fetch('/api/messages/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ receiver_id: otherUserId, content }),
        signal: controller.signal,
      })
      if (res.status === 409) {
        const body = await res.json().catch(() => ({}))
        const d = body?.detail || {}
        setConsent({ target_region: d.target_region, receiver_username: d.receiver_username })
        setMessages((prev) => prev.map((m) => m.id === tempId ? { ...m, _status: 'consent' } : m))
        return
      }
      const data = await res.json().catch(() => ({}))
      if (res.ok && data.message) {
        setMessages((prev) => prev.map((m) => m.id === tempId ? { ...data.message, _status: 'sent' } : m))
      } else {
        setMessages((prev) => prev.map((m) => m.id === tempId ? { ...m, _status: 'failed' } : m))
      }
    } catch {
      setMessages((prev) => prev.map((m) => m.id === tempId ? { ...m, _status: 'failed' } : m))
    } finally {
      clearTimeout(timer)
    }
  }, [otherUserId])

  const handleSend = (textArg) => {
    const content = (textArg ?? inputText).trim()
    if (!content || !otherUserId) return
    const tempId = `temp-${Date.now()}`
    const optimisticMsg = {
      id: tempId,
      sender_id: authUser?.id,
      content,
      created_at: new Date().toISOString(),
      _status: isOnline ? 'sending' : 'queued',
    }
    setMessages((prev) => [...prev, optimisticMsg])
    if (isOnline) performSend(content, tempId)
  }

  const handleResend = async (failedMsg) => {
    setMessages((prev) => prev.map((m) => m.id === failedMsg.id ? { ...m, _status: 'sending' } : m))
    performSend(failedMsg.content, failedMsg.id)
  }

  const handleRetract = async (msg) => {
    try {
      await fetchWithTimeout(`/api/messages/${msg.id}/retract`, {
        method: 'POST',
        headers: { ...getAuthHeaders() },
      })
      setMessages((prev) => prev.map((m) => m.id === msg.id ? { ...m, retracted: true } : m))
    } catch {}
  }

  const handleCopy = async (msg) => {
    try {
      await navigator.clipboard.writeText(msg.content)
      setCopiedId(msg.id)
      setTimeout(() => setCopiedId((id) => (id === msg.id ? null : id)), 1200)
    } catch {}
  }

  const grantConsentAndResend = async () => {
    if (!consent) return
    try {
      await fetchWithTimeout('/api/messages/consent', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ target_region: consent.target_region }),
      })
    } catch {}
    setConsent(null)
    const pending = messages.find((m) => m._status === 'consent')
    if (pending) {
      setMessages((prev) => prev.map((m) => m.id === pending.id ? { ...m, _status: 'sending' } : m))
      performSend(pending.content, pending.id)
    }
  }

  const declineConsent = () => {
    setConsent(null)
    setMessages((prev) => prev.map((m) => m._status === 'consent' ? { ...m, _status: 'failed' } : m))
  }

  // Normalize messages for ChatHistoryPanel (add role field for speaker filter)
  const normalizedHistoryMessages = useMemo(() =>
    messages.map((m) => ({
      ...m,
      role: m.sender_id === authUser?.id ? 'user' : 'other',
      timestamp: m.created_at,
    })),
    [messages, authUser?.id]
  )

  // ── Online status ──
  const fetchOnlineStatus = useCallback(async () => {
    if (!otherUserId) return
    try {
      const res = await fetchWithTimeout(`/api/auth/user/${otherUserId}/online`)
      const data = await res.json()
      setOtherOnline(data.online)
      setOtherOnlineHidden(data.hidden)
      setOtherLastActive(data.last_active_at || '')
    } catch {
      // ignore
    }
  }, [otherUserId])

  // Initial load + mark read + fetch other avatar / region
  useEffect(() => {
    if (!otherUserId) return
    loadMessages()
    fetchReactions()
    markRead()
    fetchWithTimeout(`/api/market/author/${otherUserId}`)
      .then((r) => r.json())
      .then((data) => {
        if (data.author?.avatar_data) setOtherAvatar(data.author.avatar_data)
        if (data.author?.home_region) setOtherHomeRegion(data.author.home_region)
      })
      .catch(() => {})
    if (authUser?.id) {
      fetchWithTimeout(`/api/market/author/${authUser.id}`)
        .then((r) => r.json())
        .then((data) => { if (data.author?.home_region) setMyHomeRegion(data.author.home_region) })
        .catch(() => {})
    }
    fetchOnlineStatus()
  }, [otherUserId, loadMessages, markRead, fetchOnlineStatus])

  // Poll online status every 30s
  useEffect(() => {
    if (!otherUserId) return
    const timer = setInterval(fetchOnlineStatus, 30000)
    return () => clearInterval(timer)
  }, [otherUserId, fetchOnlineStatus])

  // Inner voice affinity: pull the active character session's affinity fresh on
  // mount instead of depending on store state left by the last role chat.
  // fetchAffinity reads the current sessionId and silently no-ops when none
  // exists. No-data (204) lands as affinity=null — the overlay placeholder then
  // covers it without ever showing fake numbers.
  useEffect(() => {
    if (currentCard && currentCard.user_id === otherUserId) {
      fetchAffinity()
    }
  }, [otherUserId, currentCard?.id, fetchAffinity])

  // Poll for new messages (merge, never clobber optimistic or older pages)
  useEffect(() => {
    if (!otherUserId) return
    const timer = setInterval(() => {
      pollNewMessages()
      fetchReactions()
      markRead()
    }, POLL_INTERVAL)
    return () => clearInterval(timer)
  }, [otherUserId, pollNewMessages, fetchReactions, markRead])

  // Auto-scroll to bottom on new messages
  const { handleScroll: handleAutoScroll } = useAutoScroll(listRef, messagesEndRef, [messages])

  // Keep the viewport pinned when an older page is prepended above.
  useLayoutEffect(() => {
    const anchor = prependAnchor.current
    if (!anchor) return
    const el = listRef.current
    if (!el) return
    const delta = el.scrollHeight - anchor.height
    if (delta > 0) el.scrollTop = anchor.scrollTop + delta
    prependAnchor.current = null
  }, [messages])

  // Network status
  useEffect(() => {
    const goOnline = () => setIsOnline(true)
    const goOffline = () => setIsOnline(false)
    window.addEventListener('online', goOnline)
    window.addEventListener('offline', goOffline)
    return () => {
      window.removeEventListener('online', goOnline)
      window.removeEventListener('offline', goOffline)
    }
  }, [])

  // Auto-send queued messages when back online
  useEffect(() => {
    if (!isOnline) { autoSendRef.current = false; return }
    if (autoSendRef.current) return
    autoSendRef.current = true
    const queued = messages.filter((m) => m._status === 'queued')
    if (queued.length === 0) return
    queued.forEach((failedMsg) => {
      setMessages((prev) => prev.map((m) => m.id === failedMsg.id ? { ...m, _status: 'sending' } : m))
      performSend(failedMsg.content, failedMsg.id)
    })
  }, [isOnline, otherUserId, performSend])

  // Top-triggered pagination (replaces the old "load more" button)
  const handleLoadMore = () => {
    if (loading || !hasMore) return
    const el = listRef.current
    if (el) prependAnchor.current = { height: el.scrollHeight, scrollTop: el.scrollTop }
    loadMessages(page + 1, true)
  }
  const handleScroll = (e) => {
    handleAutoScroll(e)
    const el = listRef.current
    if (el && el.scrollTop < 80) handleLoadMore()
  }

  // Group messages into date headers + sender rows (consecutive same sender, <5min gap)
  // Missing timestamps (optimistic sending) must not force a date break or split the group.
  const rows = useMemo(() => {
    const out = []
    for (let i = 0; i < messages.length; i++) {
      const m = messages[i]
      const prev = messages[i - 1]
      const prevDay = prev ? dayLabel(prev.created_at) : null
      const curDay = dayLabel(m.created_at)
      const needDate = prev ? (prevDay && curDay && prevDay !== curDay) : Boolean(curDay)
      if (needDate) {
        out.push({ type: 'date', key: `d-${m.id}`, label: `${curDay} ${toHM(m.created_at)}` })
      }
      const isMe = m.sender_id === authUser?.id
      const last = out[out.length - 1]
      const tM = parseTS(m.created_at)
      const tP = prev ? parseTS(prev.created_at) : null
      const withinGap = !prev || !tM || !tP || (tM - tP <= GROUP_GAP)
      if (last?.type === 'row' && last.isMe === isMe && prev && prev.sender_id === m.sender_id && withinGap) {
        last.messages.push(m)
      } else {
        out.push({ type: 'row', key: `r-${m.id}`, isMe, messages: [m] })
      }
    }
    return out
  }, [messages, authUser?.id])

  const renderStatus = (msg) => {
    if (msg._status === 'queued') return <span className="dm-msg-status queued"><Wifi size={12} />待发送</span>
    if (msg._status === 'sending') return <span className="dm-msg-status sending"><Clock size={12} />发送中</span>
    if (msg._status === 'failed') return <span className="dm-msg-status failed"><AlertTriangle size={12} />发送失败</span>
    if (msg._status === 'consent') return <span className="dm-msg-status consent"><Shield size={12} />待授权</span>
    if (msg.is_read) return <span className="dm-msg-status read"><CheckDouble size={12} />已读</span>
    return <span className="dm-msg-status sent"><Check size={12} />已发送</span>
  }

  return (
    <div className="chat-view private-chat">
      <SplitOrFullscreen
        open={historyOpen}
        splitRatio={0.65}
        onSplitRatioChange={() => {}}
        main={
          <div className="chat-main-content" style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
            <div className="dm">
              <div className="dm-bg-glow" />
              <header className="dm-header">
                <button type="button" className="dm-back" onClick={() => history.back()} title="返回会话列表">
                  <ArrowLeft size={20} />
                </button>
                <div className="dm-peer">
                  <div className="dm-peer-avatar">
                    <Avatar name={peerName} src={otherAvatar} size={38} />
                  </div>
                  <div className="dm-peer-meta">
                    <div className="dm-peer-name">{peerName}</div>
                    {!otherOnlineHidden && (
                      <div className={`dm-peer-status${otherOnline ? ' online' : ''}`}>
                        <span className="dot" />
                        {otherOnline === null ? '' : otherOnline ? '当前在线' : `最后在线 ${otherLastActive ? formatChatTime(otherLastActive) : '暂无记录'}`}
                      </div>
                    )}
                  </div>
                  {isCrossRegion && <span className="dm-peer-tag">跨区</span>}
                </div>
                <div className="dm-header-actions">
                  {canShowInnerVoice && (
                    <button
                      type="button"
                      className={`dm-icon-btn${innerVoiceOpen ? ' active' : ''}`}
                      onClick={() => setInnerVoiceOpen((v) => !v)}
                      title={`${peerName}此刻的想法`}
                    >
                      <MessageCircle size={19} />
                    </button>
                  )}
                  <button
                    type="button"
                    className={`dm-icon-btn${historyOpen ? ' active' : ''}`}
                    onClick={() => setHistoryOpen((v) => !v)}
                    title="历史记录"
                  >
                    <Clock size={19} />
                  </button>
                </div>
              </header>

              {canShowInnerVoice && innerVoiceOpen && (
                <div className="dm-inner-voice">
                  <div className="dm-inner-voice-header">{affinity?.mood_emoji || '😊'} {currentCard?.name || peerName}此刻的想法</div>
                  <div className="dm-inner-voice-text">{affinity?.inner_voice ? `"${affinity.inner_voice}"` : '还没有产生想法'}</div>
                  {affinity && (
                    <>
                      {!!affinity.mood && (
                        <div className="dm-inner-voice-mood"><Heart size={13} />{affinity.mood}</div>
                      )}
                      <div className="dm-inner-voice-footer">
                        {!!affinity.stage && <span className="dm-inner-voice-stage">{affinity.stage_emoji || ''} {affinity.stage}</span>}
                        <span className="dm-inner-voice-stats">
                          <span><Heart size={11} />{affinity.affinity}</span>
                          <span><Check size={11} />{affinity.trust}</span>
                          <span><Shield size={11} />{affinity.guard}</span>
                        </span>
                      </div>
                    </>
                  )}
                </div>
              )}

              {!isOnline && (
                <div className="dm-offline">网络已断开 · 恢复连接后自动发送</div>
              )}

              {/* Messages */}
              <div className="dm-scroll" ref={listRef} onScroll={handleScroll}>
                {loading && messages.length > 0 && (
                  <div className="dm-loading"><div className="dm-spinner" /></div>
                )}
                {messages.length === 0 && loading && (
                  <div className="dm-loading"><div className="dm-spinner" /></div>
                )}
                {rows.map((row) => {
                  if (row.type === 'date') {
                    return (
                      <div className="dm-date" key={row.key}><span>{row.label}</span></div>
                    )
                  }
                  const first = row.messages[0]
                  return (
                    <div
                      className={`dm-row${row.isMe ? ' mine' : ''}${first._status === 'sending' || first._status === 'queued' ? ' is-new' : ''}`}
                      key={row.key}
                      data-msg-id={first.id}
                    >
                      <div className="dm-avatar">
                        <Avatar
                          name={row.isMe ? (displayName(authUser) || '我') : peerName}
                          src={row.isMe ? userAvatar : otherAvatar}
                          size={30}
                        />
                      </div>
                      <div className="dm-bubbles">
                        {!row.isMe && <div className="dm-name">{peerName}</div>}
                        {row.messages.map((msg) => {
                          const failed = msg._status === 'failed'
                          const rxs = reactions[msg.id] || []
                          return (
                            <React.Fragment key={msg.id}>
                              <div className="dm-bubble-wrap" data-msg-id={msg.id}>
                                {isRetractedMsg(msg) ? (
                                  <div className="dm-retracted"><RefreshCw size={13} />{row.isMe ? '你撤回了一条消息' : `${peerName}撤回了一条消息`}</div>
                                ) : (
                                  <>
                                    {row.isMe && msg.cross_border_synced === 0 && (
                                      <span className="dm-sync-pill"><RefreshCw size={11} />跨区同步中</span>
                                    )}
                                    <div className={`dm-bubble dm-bubble--${row.isMe ? 'r' : 'l'}`}>
                                      <div className="dm-bubble-text">{msg.content}</div>
                                      <div className="dm-bubble-meta">
                                        {row.isMe && renderStatus(msg)}
                                        <span>{toHM(msg.created_at)}</span>
                                      </div>
                                    </div>
                                    {row.isMe && msg._status !== 'consent' && (
                                      <div className="dm-bubble-actions">
                                        <button type="button" className="dm-mini-btn" onClick={() => setEmojiPickerId(emojiPickerId === msg.id ? null : msg.id)}>😊</button>
                                        <button type="button" className="dm-mini-btn" onClick={() => handleCopy(msg)}>{copiedId === msg.id ? '已复制' : '复制'}</button>
                                        <button type="button" className="dm-mini-btn danger" onClick={() => handleRetract(msg)}>撤回</button>
                                      </div>
                                    )}
                                  </>
                                )}
                                {emojiPickerId === msg.id && (
                                  <div className="dm-emoji-picker" data-side={row.isMe ? 'right' : 'left'}>
                                    {QUICK_EMOJIS.map((e) => (
                                      <button key={e} type="button" className="dm-emoji-pick"
                                        onClick={() => { handleReact(msg.id, e); setEmojiPickerId(null) }}>
                                        {e}
                                      </button>
                                    ))}
                                  </div>
                                )}
                                {rxs.length > 0 && (
                                  <div className="dm-reactions">
                                    {rxs.map((r) => (
                                      <button key={r.emoji} type="button"
                                        className={`dm-reaction${r.users?.includes(authUser?.id) ? ' mine' : ''}`}
                                        onClick={() => handleReact(msg.id, r.emoji)}>
                                        {r.emoji}<span className="cnt">{r.count}</span>
                                      </button>
                                    ))}
                                  </div>
                                )}
                              </div>
                              {failed && (
                                <button type="button" className="dm-resend" onClick={() => handleResend(msg)}>
                                  <RefreshCw size={11} />点击重发
                                </button>
                              )}
                            </React.Fragment>
                          )
                        })}
                      </div>
                    </div>
                  )
                })}
                <div ref={messagesEndRef} />
              </div>

              {messages.length === 0 && !loading && (
                <div className="dm-empty">
                  <div className="dm-empty-mark"><MessageSquare size={26} /></div>
                  <div className="dm-empty-title">打个招呼，开启你们的对话</div>
                  <div className="dm-empty-sub">发送第一条消息，开始和 {peerName} 交流</div>
                </div>
              )}

              {/* Input */}
              <div className="dm-composer">
                <ChatInputBar
                  value={inputText}
                  onChange={setInputText}
                  onSend={handleSend}
                  placeholder="输入消息…"
                  variant="dm"
                />
              </div>
            </div>
          </div>
        }
        panel={
          <ChatHistoryPanel
            messages={normalizedHistoryMessages}
            speakers={[{ key: 'other', label: peerName }, { key: 'me', label: '我' }]}
            onClose={() => setHistoryOpen(false)}
            resolveSpeaker={(msg) => {
              const isMe = msg.sender_id === authUser?.id
              return { name: isMe ? (displayName(authUser) || '我') : peerName, src: isMe ? userAvatar : otherAvatar }
            }}
          />
        }
      />

      {consent && (
        <div className="modal-overlay" onClick={declineConsent}>
          <div className="modal-card" style={{ maxWidth: 420 }} onClick={(e) => e.stopPropagation()}>
            <h3 className="modal-title" style={{ fontSize: 16 }}>跨区私信确认</h3>
            <div className="modal-body" style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.7, marginTop: 8 }}>
              对方在 {consent.target_region} 区。跨区私信会把这条消息同步到对方所在区域的节点，是否发送？
            </div>
            <div className="modal-actions" style={{ marginTop: 16 }}>
              <button type="button" className="btn-ghost" onClick={declineConsent}>取消</button>
              <button type="button" className="btn-primary" onClick={grantConsentAndResend}>同意并发送</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
