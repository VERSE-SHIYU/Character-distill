import { useCallback, useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout, getAuthHeaders } from '../api/client'
import Avatar from './common/Avatar'
import Loading from './common/Loading'

const POLL_INTERVAL = 30000

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

export default function MessagesPage() {
  const setView = useAppStore((s) => s.setView)
  const authUser = useAppStore((s) => s.authUser)
  const messageTargetUserId = useAppStore((s) => s.messageTargetUserId)
  const setMessageTargetUserId = useAppStore((s) => s.setMessageTargetUserId)
  const messageTargetUsername = useAppStore((s) => s.messageTargetUsername)
  const setMessageTargetUsername = useAppStore((s) => s.setMessageTargetUsername)

  const [conversations, setConversations] = useState([])
  const [convLoading, setConvLoading] = useState(true)
  const [activeOtherId, setActiveOtherId] = useState(null)
  const [activeUsername, setActiveUsername] = useState('')
  const [messages, setMessages] = useState([])
  const [msgLoading, setMsgLoading] = useState(false)
  const [inputText, setInputText] = useState('')
  const [sending, setSending] = useState(false)
  const [page, setPage] = useState(1)
  const [hasMore, setHasMore] = useState(true)
  const PAGE_SIZE = 30
  const messagesEndRef = useRef(null)
  const [mobileView, setMobileView] = useState('list')
  const [isMobile, setIsMobile] = useState(window.innerWidth <= 768)

  // Load conversations
  const loadConversations = useCallback(async () => {
    try {
      const res = await fetchWithTimeout('/api/messages/conversations')
      const data = await res.json()
      setConversations(data.conversations || [])
      if (activeOtherId) {
        const conv = (data.conversations || []).find((c) => c.other_id === activeOtherId)
        if (conv) setActiveUsername(conv.username)
      }
    } catch {
      // ignore
    } finally {
      setConvLoading(false)
    }
  }, [activeOtherId])

  // Load messages for a conversation
  const loadMessages = useCallback(async (otherId, pageNum = 1, append = false) => {
    if (!otherId) return
    setMsgLoading(true)
    try {
      const res = await fetchWithTimeout(`/api/messages/with/${otherId}?page=${pageNum}&page_size=${PAGE_SIZE}`)
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
      setMsgLoading(false)
    }
  }, [])

  // Mark messages as read
  const markRead = useCallback(async (otherId) => {
    if (!otherId) return
    try {
      await fetchWithTimeout(`/api/messages/read/${otherId}`, {
        method: 'POST',
        headers: { ...getAuthHeaders() },
      })
    } catch {
      // ignore
    }
  }, [])

  // Initial load
  useEffect(() => {
    loadConversations()
  }, [])

  // Track mobile vs desktop
  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth <= 768)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // Handle messageTargetUserId from other pages
  useEffect(() => {
    if (messageTargetUserId) {
      setActiveOtherId(messageTargetUserId)
      if (messageTargetUsername) setActiveUsername(messageTargetUsername)
      loadMessages(messageTargetUserId)
      markRead(messageTargetUserId)
      setMobileView('chat')
      setMessageTargetUserId(null)
      setMessageTargetUsername(null)
    }
  }, [messageTargetUserId])

  // When activeOtherId changes, load its messages and mark read
  useEffect(() => {
    if (activeOtherId) {
      loadMessages(activeOtherId)
      markRead(activeOtherId)
      const conv = conversations.find((c) => c.other_id === activeOtherId)
      if (conv) setActiveUsername(conv.username)
      loadConversations()
    }
  }, [activeOtherId])

  // Polling for active conversation
  useEffect(() => {
    if (!activeOtherId) return
    const timer = setInterval(() => {
      loadMessages(activeOtherId, 1)
      loadConversations()
      markRead(activeOtherId)
    }, POLL_INTERVAL)
    return () => clearInterval(timer)
  }, [activeOtherId, loadMessages, loadConversations, markRead])

  // Scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length])

  const handleSend = async () => {
    if (!inputText.trim() || sending || !activeOtherId) return
    setSending(true)
    try {
      const res = await fetchWithTimeout('/api/messages/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ receiver_id: activeOtherId, content: inputText.trim() }),
      })
      const data = await res.json()
      if (data.message) {
        setMessages((prev) => [...prev, data.message])
        setInputText('')
        loadConversations()
      }
    } catch (err) {
      console.error('Send message failed:', err)
    } finally {
      setSending(false)
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleSelectConversation = (otherId, username) => {
    setActiveOtherId(otherId)
    setActiveUsername(username)
    setMobileView('chat')
    loadMessages(otherId)
    markRead(otherId)
  }

  const handleLoadMore = () => {
    if (!msgLoading && hasMore && activeOtherId) {
      loadMessages(activeOtherId, page + 1, true)
    }
  }

  // ── Render ──
  return (
    <div className="panel messages-page">
      <header className="panel-header">
        {isMobile && mobileView === 'chat' ? (
          <>
            <button
              type="button"
              className="chat-back-btn"
              onClick={() => { setMobileView('list') }}
              title="返回列表"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5m7-7-7 7 7 7"/></svg>
              返回
            </button>
            <h1 className="panel-title" style={{ fontSize: 15 }}>{activeUsername || '私信'}</h1>
          </>
        ) : (
          <>
            <button type="button" className="chat-back-btn" onClick={() => setView('mine')} title="返回">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5m7-7-7 7 7 7"/></svg>
              返回
            </button>
            <h1 className="panel-title">私信</h1>
          </>
        )}
      </header>

      {!convLoading && conversations.length === 0 && !activeOtherId ? (
        /* ── Empty state: no conversations and no target user ── */
        <div className="messages-layout">
          <div className="messages-sidebar messages-sidebar-empty">
            <div className="messages-empty-state">
              <span className="messages-empty-icon">{'\u{1F4E8}'}</span>
              <p className="messages-empty-title">暂无私信</p>
              <p className="messages-empty-desc">
                去角色市场关注感兴趣的作者，发送你的第一条私信吧
              </p>
            </div>
          </div>
          <div className="messages-chat-area">
            <div className="messages-empty-chat">选择一个会话</div>
          </div>
        </div>
      ) : (
        <div className="messages-layout">
          {/* ── Sidebar: conversation list ── */}
          <div
            className="messages-sidebar hide-scrollbar"
            style={{ display: !isMobile || mobileView === 'list' ? 'flex' : 'none' }}
          >
            {convLoading ? (
              <Loading text="加载中…" />
            ) : (
              conversations.map((conv) => (
                <button
                  key={conv.other_id}
                  type="button"
                  className={`messages-conv-item${activeOtherId === conv.other_id ? ' active' : ''}`}
                  onClick={() => handleSelectConversation(conv.other_id, conv.username)}
                >
                  <Avatar name={conv.username || '?'} size={40} />
                  <div className="messages-conv-body">
                    <div className="messages-conv-head">
                      <span className="messages-conv-name">{conv.username}</span>
                      <span className="messages-conv-time">{formatTime(conv.last_time)}</span>
                    </div>
                    <div className="messages-conv-bottom">
                      <p className="messages-conv-preview">{conv.last_message || ''}</p>
                      {conv.unread > 0 && (
                        <span className="messages-conv-badge">
                          {conv.unread > 99 ? '99+' : conv.unread}
                        </span>
                      )}
                    </div>
                  </div>
                </button>
              ))
            )}
          </div>

          {/* ── Chat area ── */}
          <div
            className="messages-chat-area"
            style={{ display: !isMobile || mobileView === 'chat' || !activeOtherId ? 'flex' : 'none' }}
          >
            {!activeOtherId ? (
              <div className="messages-empty-chat">选择一个会话</div>
            ) : (
              <>
                {!isMobile && (
                  <div className="messages-chat-header">{activeUsername}</div>
                )}

                <div className="messages-list">
                  {hasMore && (
                    <div className="messages-load-more">
                      <button type="button" className="btn-ghost fs-12" onClick={handleLoadMore} disabled={msgLoading}>
                        {msgLoading ? '加载中…' : '加载更多'}
                      </button>
                    </div>
                  )}
                  {messages.map((msg) => {
                    const isMe = msg.sender_id === authUser?.id
                    return (
                      <div key={msg.id} className={`messages-row${isMe ? ' mine' : ' other'}`}>
                        {!isMe && (
                          <Avatar name={activeUsername || '?'} size={28} />
                        )}
                        <div className={`messages-bubble${isMe ? ' mine' : ' other'}`}>
                          <span className="messages-msg-text">{msg.content}</span>
                          <span className="messages-msg-time">{formatTime(msg.created_at)}</span>
                        </div>
                      </div>
                    )
                  })}
                  <div ref={messagesEndRef} />
                </div>

                <div className="messages-input-bar">
                  <textarea
                    className="messages-input"
                    rows={1}
                    placeholder="输入消息…"
                    value={inputText}
                    onChange={(e) => setInputText(e.target.value)}
                    onKeyDown={handleKeyDown}
                  />
                  <button
                    type="button"
                    className="messages-send-btn"
                    disabled={!inputText.trim() || sending}
                    onClick={handleSend}
                  >
                    {sending ? '…' : '发送'}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
