import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { formatChatTime } from '../../utils/time'
import Loading from './Loading'

/**
 * Shared in-chat history panel.
 *
 * Modes:
 *   "dropdown" — toggle button shows floating panel below it (default)
 *   "overlay"  — toggle button opens full-screen overlay
 *   "sidebar"  — parent controls open/close via `open` prop; panel
 *                fills its container (no toggle button rendered).
 *
 * Props:
 *   fetchSessions:   (keyword) => Promise<Array<{id, title, preview, time}>>
 *   onSelectSession: (session) => void
 *   placeholder:     string
 *   mode:            "dropdown" | "overlay" | "sidebar"
 *   open:            boolean (sidebar mode only)
 *   onClose:         () => void (sidebar mode only)
 *   onExport:        () => void
 */
export default function ChatHistoryPanel({
  fetchSessions, onSelectSession, placeholder = '搜索历史消息…',
  mode = 'dropdown', open: externalOpen, onClose, onExport,
}) {
  const [internalOpen, setInternalOpen] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [panelStyle, setPanelStyle] = useState({})
  const [selectedDate, setSelectedDate] = useState('')
  const toggleRef = useRef(null)
  const panelRef = useRef(null)
  const inputRef = useRef(null)
  const autoSelectDate = useRef(false)

  const open = mode === 'sidebar' ? externalOpen : internalOpen

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

  const closePanel = useCallback(() => {
    setKeyword('')
    setLoaded(false)
    setSelectedDate('')
    autoSelectDate.current = false
    if (mode === 'sidebar') onClose?.()
    else setInternalOpen(false)
  }, [mode, onClose])

  // ── Toggle (dropdown/overlay) ──
  const toggle = () => {
    const next = !internalOpen
    setInternalOpen(next)
    if (!next) { setKeyword(''); setLoaded(false); setSelectedDate(''); autoSelectDate.current = false }
    else {
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

  // Click outside (dropdown only)
  useEffect(() => {
    if (mode !== 'dropdown' || !internalOpen) return
    const handler = (e) => {
      if (panelRef.current && !panelRef.current.contains(e.target) &&
          toggleRef.current && !toggleRef.current.contains(e.target)) {
        closePanel()
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [mode, internalOpen])

  // ── Date grouping ──
  const dateGroups = useMemo(() => {
    const dates = new Set()
    for (const s of sessions) {
      if (s.time) {
        try { dates.add(new Date(s.time).toISOString().slice(0, 10)) } catch {}
      }
    }
    return [...dates].sort().reverse()
  }, [sessions])

  // Auto-select most recent date after data loads
  useEffect(() => {
    if (dateGroups.length > 0 && !autoSelectDate.current && loaded) {
      setSelectedDate(dateGroups[0])
      autoSelectDate.current = true
    }
  }, [dateGroups, loaded])

  const filteredSessions = useMemo(() => {
    if (!selectedDate) return sessions
    return sessions.filter(s => {
      if (!s.time) return false
      try { return new Date(s.time).toISOString().slice(0, 10) === selectedDate } catch { return false }
    })
  }, [sessions, selectedDate])

  const displaySessions = filteredSessions

  function fmtDateLabel(isoDate) {
    if (!isoDate) return ''
    const d = new Date(isoDate + 'T00:00:00')
    const today = new Date()
    const todayStr = today.toISOString().slice(0, 10)
    const yesterdayStr = new Date(today.getTime() - 86400000).toISOString().slice(0, 10)
    if (isoDate === todayStr) return '今天'
    if (isoDate === yesterdayStr) return '昨天'
    const weekday = d.toLocaleDateString('zh-CN', { weekday: 'short' })
    const diff = Math.floor((today.getTime() - d.getTime()) / 86400000)
    if (diff < 7) return weekday
    return `${d.getMonth() + 1}月${d.getDate()}日`
  }

  // ── Shared panel content ──
  const renderPanelContent = (isSidebar) => (
    <>
      <div className={isSidebar ? 'history-sidebar-header' : 'chat-history-overlay-header'}>
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
        {onExport && (
          <button type="button" className="chat-history-export-btn" onClick={onExport} title="导出对话">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
              <polyline points="7 10 12 15 17 10"/>
              <line x1="12" y1="15" x2="12" y2="3"/>
            </svg>
          </button>
        )}
        {isSidebar ? (
          <button type="button" className="history-sidebar-close" onClick={closePanel}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        ) : (
          <button type="button" className="chat-history-overlay-close" onClick={closePanel}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        )}
      </div>

      {dateGroups.length > 1 && (
        <div className="chat-history-dates">
          {[{ key: '', label: '全部' }, ...dateGroups.map(d => ({ key: d, label: fmtDateLabel(d) }))].map(item => (
            <button
              key={item.key}
              type="button"
              className={`chat-history-date-chip${selectedDate === item.key ? ' active' : ''}`}
              onClick={() => setSelectedDate(item.key)}
            >{item.label}</button>
          ))}
        </div>
      )}

      <div className={isSidebar ? 'history-sidebar-body' : 'chat-history-overlay-body'}>
        {loading && <Loading text="搜索中…" />}
        {!loading && displaySessions.length === 0 && (
          <div className="chat-history-empty">{keyword ? '无匹配结果' : '暂无历史记录'}</div>
        )}
        {!loading && displaySessions.map((s, i) => (
          <button
            key={s.id || i}
            type="button"
            className="chat-history-item"
            onClick={() => { onSelectSession(s); closePanel() }}
          >
            <div className="chat-history-item-title">{s.title || '会话'}</div>
            <div className="chat-history-item-preview">{s.preview || ''}</div>
            <div className="chat-history-item-time">{formatChatTime(s.time)}</div>
          </button>
        ))}
      </div>
    </>
  )

  // ── Sidebar mode: content only, no toggle ──
  if (mode === 'sidebar') {
    if (!open) return null
    return (
      <div className="history-sidebar-content" ref={panelRef}>
        {renderPanelContent(true)}
      </div>
    )
  }

  // ── Dropdown / Overlay mode ──
  return (
    <div className="chat-history-panel" ref={panelRef}>
      <button
        ref={toggleRef}
        type="button"
        className={`chat-history-toggle${internalOpen ? ' active' : ''}`}
        onClick={toggle}
        title="历史记录"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="10" />
          <polyline points="12 6 12 12 16 14" />
        </svg>
        历史
      </button>

      {internalOpen && mode === 'dropdown' && (
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
                onClick={() => { onSelectSession(s); closePanel() }}
              >
                <div className="chat-history-item-title">{s.title || '会话'}</div>
                <div className="chat-history-item-preview">{s.preview || ''}</div>
                <div className="chat-history-item-time">{formatChatTime(s.time)}</div>
              </button>
            ))}
          </div>
        </div>
      )}

      {internalOpen && mode === 'overlay' && (
        <div className="chat-history-overlay">
          {renderPanelContent(false)}
        </div>
      )}
    </div>
  )
}
