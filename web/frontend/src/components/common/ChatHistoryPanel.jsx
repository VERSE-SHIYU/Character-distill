import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { formatChatTime } from '../../utils/time'
import Loading from './Loading'

// ── Calendar picker (also exported for external tab use) ──

export function Calendar({ dateGroups, selectedDate, onSelectDate }) {
  const datesSet = useMemo(() => new Set(dateGroups), [dateGroups])

  const years = useMemo(() => {
    const ys = new Set()
    for (const d of dateGroups) { try { ys.add(new Date(d + 'T00:00:00').getFullYear()) } catch {} }
    const nowY = new Date().getFullYear()
    ys.add(nowY)
    return [...ys].sort((a, b) => a - b)
  }, [dateGroups])

  const now = new Date()
  const currentYear = now.getFullYear()
  const currentMonth = now.getMonth() + 1

  // Default to month of selectedDate, or most recent date, or current month
  const defaultYear = selectedDate
    ? parseInt(selectedDate.slice(0, 4), 10)
    : dateGroups[0]
      ? parseInt(dateGroups[0].slice(0, 4), 10)
      : currentYear
  const defaultMonth = selectedDate
    ? parseInt(selectedDate.slice(5, 7), 10)
    : dateGroups[0]
      ? parseInt(dateGroups[0].slice(5, 7), 10)
      : currentMonth

  const [viewYear, setViewYear] = useState(defaultYear)
  const [viewMonth, setViewMonth] = useState(defaultMonth)

  const daysInMonth = new Date(viewYear, viewMonth, 0).getDate()
  const firstDayOfWeek = new Date(viewYear, viewMonth - 1, 1).getDay() // 0=Sun

  const todayStr = now.toISOString().slice(0, 10)

  const handlePrevMonth = () => {
    if (viewMonth === 1) { setViewMonth(12); setViewYear(viewYear - 1) }
    else setViewMonth(viewMonth - 1)
  }
  const handleNextMonth = () => {
    if (viewMonth === 12) { setViewMonth(1); setViewYear(viewYear + 1) }
    else setViewMonth(viewMonth + 1)
  }

  const dayCells = []
  // Empty cells for days before the 1st
  for (let i = 0; i < firstDayOfWeek; i++) {
    dayCells.push(<div key={`empty-${i}`} className="cal-day cal-day-empty" />)
  }
  for (let d = 1; d <= daysInMonth; d++) {
    const iso = `${String(viewYear).padStart(4, '0')}-${String(viewMonth).padStart(2, '0')}-${String(d).padStart(2, '0')}`
    const hasData = datesSet.has(iso)
    const isSelected = iso === selectedDate
    const isToday = iso === todayStr
    dayCells.push(
      <button
        key={d}
        type="button"
        className={`cal-day${hasData ? ' cal-day-has' : ''}${isSelected ? ' cal-day-sel' : ''}${isToday ? ' cal-day-today' : ''}`}
        disabled={!hasData}
        onClick={() => hasData && onSelectDate(iso)}
      >
        {d}
      </button>,
    )
  }

  return (
    <div className="calendar-picker">
      <div className="cal-nav">
        <button type="button" className="cal-nav-btn" onClick={handlePrevMonth}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
        </button>
        <div className="cal-nav-selects">
          <select className="cal-select" value={viewYear} onChange={e => setViewYear(parseInt(e.target.value, 10))}>
            {years.map(y => <option key={y} value={y}>{y}年</option>)}
          </select>
          <select className="cal-select" value={viewMonth} onChange={e => setViewMonth(parseInt(e.target.value, 10))}>
            {Array.from({ length: 12 }, (_, i) => i + 1).map(m => (
              <option key={m} value={m}>{m}月</option>
            ))}
          </select>
        </div>
        <button type="button" className="cal-nav-btn" onClick={handleNextMonth}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
        </button>
      </div>
      <div className="cal-weekdays">
        {['一', '二', '三', '四', '五', '六', '日'].map(d => (
          <span key={d} className="cal-weekday">{d}</span>
        ))}
      </div>
      <div className="cal-grid">
        {dayCells}
      </div>
      {selectedDate && (
        <button type="button" className="cal-clear" onClick={() => onSelectDate('')}>全部</button>
      )}
    </div>
  )
}

