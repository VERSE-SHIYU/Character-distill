import { useCallback, useEffect, useRef, useState } from 'react'
import { formatChatTime } from '../../utils/time'
import Loading from './Loading'

/**
 * Shared in-chat history panel.
 *
 * Props:
 *   fetchSessions: (keyword) => Promise<Array<{id, title, preview, time, extra?}>>
 *   onSelectSession: (session) => void
 *   placeholder: string - search placeholder text
 */
export default function ChatHistoryPanel({ fetchSessions, onSelectSession, placeholder = '搜索历史消息…', overlay = false }) {
  const [open, setOpen] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [panelStyle, setPanelStyle] = useState({})
  const toggleRef = useRef(null)
  const panelRef = useRef(null)
  const inputRef = useRef(null)

  const doFetch = useCallback(async (kw) => {
    setLoading(true)
    try {
      const data = await fetchSessions(kw)
      setSessions(data || [])
    } catch {
      setSessions([])
    } finally {
      setLoading(false)
    }
  }, [fetchSessions])

  useEffect(() => {
    if (open && !loaded) {
      doFetch('')
      setLoaded(true)
    }
  }, [open, loaded, doFetch])

  useEffect(() => {
    if (!open) return
    const timer = setTimeout(() => doFetch(keyword), 300)
    return () => clearTimeout(timer)
  }, [keyword, open, doFetch])

  // Click outside to close
  useEffect(() => {
    if (!open) return
    const handler = (e) => {
      if (panelRef.current && !panelRef.current.contains(e.target) &&
          toggleRef.current && !toggleRef.current.contains(e.target)) {
        setOpen(false)
        setKeyword('')
        setLoaded(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const toggle = () => {
    const next = !open
    setOpen(next)
    if (!next) { setKeyword(''); setLoaded(false) }
    else {
      // Position the panel relative to the toggle button
      if (toggleRef.current) {
        const rect = toggleRef.current.getBoundingClientRect()
        setPanelStyle({
          position: 'fixed',
          top: rect.bottom + 4,
          right: document.documentElement.clientWidth - rect.right,
        })
      }
      setTimeout(() => inputRef.current?.focus(), 100)
    }
  }

  return (
    <div className="chat-history-panel" ref={panelRef}>
      <button
        ref={toggleRef}
        type="button"
        className={`chat-history-toggle${open ? ' active' : ''}`}
        onClick={toggle}
        title="历史记录"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="10" />
          <polyline points="12 6 12 12 16 14" />
        </svg>
        历史
      </button>

      {open && !overlay && (
        <div className="chat-history-panel-body" style={panelStyle}>
          <div className="chat-history-search-bar">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
              <circle cx="11" cy="11" r="8" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <input
              ref={inputRef}
              type="text"
              className="chat-history-search-input"
              placeholder={placeholder}
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
            />
          </div>

          <div className="chat-history-results">
            {loading && <Loading text="搜索中…" />}
            {!loading && sessions.length === 0 && (
              <div className="chat-history-empty">{keyword ? '无匹配结果' : '暂无历史记录'}</div>
            )}
            {!loading && sessions.map((s, i) => (
              <button
                key={s.id || i}
                type="button"
                className="chat-history-item"
                onClick={() => { onSelectSession(s); setOpen(false); setKeyword(''); setLoaded(false) }}
              >
                <div className="chat-history-item-title">{s.title || '会话'}</div>
                <div className="chat-history-item-preview">{s.preview || ''}</div>
                <div className="chat-history-item-time">{formatChatTime(s.time)}</div>
              </button>
            ))}
          </div>
        </div>
      )}

      {open && overlay && (
        <div className="chat-history-overlay">
          <div className="chat-history-overlay-header">
            <div className="chat-history-search-bar" style={{ flex: 1 }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
                <circle cx="11" cy="11" r="8" />
                <line x1="21" y1="21" x2="16.65" y2="16.65" />
              </svg>
              <input
                ref={inputRef}
                type="text"
                className="chat-history-search-input"
                placeholder={placeholder}
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
              />
            </div>
            <button type="button" className="chat-history-overlay-close" onClick={() => { setOpen(false); setKeyword(''); setLoaded(false) }}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
          </div>

          <div className="chat-history-overlay-body">
            {loading && <Loading text="搜索中…" />}
            {!loading && sessions.length === 0 && (
              <div className="chat-history-empty">{keyword ? '无匹配结果' : '暂无历史记录'}</div>
            )}
            {!loading && sessions.map((s, i) => (
              <button
                key={s.id || i}
                type="button"
                className="chat-history-item"
                onClick={() => { onSelectSession(s); setOpen(false); setKeyword(''); setLoaded(false) }}
              >
                <div className="chat-history-item-title">{s.title || '会话'}</div>
                <div className="chat-history-item-preview">{s.preview || ''}</div>
                <div className="chat-history-item-time">{formatChatTime(s.time)}</div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
