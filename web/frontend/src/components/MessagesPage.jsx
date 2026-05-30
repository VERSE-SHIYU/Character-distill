import { useCallback, useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout } from '../api/client'
import Avatar from './common/Avatar'
import Loading from './common/Loading'
import PrivateMessageChat from './PrivateMessageChat'

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
  const goBack = useAppStore((s) => s.goBack)
  const previousView = useAppStore((s) => s.previousView)
  const messageTargetUserId = useAppStore((s) => s.messageTargetUserId)
  const setMessageTargetUserId = useAppStore((s) => s.setMessageTargetUserId)
  const messageTargetUsername = useAppStore((s) => s.messageTargetUsername)
  const setMessageTargetUsername = useAppStore((s) => s.setMessageTargetUsername)

  const [conversations, setConversations] = useState([])
  const [convLoading, setConvLoading] = useState(true)
  const [activeOtherId, setActiveOtherId] = useState(null)
  const [activeUsername, setActiveUsername] = useState('')
  const [mobileView, setMobileView] = useState('list')
  const [isMobile, setIsMobile] = useState(window.innerWidth <= 768)
  const pollTimerRef = useRef(null)

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
      setMobileView('chat')
      setMessageTargetUserId(null)
      setMessageTargetUsername(null)
    }
  }, [messageTargetUserId, messageTargetUsername, setMessageTargetUserId, setMessageTargetUsername])

  // When activeOtherId changes, update username from conversations
  useEffect(() => {
    if (activeOtherId) {
      const conv = conversations.find((c) => c.other_id === activeOtherId)
      if (conv) setActiveUsername(conv.username)
      loadConversations()
    }
  }, [activeOtherId])

  // Poll for conversation list updates
  useEffect(() => {
    if (!activeOtherId) return
    pollTimerRef.current = setInterval(() => {
      loadConversations()
    }, POLL_INTERVAL)
    return () => clearInterval(pollTimerRef.current)
  }, [activeOtherId, loadConversations])

  const handleSelectConversation = (otherId, username) => {
    setActiveOtherId(otherId)
    setActiveUsername(username)
    setMobileView('chat')
  }

  const handleBackFromChat = () => {
    if (isMobile && mobileView === 'chat') {
      setMobileView('list')
      return
    }
    if (previousView) {
      goBack()
      return
    }
    setView('home')
  }

  // ── Render ──
  return (
    <div className="panel messages-page">

      {!convLoading && conversations.length === 0 && !activeOtherId ? (
        /* ── Empty state ── */
        <div className="messages-layout">
          <div className="messages-sidebar messages-sidebar-empty">
            <div className="messages-sidebar-header">
              <h2 className="messages-sidebar-title">私信</h2>
              <button type="button" className="chat-back-btn" onClick={handleBackFromChat}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5m7-7-7 7 7 7"/></svg>
                  返回
                </button>
            </div>
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
            <div className="messages-sidebar-header">
              <h2 className="messages-sidebar-title">私信</h2>
              <button type="button" className="chat-back-btn" onClick={handleBackFromChat}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5m7-7-7 7 7 7"/></svg>
                  返回
                </button>
            </div>
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
                  <Avatar name={conv.username || '?'} src={conv.avatar_data} size={44} />
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
            style={{ display: !isMobile || mobileView === 'chat' ? 'flex' : 'none' }}
          >
            {!activeOtherId ? (
              <div className="messages-empty-chat">选择一个会话</div>
            ) : (
              <PrivateMessageChat
                otherUserId={activeOtherId}
                otherUsername={activeUsername}
              />
            )}
          </div>
        </div>
      )}
    </div>
  )
}