// ── ChatHistoryPanel ──

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
 *   selectedDate:    string (controlled from parent, for group-chat date-tab)
 *   onSelectDate:    (isoDate) => void (controlled from parent)
 */
export default function ChatHistoryPanel({
  fetchSessions, onSelectSession, placeholder = '搜索历史消息…',
  mode = 'dropdown', open: externalOpen, onClose, onExport,
  selectedDate: controlledDate, onSelectDate: controlledOnSelectDate,
  hideTabs = false,
}) {
  const [internalOpen, setInternalOpen] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [panelStyle, setPanelStyle] = useState({})
  const [internalSelectedDate, setInternalSelectedDate] = useState('')
  const [internalTab, setInternalTab] = useState('history') // 'history' | 'date'
  const toggleRef = useRef(null)
  const panelRef = useRef(null)
  const inputRef = useRef(null)
  const autoSelectDate = useRef(false)

  // Use controlled or internal date
  const selectedDate = controlledDate !== undefined ? controlledDate : internalSelectedDate
  const setSelectedDate = controlledOnSelectDate || setInternalSelectedDate

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
    if (!controlledDate) setInternalSelectedDate('')
    setInternalTab('history')
    autoSelectDate.current = false
    if (mode === 'sidebar') onClose?.()
    else setInternalOpen(false)
  }, [mode, onClose, controlledDate])

  // ── Toggle (dropdown/overlay) ──
  const toggle = () => {
    const next = !internalOpen
    setInternalOpen(next)
    if (!next) { setKeyword(''); setLoaded(false); if (!controlledDate) setInternalSelectedDate(''); setInternalTab('history'); autoSelectDate.current = false }
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
  }, [mode, internalOpen, closePanel])

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
    if (dateGroups.length > 0 && !autoSelectDate.current && loaded && !controlledDate) {
      setInternalSelectedDate(dateGroups[0])
      autoSelectDate.current = true
    }
  }, [dateGroups, loaded, controlledDate])

  const filteredSessions = useMemo(() => {
    if (!selectedDate) return sessions
    return sessions.filter(s => {
      if (!s.time) return false
      try { return new Date(s.time).toISOString().slice(0, 10) === selectedDate } catch { return false }
    })
  }, [sessions, selectedDate])

  const handleCalendarSelect = (isoDate) => {
    setSelectedDate(isoDate || '')
    if (isoDate) setInternalTab('history')
  }

  // ── Render panel content ──
  const renderPanelContent = (isSidebar) => {
    const showTabBar = !hideTabs
    return (
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

      {/* Date tabs: 历史 | 日期 */}
      {showTabBar && (
        <div className="history-date-tabs">
          <button
            type="button"
            className={`history-date-tab${internalTab === 'history' ? ' active' : ''}`}
            onClick={() => setInternalTab('history')}
          >历史</button>
          <button
            type="button"
            className={`history-date-tab${internalTab === 'date' ? ' active' : ''}`}
            onClick={() => setInternalTab('date')}
          >日期</button>
        </div>
      )}

      {internalTab === 'date' ? (
        <div className={isSidebar ? 'history-sidebar-body' : 'chat-history-overlay-body'}>
          <Calendar dateGroups={dateGroups} selectedDate={selectedDate} onSelectDate={handleCalendarSelect} />
        </div>
      ) : (
        <div className={isSidebar ? 'history-sidebar-body' : 'chat-history-overlay-body'}>
          {loading && <Loading text="搜索中…" />}
          {!loading && filteredSessions.length === 0 && (
            <div className="chat-history-empty">{keyword ? '无匹配结果' : selectedDate ? '该日期无记录' : '暂无历史记录'}</div>
          )}
          {!loading && filteredSessions.map((s, i) => (
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
      )}
    </>
    )
  }

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
          {renderPanelContent(false)}
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
