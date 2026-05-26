import React, { useCallback, useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout, getAuthHeaders } from '../api/client'
import Avatar from './common/Avatar'
const POLL_INTERVAL = 30000
const PAGE_SIZE = 30

function formatTime(dateStr) {
  if (!dateStr) return ''
  const d = new Date(dateStr)
  if (isNaN(d.getTime())) return ''
  const now = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  const hhmm = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  if (d.toDateString() === now.toDateString()) return hhmm
  const mmdd = `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
  if (d.getFullYear() === now.getFullYear()) return `${mmdd} ${hhmm}`
  return `${d.getFullYear()}-${mmdd} ${hhmm}`
}

export default function PrivateMessageChat({ otherUserId, otherUsername, onBack }) {
  const authUser = useAppStore((s) => s.authUser)
  const userAvatar = useAppStore((s) => s.userAvatar)

  const [messages, setMessages] = useState([])
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [inputText, setInputText] = useState('')
  const [page, setPage] = useState(1)
  const [hasMore, setHasMore] = useState(true)
  const [isOnline, setIsOnline] = useState(navigator.onLine)

  const messagesEndRef = useRef(null)

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

  // Initial load + mark read
  useEffect(() => {
    if (!otherUserId) return
    loadMessages()
    markRead()
  }, [otherUserId, loadMessages, markRead])

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
    if (!isOnline) return
    const queued = messages.filter(m => m._status === 'queued')
    queued.forEach(msg => handleResend(msg))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOnline])

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

    setSending(true)
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
    } finally {
      setSending(false)
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
        <button type="button" className="chat-back-btn" onClick={onBack} title="返回">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5m7-7-7 7 7 7"/></svg>
          返回
        </button>
        <Avatar name={otherUsername || '?'} size={32} />
        <span className="private-chat-title">{otherUsername || '私信'}</span>
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
                <div className="messages-time-divider">{formatTime(msg.created_at)}</div>
              )}
              <div className={`messages-row${isMe ? ' mine' : ' other'}`}>
                {!isMe && (
                  <Avatar name={otherUsername || '?'} size={36} />
                )}
                <div className={`messages-bubble${isMe ? ' mine' : ' other'}`}>
                  <span className="messages-msg-text">{msg.content}</span>
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
                {isMe && <Avatar name={authUser?.username || '?'} src={userAvatar} size={36} />}
              </div>
            </React.Fragment>
          )
        })}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="private-chat-input-bar">
        <div className="messages-input-toolbar messages-input-toolbar-top">
          <button type="button" className="messages-toolbar-btn" title="表情"
            onClick={() => {/* 预留表情选择器 */}}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10"/>
              <path d="M8 14s1.5 2 4 2 4-2 4-2"/>
              <line x1="9" y1="9" x2="9.01" y2="9"/>
              <line x1="15" y1="9" x2="15.01" y2="9"/>
            </svg>
          </button>
        </div>
        <textarea
          className="messages-input"
          rows={3}
          placeholder="输入消息…"
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          onKeyDown={handleKeyDown}
        />
        <div className="messages-input-toolbar messages-input-toolbar-bottom">
          <div className="messages-input-toolbar-left" />
          <button
            type="button"
            className="messages-send-btn"
            disabled={!inputText.trim() || sending}
            onClick={handleSend}
          >
            {sending ? '…' : '发送'}
          </button>
        </div>
      </div>
    </div>
  )
}
