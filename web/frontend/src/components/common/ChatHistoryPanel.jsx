import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { formatChatTime } from '../../utils/time'
import { ChevronLeft, ChevronRight, ChevronDown, Search, Close } from './Icon'
import Avatar from './Avatar'

// ── Calendar picker (also exported for external tab use) ──

function PickerDropdown({ options, selected, onSelect, onClose, suffix = '' }) {
  const ref = useRef(null)
  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) onClose() }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [onClose])
  return (
    <div className="cal-picker-drop" ref={ref}>
      {options.map(opt => (
        <button
          key={opt.value}
          type="button"
          className={`cal-picker-opt${opt.value === selected ? ' sel' : ''}`}
          onClick={() => { onSelect(opt.value); onClose() }}
        >
          {opt.label}{suffix}
        </button>
      ))}
    </div>
  )
}

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
  const [openPicker, setOpenPicker] = useState(null)

  const daysInMonth = new Date(viewYear, viewMonth, 0).getDate()
  const firstDayOfWeek = new Date(viewYear, viewMonth - 1, 1).getDay()
  const todayStr = now.toISOString().slice(0, 10)

  const handlePrevMonth = () => {
    if (viewMonth === 1) { setViewMonth(12); setViewYear(viewYear - 1) }
    else setViewMonth(viewMonth - 1)
  }
  const handleNextMonth = () => {
    if (viewMonth === 12) { setViewMonth(1); setViewYear(viewYear + 1) }
    else setViewMonth(viewMonth + 1)
  }

  const yearOpts = years.map(y => ({ value: y, label: String(y) }))
  const monthOpts = Array.from({ length: 12 }, (_, i) => ({ value: i + 1, label: String(i + 1) }))

  const dayCells = []
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
          <ChevronLeft size={14} />
        </button>
        <div className="cal-nav-center">
          <div className="cal-nav-pills">
            <button
              type="button"
              className={`cal-pill${openPicker === 'year' ? ' active' : ''}`}
              onClick={() => setOpenPicker(openPicker === 'year' ? null : 'year')}
            >
              {viewYear}年
              <ChevronDown size={10} />
            </button>
            {openPicker === 'year' && (
              <PickerDropdown options={yearOpts} selected={viewYear} onSelect={setViewYear} onClose={() => setOpenPicker(null)} suffix="年" />
            )}
          </div>
          <div className="cal-nav-pills">
            <button
              type="button"
              className={`cal-pill${openPicker === 'month' ? ' active' : ''}`}
              onClick={() => setOpenPicker(openPicker === 'month' ? null : 'month')}
            >
              {viewMonth}月
              <ChevronDown size={10} />
            </button>
            {openPicker === 'month' && (
              <PickerDropdown options={monthOpts} selected={viewMonth} onSelect={setViewMonth} onClose={() => setOpenPicker(null)} suffix="月" />
            )}
          </div>
        </div>
        <button type="button" className="cal-nav-btn" onClick={handleNextMonth}>
          <ChevronRight size={14} />
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

// ── Message-level history panel ──

/**
 * Inline message-history panel for 1v1 chat / DM.
 *
 * Props:
 *   messages        raw message array (filtered internally)
 *   speakers        [{key, label}] — speaker filter tab defs
 *   dateGroups      [isoString] — pre-computed dates that have messages
 *   selectedDate    iso string — currently selected date (controlled)
 *   onSelectDate    fn(iso) — date selection callback
 *   onJumpTo        fn(msgId) — scroll chat to this message
 *   onClose         fn() — close the panel
 *   extraActions    ReactNode — slot for export button etc.
 *   renderMessage   fn(msg, i, speakerLabel) => ReactNode — custom message row
 *   resolveSpeaker  fn(msg) => { name, src } — resolve speaker avatar/name per msg
 */
