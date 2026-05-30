import React, { useCallback, useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout, getAuthHeaders } from '../api/client'
import { formatChatTime } from '../utils/time'
import ChatHistoryPanel from './common/ChatHistoryPanel'
import Avatar from './common/Avatar'
import EmojiPicker from './common/EmojiPicker'
const POLL_INTERVAL = 5000
const PAGE_SIZE = 30

export default function PrivateMessageChat({ otherUserId, otherUsername, onBack }) {
  const authUser = useAppStore((s) => s.authUser)
  const userAvatar = useAppStore((s) => s.userAvatar)

  const [messages, setMessages] = useState([])
  const [loading, setLoading] = useState(true)
  const [inputText, setInputText] = useState('')
  const [page, setPage] = useState(1)
  const [hasMore, setHasMore] = useState(true)
  const [isOnline, setIsOnline] = useState(navigator.onLine)

  const messagesEndRef = useRef(null)
  const autoSendRef = useRef(false)
  const taRef = useRef(null)
  const [showEmoji, setShowEmoji] = useState(false)
  const [otherAvatar, setOtherAvatar] = useState(null)
  const [otherOnline, setOtherOnline] = useState(null) // null=loading, true, false
  const [otherOnlineHidden, setOtherOnlineHidden] = useState(false)
  const [otherLastActive, setOtherLastActive] = useState('')

  // ── Sidebar history splitter ──
  const [historyOpen, setHistoryOpen] = useState(false)
  const [splitRatio, setSplitRatio] = useState(0.65)
  const splitContainerRef = useRef(null)

  const onSplitterMouseDown = useCallback((e) => {
    e.preventDefault()
    const container = splitContainerRef.current
    if (!container) return
    const rect = container.getBoundingClientRect()
    const onMove = (moveE) => {
      const ratio = (moveE.clientX - rect.left) / rect.width
      setSplitRatio(Math.min(0.8, Math.max(0.4, ratio)))
    }
    const onUp = () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [])

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

  // Mark messages as read
  const markRead = useCallback(async () => {
    if (!otherUserId) return
    try {
      await fetchWithTimeout(`/api/messages/read/${otherUserId}`, {
        method: 'POST',
        headers: { ...getAuthHeaders() },
      })
    } catch {
      // ignore
    }
  }, [otherUserId])

  // ── 历史搜索（客户端搜索已加载消息） ──
  const historyFetchSessions = useCallback(async (keyword) => {
    const msgs = messages
    if (!keyword) return []
    const q = keyword.toLowerCase()
    const matching = msgs.filter((m) => m.content?.toLowerCase().includes(q)).slice(0, 20)
    return matching.map((m) => ({
      id: m.id,
      title: m.sender_id === authUser?.id ? '我' : (otherUsername || '对方'),
      preview: m.content?.slice(0, 60),
      time: m.created_at,
    }))
  }, [messages, authUser?.id, otherUsername])

  const historySelectSession = useCallback((session) => {
    // Scroll to the message (find and scroll into view)
    setTimeout(() => {
      const el = document.querySelector(`[data-msg-id="${session.id}"]`)
      el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }, 100)
  }, [])

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
      markRead()
    }, POLL_INTERVAL)
    return () => clearInterval(timer)
  }, [otherUserId, loadMessages, markRead])

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length])

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

  // Emoji picker outside-click
  useEffect(() => {
    if (!showEmoji) return
    const handler = (e) => {
      if (!e.target.closest('.emoji-picker') && !e.target.closest('[data-emoji-btn]')) {
        setShowEmoji(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showEmoji])

  const handleSend = async () => {
    if (!inputText.trim() || !otherUserId) return
    const tempId = `temp-${Date.now()}`
    const optimisticMsg = {
      id: tempId,
      sender_id: authUser?.id,
      content: inputText.trim(),
      created_at: new Date().toISOString(),
      _status: isOnline ? 'sending' : 'queued',
    }
    setMessages(prev => [...prev, optimisticMsg])
    setInputText('')

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

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleLoadMore = () => {
    if (!loading && hasMore) {
      loadMessages(page + 1, true)
    }
  }

  return (
    <div className="private-chat">
      {/* Header */}
      <div className="private-chat-header">
        <button type="button" className="chat-back-btn" onClick={onBack} style={{ gap: 4 }}>
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5m7-7-7 7 7 7"/></svg>
          返回
        </button>
        <Avatar name={otherUsername || '?'} src={otherAvatar} size={32} />
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span className="private-chat-title">{otherUsername || '私信'}</span>
          {!otherOnlineHidden && (
            <span style={{ fontSize: 12, color: otherOnline ? '#22c55e' : 'var(--text-dim)', display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: otherOnline ? '#22c55e' : 'var(--text-dim)', display: 'inline-block', flexShrink: 0 }} />
              {otherOnline === null ? '' : otherOnline ? '在线' : formatChatTime(otherLastActive)}
            </span>
          )}
        </div>
        <button
          type="button"
          className={`chat-history-toggle${historyOpen ? ' active' : ''}`}
          onClick={() => setHistoryOpen(v => !v)}
          title="历史记录"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <polyline points="12 6 12 12 16 14" />
          </svg>
          历史
        </button>
      </div>

      {!isOnline && (
        <div className="messages-offline-banner">
          网络已断开，消息将在恢复连接后自动发送
        </div>
      )}

      <div className="chat-with-history" ref={splitContainerRef} style={{ flex: 1, minHeight: 0 }}>
        <div className="chat-main-content" style={historyOpen ? { flex: splitRatio, minWidth: 0, display: 'flex', flexDirection: 'column' } : { flex: 1, display: 'flex', flexDirection: 'column' }}>
          {/* Messages */}
          <div className="private-chat-body">
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
                    {!isMe && (
                      <Avatar name={otherUsername || '?'} src={otherAvatar} size={52} />
                    )}
                    <div className={`messages-bubble${isMe ? ' mine' : ' other'}`}>
                      <span className="messages-msg-text">{msg.content}</span>
                      {msg.created_at && (
                        <div className={`msg-time ${isMe ? 'msg-time-user' : ''}`}>{formatChatTime(msg.created_at)}</div>
                      )}
                    </div>
                    {isMe && msg._status === 'queued' && (
                      <span className="messages-status queued" title="等待网络恢复">📶</span>
                    )}
                    {isMe && msg._status === 'sending' && (
                      <span className="messages-status sending" title="发送中">⏳</span>
                    )}
                    {isMe && msg._status === 'failed' && (
                      <button
                        type="button"
                        className="messages-status failed"
                        onClick={() => handleResend(msg)}
                        title="发送失败，点击重试"
                      >
                        ⚠
                      </button>
                    )}
                    {isMe && <Avatar name={authUser?.username || '?'} src={userAvatar} size={52} />}
                  </div>
                </React.Fragment>
              )
            })}
            <div ref={messagesEndRef} />
          </div>

          {/* Input */}
          <div className="private-chat-input-bar">
        <div className="messages-input-toolbar messages-input-toolbar-top">
          <button type="button" className="messages-toolbar-btn" title="表情" data-emoji-btn
            onClick={() => setShowEmoji(!showEmoji)}>
            <svg width="30" height="30" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10"/>
              <path d="M8 14s1.5 2 4 2 4-2 4-2"/>
              <line x1="9" y1="9" x2="9.01" y2="9"/>
              <line x1="15" y1="9" x2="15.01" y2="9"/>
            </svg>
          </button>
        </div>
        <div style={{ position: 'relative' }}>
          {showEmoji && <EmojiPicker textareaRef={taRef} onEmojiSelect={() => setShowEmoji(false)} />}
          <textarea
            ref={taRef}
            className="messages-input"
            rows={3}
            placeholder="输入消息…"
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={handleKeyDown}
          />
        </div>
        <div className="messages-input-toolbar messages-input-toolbar-bottom">
          <div className="messages-input-toolbar-left" />
          <button
            type="button"
            className="messages-send-btn"
            disabled={!inputText.trim()}
            onClick={handleSend}
          >
            发送
          </button>
        </div>
      </div>
        </div>

        {historyOpen && (
          <>
            <div className="chat-splitter" onMouseDown={onSplitterMouseDown} />
            <div className="history-sidebar">
              <ChatHistoryPanel
                mode="sidebar"
                open={historyOpen}
                onClose={() => setHistoryOpen(false)}
                fetchSessions={historyFetchSessions}
                onSelectSession={historySelectSession}
                placeholder="搜索当前对话…"
              />
            </div>
          </>
        )}
      </div>
    </div>
  )
}
