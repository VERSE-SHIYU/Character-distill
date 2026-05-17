import { useCallback, useEffect, useMemo, useState } from 'react'
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
  if (!iso) return '\u2014'
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
  if (!text) return '\u6682\u65e0\u6d88\u606f'
  const one = text.replace(/\s+/g, ' ').trim()
  return one.length > max ? `${one.slice(0, max)}\u2026` : one
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
  const cards = useAppStore((s) => s.cards)
  const resumeSession = useAppStore((s) => s.resumeSession)

  const [keyword, setKeyword] = useState('')
  const [character, setCharacter] = useState('')
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)

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

  const loadList = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({
        page: String(page),
        page_size: String(PAGE_SIZE),
      })
      if (debouncedKeyword.trim()) params.set('keyword', debouncedKeyword.trim())
      if (character) params.set('character', character)

      const res = await fetchWithTimeout(`/api/history/list?${params}`)
      const data = await res.json()
      setItems(data.items || [])
      setTotal(data.total ?? 0)
    } catch (err) {
      console.error('[HistoryPanel] load list failed:', err)
      setError(err.message || '\u52a0\u8f7d\u5931\u8d25')
    } finally {
      setLoading(false)
    }
  }, [page, debouncedKeyword, character])

  useEffect(() => {
    setPage(1)
  }, [debouncedKeyword, character])

  useEffect(() => {
    loadList()
  }, [loadList])

  const openDetail = async (sessionId) => {
    setDetailLoading(true)
    setError(null)
    try {
      const res = await fetchWithTimeout(`/api/history/${sessionId}`)
      const data = await res.json()
      setDetail(data)
    } catch (err) {
      console.error('[HistoryPanel] load detail failed:', err)
      setError(err.message || '\u52a0\u8f7d\u8be6\u60c5\u5931\u8d25')
    } finally {
      setDetailLoading(false)
    }
  }

  const handleDelete = async (sessionId) => {
    if (!window.confirm('\u786e\u5b9a\u5220\u9664\u8be5\u4f1a\u8bdd\uff1f\u6b64\u64cd\u4f5c\u4e0d\u53ef\u6062\u590d\u3002')) return
    try {
      await fetchWithTimeout(`/api/history/${sessionId}`, { method: 'DELETE' })
      if (detail?.session?.id === sessionId) setDetail(null)
      await loadList()
    } catch (err) {
      console.error('[HistoryPanel] delete failed:', err)
      setError(err.message || '\u5220\u9664\u5931\u8d25')
    }
  }

  const handleContinue = async (sessionId) => {
    try {
      await resumeSession(sessionId)
    } catch { /* store sets error */ }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  if (detail) {
    return (
      <HistoryDetail
        data={detail}
        loading={detailLoading}
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
        <h1 className="panel-title">{'\u5386\u53f2\u8bb0\u5f55'}</h1>
        <p className="panel-desc">{'\u641c\u7d22\u3001\u7b5b\u9009\u5e76\u7ba1\u7406\u8fc7\u5f80\u5bf9\u8bdd'}</p>
      </header>

      {error && <ErrorBox message={error} onDismiss={() => setError(null)} />}

      <div className="history-toolbar">
        <input
          type="search"
          className="history-search"
          placeholder={'\u641c\u7d22\u6d88\u606f\u5173\u952e\u8bcd\u2026'}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
        />
        <select
          className="history-filter"
          value={character}
          onChange={(e) => setCharacter(e.target.value)}
        >
          <option value="">{'\u5168\u90e8\u89d2\u8272'}</option>
          {characterOptions.map((name) => (
            <option key={name} value={name}>{name}</option>
          ))}
        </select>
      </div>

      {loading && items.length === 0 ? (
        <Loading text={'\u52a0\u8f7d\u4f1a\u8bdd\u2026'} />
      ) : items.length === 0 ? (
        <p className="history-empty">{'\u6682\u65e0\u5339\u914d\u7684\u4f1a\u8bdd'}</p>
      ) : (
        <ul className="history-list">
          {items.map((it) => (
            <li key={it.id}>
              <button
                type="button"
                className="history-item"
                onClick={() => openDetail(it.id)}
              >
                <Avatar name={it.character_name || '?'} size={40} />
                <div className="history-item-body">
                  <div className="history-item-head">
                    <span className="history-item-name">{it.character_name}</span>
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

      {total > PAGE_SIZE && (
        <div className="history-pagination">
          <button
            type="button"
            className="history-page-btn"
            disabled={page <= 1 || loading}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            {'\u2190 \u4e0a\u4e00\u9875'}
          </button>
          <span className="history-page-info">
            {page} / {totalPages} {'\u00b7 '} {total} {'\u6761'}
          </span>
          <button
            type="button"
            className="history-page-btn"
            disabled={page >= totalPages || loading}
            onClick={() => setPage((p) => p + 1)}
          >
            {'\u4e0b\u4e00\u9875 \u2192'}
          </button>
        </div>
      )}
    </div>
  )
}

function HistoryDetail({ data, loading, onBack, onContinue, onDelete, onExport }) {
  const session = data.session || {}
  const messages = data.messages || []
  const charName = session.character_name || '?'

  return (
    <div className="history-panel panel history-detail-view">
      <div className="history-detail-top">
        <button type="button" className="history-back-btn" onClick={onBack}>
          {'\u2190 \u8fd4\u56de\u5217\u8868'}
        </button>
        <div className="history-detail-actions">
          <button type="button" className="btn-primary history-action-sm" onClick={onContinue}>
            {'\u7ee7\u7eed\u5bf9\u8bdd'}
          </button>
          <button
            type="button"
            className="history-action-sm history-action-ghost"
            onClick={() => onExport('json')}
          >
            {'\u5bfc\u51fa JSON'}
          </button>
          <button
            type="button"
            className="history-action-sm history-action-ghost"
            onClick={() => onExport('txt')}
          >
            {'\u5bfc\u51fa TXT'}
          </button>
          <button
            type="button"
            className="history-action-sm history-action-danger"
            onClick={onDelete}
          >
            {'\u5220\u9664'}
          </button>
        </div>
      </div>

      <header className="history-detail-header">
        <Avatar name={charName} size={44} />
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

      <p className="history-readonly-hint">{'\u53ea\u8bfb\u9884\u89c8'}</p>

      {loading ? (
        <Loading text={'\u52a0\u8f7d\u4e2d\u2026'} />
      ) : (
        <div className="history-readonly-messages">
          {messages.map((msg, i) => {
            if (msg.role === 'summary') {
              return (
                <div key={msg.id ?? i} className="chat-summary history-readonly-summary">
                  <div className="chat-summary-toggle" style={{ cursor: 'default' }}>
                    <span>{'\u{1F4CB} \u5bf9\u8bdd\u6458\u8981'}</span>
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
                    <Avatar name={charName} size={28} />
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
