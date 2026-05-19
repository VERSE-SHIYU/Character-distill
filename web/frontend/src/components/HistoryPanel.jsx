import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout } from '../api/client'
import Avatar from './common/Avatar'
import Loading from './common/Loading'
import ErrorBox from './common/ErrorBox'

const PAGE_SIZE = 20

function useDebouncedValue(value, delayMs) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(t)
  }, [value, delayMs])
  return debounced
}

function formatTime(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

function previewText(text, max = 72) {
  if (!text) return '暂无消息'
  const one = text.replace(/\s+/g, ' ').trim()
  return one.length > max ? `${one.slice(0, max)}…` : one
}

async function downloadExport(sessionId, format) {
  const res = await fetchWithTimeout(
    `/api/history/${sessionId}/export?format=${format}`,
  )
  const blob =
    format === 'json'
      ? await res.blob()
      : new Blob([await res.text()], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `session-${sessionId.slice(0, 8)}.${format}`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export default function HistoryPanel() {
  const texts = useAppStore((s) => s.texts)
  const cards = useAppStore((s) => s.cards)
  const resumeSession = useAppStore((s) => s.resumeSession)
  const resumeLoading = useAppStore((s) => s.resumeLoading)

  const [keyword, setKeyword] = useState('')
  const [character, setCharacter] = useState('')
  const [textFilter, setTextFilter] = useState('')
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasMore, setHasMore] = useState(true)
  const [error, setError] = useState(null)
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [collapsedGroups, setCollapsedGroups] = useState({})
  const [selectMode, setSelectMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState(new Set())
  const pageRef = useRef(1)
  const listRef = useRef(null)
  const abortRef = useRef(false)

  const debouncedKeyword = useDebouncedValue(keyword, 300)

  const characterOptions = useMemo(() => {
    const names = new Set()
    cards.forEach((c) => {
      let n = c.name
      if (!n && c.card_json) {
        try {
          const parsed = typeof c.card_json === 'string'
            ? JSON.parse(c.card_json)
            : c.card_json
          n = parsed?.name
        } catch { /* ignore */ }
      }
      if (n) names.add(n)
    })
    items.forEach((it) => {
      if (it.character_name) names.add(it.character_name)
    })
    return Array.from(names).sort((a, b) => a.localeCompare(b, 'zh-CN'))
  }, [cards, items])

  const groupedItems = useMemo(() => {
    const map = {}
    items.forEach((it) => {
      const tid = it.text_id || '__unknown__'
      if (!map[tid]) map[tid] = []
      map[tid].push(it)
    })
    return map
  }, [items])

  const loadPage = useCallback(async (page, append) => {
    const setter = append ? setLoadingMore : setLoading
    setter(true)
    setError(null)
    try {
      const params = new URLSearchParams({
        page: String(page),
        page_size: String(PAGE_SIZE),
      })
      if (debouncedKeyword.trim()) params.set('keyword', debouncedKeyword.trim())
      if (character) params.set('character', character)
      if (textFilter) params.set('text_id', textFilter)

      const res = await fetchWithTimeout(`/api/history/list?${params}`)
      const data = await res.json()
      const newItems = data.items || []
      if (append) {
        setItems((prev) => [...prev, ...newItems])
      } else {
        setItems(newItems)
      }
      const total = data.total ?? 0
      if (append) {
        setHasMore(newItems.length === PAGE_SIZE && (items.length + newItems.length) < total)
      } else {
        setHasMore(newItems.length === PAGE_SIZE && newItems.length < total)
      }
    } catch (err) {
      console.error('[HistoryPanel] load failed:', err)
      setError(err.message || '加载失败')
    } finally {
      setter(false)
    }
  }, [debouncedKeyword, character, textFilter])

  // Reset + load first page on filter change
  useEffect(() => {
    pageRef.current = 1
    abortRef.current = false
    loadPage(1, false)
  }, [loadPage])

  // Infinite scroll handler
  useEffect(() => {
    const el = listRef.current
    if (!el) return

    const handleScroll = () => {
      if (abortRef.current || loadingMore || !hasMore) return
      const { scrollTop, scrollHeight, clientHeight } = el
      if (scrollHeight - scrollTop - clientHeight < 160) {
        pageRef.current += 1
        loadPage(pageRef.current, true)
      }
    }

    el.addEventListener('scroll', handleScroll, { passive: true })
    return () => el.removeEventListener('scroll', handleScroll)
  }, [loadingMore, hasMore, loadPage])

  const openDetail = async (sessionId) => {
    setDetailLoading(true)
    setError(null)
    try {
      const res = await fetchWithTimeout(`/api/history/${sessionId}`)
      const data = await res.json()
      setDetail(data)
    } catch (err) {
      console.error('[HistoryPanel] load detail failed:', err)
      setError(err.message || '加载详情失败')
    } finally {
      setDetailLoading(false)
    }
  }

  const handleDelete = async (sessionId) => {
    if (!window.confirm('确定删除该会话？此操作不可恢复。')) return
    try {
      await fetchWithTimeout(`/api/history/${sessionId}`, { method: 'DELETE' })
      if (detail?.session?.id === sessionId) setDetail(null)
      setItems((prev) => prev.filter((it) => it.id !== sessionId))
    } catch (err) {
      console.error('[HistoryPanel] delete failed:', err)
      setError(err.message || '删除失败')
    }
  }

  const handleContinue = async (sessionId) => {
    try {
      await resumeSession(sessionId)
    } catch { /* store sets error */ }
  }

  const handleClearAll = async () => {
    if (!window.confirm('确定清空全部历史记录？此操作不可恢复。')) return
    try {
      await fetchWithTimeout('/api/history/clear-all', { method: 'POST' })
      setItems([])
      setDetail(null)
    } catch (err) {
      console.error('[HistoryPanel] clear-all failed:', err)
      setError(err.message || '清空失败')
    }
  }

  const toggleSelect = (id) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const handleBatchDelete = async () => {
    if (selectedIds.size === 0) return
    if (!window.confirm(`确定删除选中的 ${selectedIds.size} 条会话？`)) return
    for (const id of selectedIds) {
      try {
        await fetchWithTimeout(`/api/history/${id}`, { method: 'DELETE' })
      } catch (err) {
        console.error('[HistoryPanel] batch delete failed:', err)
      }
    }
    setItems((prev) => prev.filter((it) => !selectedIds.has(it.id)))
    setSelectedIds(new Set())
    setSelectMode(false)
  }

  const toggleGroup = (textId) => {
    setCollapsedGroups((prev) => ({ ...prev, [textId]: !prev[textId] }))
  }

  if (detail) {
    return (
      <HistoryDetail
        data={detail}
        loading={detailLoading}
        resumeLoading={resumeLoading}
        onBack={() => setDetail(null)}
        onContinue={() => handleContinue(detail.session.id)}
        onDelete={() => handleDelete(detail.session.id)}
        onExport={(fmt) => downloadExport(detail.session.id, fmt)}
      />
    )
  }

  return (
    <div className="history-panel panel">
      <header className="panel-header">
        <h1 className="panel-title">历史记录</h1>
        <p className="panel-desc">搜索、筛选并管理过往对话</p>
      </header>

      {error && <ErrorBox message={error} onDismiss={() => setError(null)} />}

      <div className="history-toolbar">
        <input
          type="search"
          className="history-search"
          placeholder="搜索消息关键词…"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
        />
        <select
          className="history-filter"
          value={textFilter}
          onChange={(e) => setTextFilter(e.target.value)}
        >
          <option value="">全部文本</option>
          {texts.map((t) => (
            <option key={t.id} value={t.id}>{t.filename}</option>
          ))}
        </select>
        <select
          className="history-filter"
          value={character}
          onChange={(e) => setCharacter(e.target.value)}
        >
          <option value="">全部角色</option>
          {characterOptions.map((name) => (
            <option key={name} value={name}>{name}</option>
          ))}
        </select>
        <button
          type="button"
          className="btn-secondary btn-sm"
          onClick={() => { setSelectMode(!selectMode); setSelectedIds(new Set()) }}
        >
          {selectMode ? '取消' : '多选'}
        </button>
        {selectMode && selectedIds.size > 0 && (
          <button
            type="button"
            className="btn-danger-sm"
            onClick={handleBatchDelete}
          >
            删除 ({selectedIds.size})
          </button>
        )}
        {!selectMode && items.length > 0 && (
          <button
            type="button"
            className="btn-danger-sm"
            onClick={handleClearAll}
          >
            清空全部
          </button>
        )}
      </div>

      {loading ? (
        <Loading text="加载会话…" />
      ) : items.length === 0 ? (
        <p className="history-empty">暂无匹配的会话</p>
      ) : (
        <div className="history-grouped" ref={listRef}>
          {Object.entries(groupedItems).map(([textId, sessionList]) => {
            const text = texts.find((t) => t.id === textId)
            const textName = text?.filename || (textId === '__unknown__' ? '未关联文本' : textId.slice(0, 8))
            return (
              <div key={textId} className="history-group">
                <h3 className="history-group-title" onClick={() => toggleGroup(textId)}>
                  <span className={`history-group-arrow${collapsedGroups[textId] ? ' collapsed' : ''}`}>{collapsedGroups[textId] ? '▶' : '▼'}</span>
                  {'\u{1F4D6}'} {textName}
                </h3>
                {!collapsedGroups[textId] && (
                <ul className="history-list">
                  {sessionList.map((it) => (
                    <li key={it.id} className={`history-swipe-wrapper${selectMode ? ' select-mode' : ''}`}>
                      {selectMode && (
                        <input
                          type="checkbox"
                          className="history-checkbox"
                          checked={selectedIds.has(it.id)}
                          onChange={() => toggleSelect(it.id)}
                          onClick={(e) => e.stopPropagation()}
                        />
                      )}
                      <div className="history-swipe-actions">
                        <button
                          type="button"
                          className="history-swipe-delete"
                          onClick={(e) => {
                            e.stopPropagation()
                            if (window.confirm('确定删除该会话？')) handleDelete(it.id)
                          }}
                        >
                          删除
                        </button>
                      </div>
                      <button
                        type="button"
                        className="history-item"
                        onClick={() => selectMode ? toggleSelect(it.id) : openDetail(it.id)}
                      >
                        <Avatar name={it.character_name || '?'} size={40} />
                        <div className="history-item-body">
                          <div className="history-item-head">
                            <div className="history-item-name-row">
                              <span className="history-item-name">{it.character_name}</span>
                              {textName && textId !== '__unknown__' && (
                                <span className="history-item-text-label">{textName}</span>
                              )}
                            </div>
                            <span className="history-item-time">
                              {formatTime(it.last_message_at || it.updated_at)}
                            </span>
                          </div>
                          <p className="history-item-preview">
                            {previewText(it.last_message)}
                          </p>
                        </div>
                      </button>
                    </li>
                  ))}
                </ul>
                )}
              </div>
            )
          })}
          {loadingMore && (
            <Loading text="加载更多…" />
          )}
          {!hasMore && items.length > 0 && (
            <p className="history-end">已加载全部会话</p>
          )}
        </div>
      )}
    </div>
  )
}

function HistoryDetail({ data, loading, onBack, onContinue, onDelete, onExport, resumeLoading }) {
  const session = data.session || {}
  const messages = data.messages || []
  const charName = session.character_name || '?'

  return (
    <div className="history-panel panel history-detail-view">
      <div className="history-detail-top">
        <button type="button" className="history-back-btn" onClick={onBack}>
          ← 返回列表
        </button>
        <div className="history-detail-actions">
          <button type="button" className="btn-primary history-action-sm" disabled={resumeLoading} onClick={onContinue}>
            {resumeLoading ? '加载中…' : '继续对话'}
          </button>
          <button
            type="button"
            className="history-action-sm history-action-ghost"
            onClick={() => onExport('json')}
          >
            导出 JSON
          </button>
          <button
            type="button"
            className="history-action-sm history-action-ghost"
            onClick={() => onExport('txt')}
          >
            导出 TXT
          </button>
          <button
            type="button"
            className="history-action-sm history-action-danger"
            onClick={onDelete}
          >
            删除
          </button>
        </div>
      </div>

      <header className="history-detail-header">
        <Avatar name={charName} size={75} />
        <div className="history-detail-meta-wrap">
          <h2 className="history-detail-name">{charName}</h2>
          {session.user_role && (
            <span className="chat-topbar-user-badge">{session.user_role}</span>
          )}
          <p className="history-detail-meta">
            {formatTime(session.updated_at || session.created_at)}
          </p>
        </div>
      </header>

      <p className="history-readonly-hint">只读预览</p>

      {loading ? (
        <Loading text="加载中…" />
      ) : (
        <div className="history-readonly-messages">
          {messages.map((msg, i) => {
            if (msg.role === 'summary') {
              return (
                <div key={msg.id ?? i} className="chat-summary history-readonly-summary">
                  <div className="chat-summary-toggle" style={{ cursor: 'default' }}>
                    <span>{'\u{1F4CB}'} 对话摘要</span>
                  </div>
                  <div className="chat-summary-body">{msg.content}</div>
                </div>
              )
            }
            const isUser = msg.role === 'user'
            return (
              <div
                key={msg.id ?? i}
                className={`chat-msg ${isUser ? 'chat-msg-user' : 'chat-msg-char'} history-readonly-msg`}
              >
                {!isUser && (
                  <div className="chat-msg-avatar">
                    <Avatar name={charName} size={70} />
                  </div>
                )}
                <div className={`chat-bubble ${isUser ? 'chat-bubble-user' : 'chat-bubble-char'}`}>
                  {msg.content}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
