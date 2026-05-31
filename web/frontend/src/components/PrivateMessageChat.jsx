import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout, getAuthHeaders } from '../api/client'
import { formatChatTime } from '../utils/time'
import { Calendar } from './common/ChatHistoryPanel'
import Avatar from './common/Avatar'
import EmojiPicker from './common/EmojiPicker'
const POLL_INTERVAL = 5000
const PAGE_SIZE = 30

export default function PrivateMessageChat({ otherUserId, otherUsername }) {
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

  // ── 历史面板（平铺当前对话消息） ──
  const [historyFilterDate, setHistoryFilterDate] = useState('')
  const [historySearchKeyword, setHistorySearchKeyword] = useState('')
  const [historyTab, setHistoryTab] = useState('history')
  const [historyFilterSpeaker, setHistoryFilterSpeaker] = useState('all')

  const filteredHistoryMessages = useMemo(() => {
    let result = messages
    if (historyFilterDate) {
      result = result.filter(m => {
        const d = m.created_at ? new Date(m.created_at).toISOString().slice(0, 10) : ''
        return d === historyFilterDate
      })
    }
    if (historySearchKeyword) {
      const q = historySearchKeyword.toLowerCase()
      result = result.filter(m => (m.content || '').toLowerCase().includes(q))
    }
    if (historyFilterSpeaker === 'other') {
      result = result.filter(m => m.sender_id !== authUser?.id)
    } else if (historyFilterSpeaker === 'me') {
      result = result.filter(m => m.sender_id === authUser?.id)
    }
    return result
  }, [messages, historyFilterDate, historySearchKeyword, historyFilterSpeaker, authUser?.id])

  const historyDateGroups = useMemo(() => {
    const dates = new Set()
    for (const m of messages) {
      if (m.created_at) {
        try { dates.add(new Date(m.created_at).toISOString().slice(0, 10)) } catch {}
      }
    }
    return [...dates].sort().reverse()
  }, [messages])

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
      <div className="chat-with-history" ref={splitContainerRef} style={{ flex: 1, minHeight: 0 }}>
        <div className="chat-main-content" style={historyOpen ? { flex: splitRatio, minWidth: 0, display: 'flex', flexDirection: 'column' } : { flex: 1, display: 'flex', flexDirection: 'column' }}>
          {/* Header */}
          <div className="private-chat-header">
        <div className="private-chat-header-left">
          <Avatar name={otherUsername || '?'} src={otherAvatar} size={40} />
          <div className="private-chat-header-info">
            <span className="private-chat-title">{otherUsername || '私信'}</span>
            {!otherOnlineHidden && (
              <span className={`private-chat-header-status${otherOnline ? ' online' : ''}`}>
                <span className="private-chat-header-status-dot" />
                {otherOnline === null ? '' : otherOnline ? '当前在线' : `最后在线 ${formatChatTime(otherLastActive)}`}
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
            <div className="history-sidebar" style={{ flex: 1 - splitRatio, minWidth: 280, maxWidth: '50vw' }}>
              <div className="history-sidebar-content">
                <div className="history-sidebar-header">
                  <div className="chat-history-search-bar" style={{ flex: 1 }}>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
                      <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
                    </svg>
                    <input type="text" className="chat-history-search-input" placeholder="搜索消息…"
                      value={historySearchKeyword}
                      onChange={(e) => setHistorySearchKeyword(e.target.value)} />
                  </div>
                  <button type="button" className="history-sidebar-close" onClick={() => setHistoryOpen(false)}>
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                  </button>
                </div>

                <div className="history-date-tabs">
                  <button type="button" className={`history-date-tab${historyTab === 'history' ? ' active' : ''}`}
                    onClick={() => setHistoryTab('history')}>历史</button>
                  <button type="button" className={`history-date-tab${historyTab === 'date' ? ' active' : ''}`}
                    onClick={() => setHistoryTab('date')}>日期</button>
                </div>

                <div className="history-speaker-tabs">
                  <button type="button" className={`history-speaker-tab${historyFilterSpeaker === 'all' ? ' active' : ''}`}
                    onClick={() => setHistoryFilterSpeaker('all')}>全部</button>
                  <button type="button" className={`history-speaker-tab${historyFilterSpeaker === 'other' ? ' active' : ''}`}
                    onClick={() => setHistoryFilterSpeaker('other')}>{otherUsername || '对方'}</button>
                  <button type="button" className={`history-speaker-tab${historyFilterSpeaker === 'me' ? ' active' : ''}`}
                    onClick={() => setHistoryFilterSpeaker('me')}>我</button>
                </div>

                {historyTab === 'date' ? (
                  <div className="history-sidebar-body">
                    <Calendar dateGroups={historyDateGroups} selectedDate={historyFilterDate}
                      onSelectDate={(iso) => { setHistoryFilterDate(iso || ''); if (iso) setHistoryTab('history') }} />
                  </div>
                ) : (
                  <div className="history-sidebar-body">
                    {(historyFilterDate || historyFilterSpeaker !== 'all') && (
                      <div className="group-history-filter-bar">
                        <span className="group-history-filter-label">筛选：</span>
                        {historyFilterDate && (
                          <span className="group-history-filter-chip">
                            {historyFilterDate}
                            <button type="button" className="group-history-filter-chip-x" onClick={() => setHistoryFilterDate('')}>
                              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                            </button>
                          </span>
                        )}
                        {historyFilterSpeaker !== 'all' && (
                          <span className="group-history-filter-chip">
                            {historyFilterSpeaker === 'other' ? (otherUsername || '对方') : '我'}
                            <button type="button" className="group-history-filter-chip-x" onClick={() => setHistoryFilterSpeaker('all')}>
                              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                            </button>
                          </span>
                        )}
                      </div>
                    )}
                    {filteredHistoryMessages.length === 0 ? (
                      <div className="group-history-empty">暂无消息</div>
                    ) : (
                      <div className="group-history-list">
                        {filteredHistoryMessages.map((m, i) => {
                          const isMe = m.sender_id === authUser?.id
                          const speakerName = isMe ? (authUser?.username || '我') : (otherUsername || '对方')
                          return (
                            <div key={m.id || i} className="group-history-item">
                              <Avatar name={speakerName} size={28}
                                src={isMe ? userAvatar : otherAvatar} />
                              <div className="group-history-item-body">
                                <div className="group-history-item-head">
                                  <span className="group-history-item-speaker">{speakerName}</span>
                                  <span className="group-history-item-time">{m.created_at ? formatChatTime(m.created_at) : ''}</span>
                                </div>
                                <p className="group-history-item-text">{m.content}</p>
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
