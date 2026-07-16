import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useAutoScroll } from '../hooks/useAutoScroll'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout, getAuthHeaders } from '../api/client'
import { formatChatTime } from '../utils/time'
import SplitOrFullscreen from './common/SplitOrFullscreen'
import ChatHistoryPanel from './common/ChatHistoryPanel'
import Avatar from './common/Avatar'
import ChatBubble from './common/ChatBubble'
import { displayName } from '../utils/displayName'
import MessageReactions from './common/MessageReactions'
import ChatInputBar from './common/ChatInputBar'
import useIsMobile from '../hooks/useIsMobile'
const POLL_INTERVAL = 5000
const PAGE_SIZE = 30

export default function PrivateMessageChat({ otherUserId, otherUsername }) {
  const authUser = useAppStore((s) => s.authUser)
  const userAvatar = useAppStore((s) => s.userAvatar)
  const refreshUnread = useAppStore((s) => s.refreshUnread)

  const [messages, setMessages] = useState([])
  const [loading, setLoading] = useState(true)
  const [inputText, setInputText] = useState('')
  const [page, setPage] = useState(1)
  const [hasMore, setHasMore] = useState(true)
  const [isOnline, setIsOnline] = useState(navigator.onLine)

  const listRef = useRef(null)
  const messagesEndRef = useRef(null)
  const autoSendRef = useRef(false)
  const [otherAvatar, setOtherAvatar] = useState(null)
  const [otherOnline, setOtherOnline] = useState(null) // null=loading, true, false
  const [otherOnlineHidden, setOtherOnlineHidden] = useState(false)
  const [otherLastActive, setOtherLastActive] = useState('')
  const [reactions, setReactions] = useState({})

  const [historyOpen, setHistoryOpen] = useState(false)
  const isMobile = useIsMobile()

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

  // Fetch reactions for the current conversation
  const fetchReactions = useCallback(async () => {
    if (!otherUserId) return
    try {
      const res = await fetchWithTimeout(`/api/messages/with/${otherUserId}/reactions`)
      const d = await res.json()
      setReactions(d.reactions || {})
    } catch {}
  }, [otherUserId])

  // React to a DM
  const handleReact = useCallback(async (messageId, emoji) => {
    try {
      await fetchWithTimeout(`/api/messages/${messageId}/react`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ emoji }),
      })
      const res = await fetchWithTimeout(`/api/messages/with/${otherUserId}/reactions`)
      const d = await res.json()
      setReactions(d.reactions || {})
    } catch {}
  }, [otherUserId])

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
  }, [otherUserId])

  // Normalize messages for ChatHistoryPanel (add role field for speaker filter)
  const normalizedHistoryMessages = useMemo(() =>
    messages.map(m => ({
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

  // Initial load + mark read + fetch other avatar
  useEffect(() => {
    if (!otherUserId) return
    loadMessages()
    fetchReactions()
    markRead()
    fetchWithTimeout(`/api/market/author/${otherUserId}`)
      .then(r => r.json())
      .then(data => {
        if (data.author?.avatar_data) setOtherAvatar(data.author.avatar_data)
      })
      .catch(() => {})
    fetchOnlineStatus()
  }, [otherUserId, loadMessages, markRead, fetchOnlineStatus])

  // Poll online status every 30s
  useEffect(() => {
    if (!otherUserId) return
    const timer = setInterval(fetchOnlineStatus, 30000)
    return () => clearInterval(timer)
  }, [otherUserId, fetchOnlineStatus])

  // Poll for new messages
  useEffect(() => {
    if (!otherUserId) return
    const timer = setInterval(() => {
      loadMessages(1)
      fetchReactions()
      markRead()
    }, POLL_INTERVAL)
    return () => clearInterval(timer)
  }, [otherUserId, loadMessages, fetchReactions, markRead])

  // Auto-scroll to bottom on new messages
  const { handleScroll } = useAutoScroll(listRef, messagesEndRef, [messages])

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
    const queued = messages.filter(m => m._status === 'queued')
    if (queued.length === 0) return
    queued.forEach(async (failedMsg) => {
      setMessages(prev => prev.map(m => m.id === failedMsg.id ? { ...m, _status: 'sending' } : m))
      try {
        const res = await fetchWithTimeout('/api/messages/send', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
          body: JSON.stringify({ receiver_id: otherUserId, content: failedMsg.content }),
        })
        const data = await res.json()
        if (data.message) {
          setMessages(prev => prev.map(m => m.id === failedMsg.id ? { ...data.message, _status: 'sent' } : m))
        } else {
          setMessages(prev => prev.map(m => m.id === failedMsg.id ? { ...m, _status: 'failed' } : m))
        }
      } catch {
        setMessages(prev => prev.map(m => m.id === failedMsg.id ? { ...m, _status: 'failed' } : m))
      }
    })
  }, [isOnline, otherUserId])

  const handleSend = async (textArg) => {
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
    setMessages(prev => [...prev, optimisticMsg])

    if (!isOnline) return

    try {
      const res = await fetchWithTimeout('/api/messages/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ receiver_id: otherUserId, content: optimisticMsg.content }),
      })
      const data = await res.json()
      if (data.message) {
        setMessages(prev => prev.map(m => m.id === tempId ? { ...data.message, _status: 'sent' } : m))
      } else {
        setMessages(prev => prev.map(m => m.id === tempId ? { ...m, _status: 'failed' } : m))
      }
    } catch {
      setMessages(prev => prev.map(m => m.id === tempId ? { ...m, _status: 'failed' } : m))
    }
  }

  const handleResend = async (failedMsg) => {
    setMessages(prev => prev.map(m => m.id === failedMsg.id ? { ...m, _status: 'sending' } : m))
    try {
      const res = await fetchWithTimeout('/api/messages/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ receiver_id: otherUserId, content: failedMsg.content }),
      })
      const data = await res.json()
      if (data.message) {
        setMessages(prev => prev.map(m => m.id === failedMsg.id ? { ...data.message, _status: 'sent' } : m))
      } else {
        setMessages(prev => prev.map(m => m.id === failedMsg.id ? { ...m, _status: 'failed' } : m))
      }
    } catch {
      setMessages(prev => prev.map(m => m.id === failedMsg.id ? { ...m, _status: 'failed' } : m))
    }
  }

  const handleLoadMore = () => {
    if (!loading && hasMore) {
      loadMessages(page + 1, true)
    }
  }

  return (
    <div className="chat-view private-chat">
      <SplitOrFullscreen
        open={historyOpen}
        splitRatio={0.65}
        onSplitRatioChange={() => {}}
        main={
          <div className="chat-main-content" style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
          {/* Header */}
          <div className="private-chat-header">
        <div className="private-chat-header-left">
          <Avatar name={otherUsername || '?'} src={otherAvatar} size={40} />
          <div className="private-chat-header-info">
            <span className="private-chat-title">{otherUsername || '私信'}</span>
            {!otherOnlineHidden && (
              <span className={`private-chat-header-status${otherOnline ? ' online' : ''}`}>
                <span className="private-chat-header-status-dot" />
                {otherOnline === null ? '' : otherOnline ? '当前在线' : `最后在线 ${otherLastActive ? formatChatTime(otherLastActive) : '暂无记录'}`}
              </span>
            )}
          </div>
        </div>
        <div className="private-chat-header-right">
          <button
            type="button"
            className={`chat-topbar-btn${historyOpen ? ' active' : ''}`}
            onClick={() => setHistoryOpen(v => !v)}
            title="历史记录"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" />
              <polyline points="12 6 12 12 16 14" />
            </svg>
          </button>
        </div>
      </div>

      {!isOnline && (
        <div className="messages-offline-banner">
          网络已断开，消息将在恢复连接后自动发送
        </div>
      )}

          {/* Messages */}
          <div className="private-chat-body" ref={listRef} onScroll={handleScroll}>
            {hasMore && (
              <div className="messages-load-more">
                <button type="button" className="btn-ghost fs-12" onClick={handleLoadMore} disabled={loading}>
                  {loading ? '加载中…' : '加载更多'}
                </button>
              </div>
            )}
            {messages.map((msg, i) => {
              const prev = messages[i - 1]
              const showTime = !prev ||
                (new Date(msg.created_at) - new Date(prev.created_at)) > 5 * 60 * 1000
              const isMe = msg.sender_id === authUser?.id
              return (
                <React.Fragment key={msg.id}>
                  {showTime && (
                    <div className="messages-time-divider">{formatChatTime(msg.created_at)}</div>
                  )}
                  <div className={`messages-row${isMe ? ' mine' : ' other'}`} data-msg-id={msg.id}>
                    <ChatBubble
                      side={isMe ? 'right' : 'left'}
                      avatar={
                        <Avatar
                          name={isMe ? (displayName(authUser) || '?') : (otherUsername || '?')}
                          src={isMe ? userAvatar : otherAvatar}
                          size={isMobile ? 57 : 52}
                        />
                      }
                      time={msg.created_at ? formatChatTime(msg.created_at) : undefined}
                      status={isMe && msg._status ? (
                        msg._status === 'failed' ? (
                          <button type="button" className="messages-status failed"
                            onClick={() => handleResend(msg)} title="发送失败，点击重试">⚠</button>
                        ) : msg._status === 'sending' ? (
                          <span className="messages-status sending" title="发送中">⏳</span>
                        ) : (
                          <span className="messages-status queued" title="等待网络恢复">📶</span>
                        )
                      ) : undefined}
                    >
                      <span className="messages-msg-text">{msg.content}</span>
                      <MessageReactions
                        side={isMe ? 'right' : 'left'}
                        reactions={reactions[msg.id] || []}
                        showQuickBar={true}
                        onReact={(emoji) => handleReact(msg.id, emoji)}
                        authUserId={authUser?.id}
                      />
                    </ChatBubble>
                  </div>
                </React.Fragment>
              )
            })}
            <div ref={messagesEndRef} />
          </div>

          {/* Input */}
          <ChatInputBar
            value={inputText}
            onChange={setInputText}
            onSend={handleSend}
            placeholder="输入消息…"
          />
        </div>
      }
      panel={
        <ChatHistoryPanel
          messages={normalizedHistoryMessages}
          speakers={[{key:'other', label: otherUsername || '对方'}, {key:'me', label:'我'}]}
          onClose={() => setHistoryOpen(false)}
          resolveSpeaker={(msg) => {
            const isMe = msg.sender_id === authUser?.id
            return { name: isMe ? (displayName(authUser) || '我') : (otherUsername || '对方'), src: isMe ? userAvatar : otherAvatar }
          }}
        />
      }
    />
    </div>
  )
}