export default function ChatHistoryPanel({
  messages = [],
  speakers = [],
  dateGroups: externalDateGroups,
  selectedDate: controlledDate,
  onSelectDate,
  onJumpTo,
  onClose,
  extraActions,
  renderMessage,
  resolveSpeaker,
}) {
  const [searchKeyword, setSearchKeyword] = useState('')
  const [historyTab, setHistoryTab] = useState('history')
  const [historyFilterSpeaker, setHistoryFilterSpeaker] = useState('all')
  const [internalDate, setInternalDate] = useState('')

  // Allow controlled or internal date management
  const selectedDate = controlledDate !== undefined ? controlledDate : internalDate
  const setSelectedDate = onSelectDate || setInternalDate

  // Compute date groups from messages if not provided externally
  const defaultDateGroups = useMemo(() => {
    const dates = new Set()
    for (const m of messages) {
      const ts = m.timestamp || m.created_at
      if (ts) {
        try { dates.add(new Date(ts).toISOString().slice(0, 10)) } catch {}
      }
    }
    return [...dates].sort().reverse()
  }, [messages])
  const dateGroups = externalDateGroups || defaultDateGroups

  const filteredMessages = useMemo(() => {
    let result = messages
    if (selectedDate) {
      result = result.filter(m => {
        const ts = m.timestamp || m.created_at
        const d = ts ? new Date(ts).toISOString().slice(0, 10) : ''
        return d === selectedDate
      })
    }
    if (searchKeyword) {
      const q = searchKeyword.toLowerCase()
      result = result.filter(m => (m.content || '').toLowerCase().includes(q))
    }
    if (historyFilterSpeaker === 'other') {
      result = result.filter(m => m.role !== 'user')
    } else if (historyFilterSpeaker === 'me') {
      result = result.filter(m => m.role === 'user')
    }
    return result
  }, [messages, selectedDate, searchKeyword, historyFilterSpeaker])

  const handleCalendarSelect = (iso) => {
    setSelectedDate(iso || '')
    if (iso) setHistoryTab('history')
  }

  const speakerLabel = (key) => {
    const found = speakers.find(s => s.key === key)
    return found?.label || key
  }

  return (
    <div className="history-sidebar-content">
      <div className="history-sidebar-header">
        <div className="chat-history-search-bar" style={{ flex: 1 }}>
          <Search size={14} style={{ flexShrink: 0 }} />
          <input type="text" className="chat-history-search-input" placeholder="搜索消息…"
            value={searchKeyword}
            onChange={(e) => setSearchKeyword(e.target.value)} />
        </div>
        {extraActions}
        <button type="button" className="history-sidebar-close" onClick={onClose}>
          <Close size={20} />
        </button>
      </div>

      <div className="history-date-tabs">
        <button type="button" className={`history-date-tab${historyTab === 'history' ? ' active' : ''}`}
          onClick={() => setHistoryTab('history')}>历史</button>
        <button type="button" className={`history-date-tab${historyTab === 'date' ? ' active' : ''}`}
          onClick={() => setHistoryTab('date')}>日期</button>
      </div>

      {speakers.length > 0 && (
        <div className="history-speaker-tabs">
          <button type="button" className={`history-speaker-tab${historyFilterSpeaker === 'all' ? ' active' : ''}`}
            onClick={() => setHistoryFilterSpeaker('all')}>全部</button>
          {speakers.map(s => (
            <button key={s.key} type="button" className={`history-speaker-tab${historyFilterSpeaker === s.key ? ' active' : ''}`}
              onClick={() => setHistoryFilterSpeaker(s.key)}>{s.label}</button>
          ))}
        </div>
      )}

      {historyTab === 'date' ? (
        <div className="history-sidebar-body">
          <Calendar dateGroups={dateGroups} selectedDate={selectedDate} onSelectDate={handleCalendarSelect} />
        </div>
      ) : (
        <div className="history-sidebar-body">
          {(selectedDate || historyFilterSpeaker !== 'all') && (
            <div className="group-history-filter-bar">
              <span className="group-history-filter-label">筛选：</span>
              {selectedDate && (
                <span className="group-history-filter-chip">
                  {selectedDate}
                  <button type="button" className="group-history-filter-chip-x" onClick={() => setSelectedDate('')}>
                    <Close size={10} />
                  </button>
                </span>
              )}
              {historyFilterSpeaker !== 'all' && (
                <span className="group-history-filter-chip">
                  {speakerLabel(historyFilterSpeaker)}
                  <button type="button" className="group-history-filter-chip-x" onClick={() => setHistoryFilterSpeaker('all')}>
                    <Close size={10} />
                  </button>
                </span>
              )}
            </div>
          )}

          {filteredMessages.length === 0 ? (
            <div className="group-history-empty">暂无消息</div>
          ) : (
            <div className="group-history-list">
              {filteredMessages.map((m, i) => {
                if (renderMessage) return renderMessage(m, i, speakerLabel)
                const ts = m.timestamp || m.created_at
                const time = ts ? formatChatTime(ts) : ''
                const speaker = resolveSpeaker?.(m) || { name: '?', src: null }
                return (
                  <div key={m.id || i} className="group-history-item" onClick={() => onJumpTo?.(m.id)}>
                    <Avatar name={speaker.name} size={28} src={speaker.src} />
                    <div className="group-history-item-body">
                      <div className="group-history-item-head">
                        <span className="group-history-item-speaker">{speaker.name}</span>
                        <span className="group-history-item-time">{time}</span>
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
  )
}
