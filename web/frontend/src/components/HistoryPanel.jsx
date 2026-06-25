import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout } from '../api/client'
import Avatar from './common/Avatar'
import ChatBubble from './common/ChatBubble'
import Loading from './common/Loading'
import ErrorBox from './common/ErrorBox'
import ConfirmModal from './common/ConfirmModal'
import { SkeletonRow } from './common/Skeleton'
import { loadCardAvatar } from '../store/db'
import { Book, Clipboard, Trash2 } from './common/Icon'
import { parseCardJson } from '../utils/card'
import { formatChatTime } from '../utils/time'

function parseCardIds(raw) {
  if (Array.isArray(raw)) return raw
  try { return JSON.parse(raw || '[]') } catch { return [] }
}

const PAGE_SIZE = 20

/* ── Confirm action definitions (single source of truth) ── */
const CONFIRM_ACTION_DEFS = {
  deleteSession: {
    title: '删除会话',
    message: '确定删除该会话？将移入回收站。',
    confirmText: '删除',
  },
  purgeSession: {
    title: '彻底删除',
    message: '确定彻底删除该会话？此操作不可恢复。',
    confirmText: '彻底删除',
  },
  deleteGroup: {
    title: '删除群聊',
    message: '确定删除该群聊？将移入回收站，可后续恢复。',
    confirmText: '删除',
  },
  deleteText: {
    title: '删除书籍',
    message: '确定删除该书籍？将移入回收站。',
    confirmText: '删除',
  },
  clearAll: {
    title: '移入回收站',
    message: '确定将所有历史记录移入回收站？',
    confirmText: '确认',
  },
  purgeTrash: {
    title: '清空回收站',
    message: '确定清空回收站？所有记录将被彻底删除，不可恢复。',
    confirmText: '清空',
  },
  batchDelete: {
    title: '批量删除',
    message: '', // dynamic, provided at call site
    confirmText: '删除',
  },
}

function useDebouncedValue(value, delayMs) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(t)
  }, [value, delayMs])
  return debounced
}

function fmtTime(iso) {
  if (!iso) return '—'
  try { return formatChatTime(iso) } catch { return iso }
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

export default function HistoryPanel({ initialTrash = false }) {
  const texts = useAppStore((s) => s.texts)
  const textProgress = useAppStore((s) => s.textProgress)
  const loadTextProgress = useAppStore((s) => s.loadTextProgress)
  const cards = useAppStore((s) => s.cards)
  const resumeSession = useAppStore((s) => s.resumeSession)
  const resumeLoading = useAppStore((s) => s.resumeLoading)
  const cardAvatars = useAppStore((s) => s.cardAvatars)
  const setCardAvatar = useAppStore((s) => s.setCardAvatar)

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
  const [trashMode, setTrashMode] = useState(initialTrash)
  const [chatTab, setChatTab] = useState('chat') // 'chat' | 'group' | 'books'
  const [groupItems, setGroupItems] = useState([])
  const [groupDetail, setGroupDetail] = useState(null)
  const [groupDetailLoading, setGroupDetailLoading] = useState(false)
  const [textItems, setTextItems] = useState([])
  const [textsLoading, setTextsLoading] = useState(false)
  const [pendingAction, setPendingAction] = useState(null)
  const requestConfirm = (actionType, { message, run } = {}) => {
    const def = CONFIRM_ACTION_DEFS[actionType]
    if (!def) return
    console.log('[HistoryPanel] requestConfirm ->', def.title)
    setPendingAction({
      title: def.title,
      message: message ?? def.message,
      confirmText: def.confirmText,
      danger: true,
      run,
    })
  }
  const runPendingAction = async () => {
    if (!pendingAction) return
    const action = pendingAction
    setPendingAction(null)
    try {
      await action.run()
    } catch (err) {
      console.error('[HistoryPanel] pending action failed:', err)
      setError(err?.message || '操作失败')
    }
  }
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
          const parsed = parseCardJson(c)
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
      if (trashMode) {
        // Trash mode: simple list, no filters/pagination
        const res = await fetchWithTimeout('/api/history/trash')
        const data = await res.json()
        setItems(Array.isArray(data) ? data : [])
        setHasMore(false)
      } else {
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
      }
    } catch (err) {
      console.error('[HistoryPanel] load failed:', err)
      setError(err.message || '加载失败')
    } finally {
      setter(false)
    }
  }, [debouncedKeyword, character, textFilter, trashMode])

  // Reset + load first page on filter change
  useEffect(() => {
    pageRef.current = 1
    abortRef.current = false
    loadPage(1, false)
  }, [loadPage])

  // Load card avatars for session list items
  useEffect(() => {
    const ids = new Set(items.map((it) => it.card_id).filter(Boolean))
    ids.forEach((id) => {
      if (!cardAvatars[id]) {
        loadCardAvatar(id).then((dataUrl) => {
          if (dataUrl) setCardAvatar(id, dataUrl)
        })
      }
    })
  }, [items])

  // Infinite scroll handler (normal mode only)
  useEffect(() => {
    if (trashMode) return
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
  }, [loadingMore, hasMore, loadPage, trashMode])

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
    try {
      await fetchWithTimeout(`/api/history/${sessionId}`, { method: 'DELETE' })
      if (detail?.session?.id === sessionId) setDetail(null)
      setItems((prev) => prev.filter((it) => it.id !== sessionId))
    } catch (err) {
      console.error('[HistoryPanel] delete failed:', err)
      setError(err.message || '删除失败')
    }
  }

  const handleDeleteGroup = async (groupId) => {
    try {
      await fetchWithTimeout(`/api/group/${groupId}`, { method: 'DELETE' })
      setGroupItems((prev) => prev.filter((g) => g.id !== groupId))
      setGroupDetail(null)
    } catch (err) {
      console.error('[HistoryPanel] group delete failed:', err)
      setError(err.message || '删除失败')
    }
  }

  const handleRestore = async (sessionId) => {
    try {
      await fetchWithTimeout(`/api/history/${sessionId}/restore`, { method: 'POST' })
      setItems((prev) => prev.filter((it) => it.id !== sessionId))
    } catch (err) {
      console.error('[HistoryPanel] restore failed:', err)
      setError(err.message || '恢复失败')
    }
  }

  const handlePurge = async (sessionId) => {
    try {
      await fetchWithTimeout(`/api/history/${sessionId}?permanent=true`, { method: 'DELETE' })
      setItems((prev) => prev.filter((it) => it.id !== sessionId))
      if (detail?.session?.id === sessionId) setDetail(null)
    } catch (err) {
      console.error('[HistoryPanel] purge failed:', err)
      setError(err.message || '彻底删除失败')
    }
  }

  const handleContinue = async (sessionId) => {
    try {
      await resumeSession(sessionId)
    } catch (err) {
      console.error('[HistoryPanel] handleContinue failed:', err)
      setError(err?.message || '进入对话失败，请重试')
    }
  }

  const handleClearAll = async () => {
    try {
      await fetchWithTimeout('/api/history/clear-all', { method: 'POST' })
      setItems([])
      setDetail(null)
    } catch (err) {
      console.error('[HistoryPanel] clear-all failed:', err)
      setError(err.message || '操作失败')
    }
  }

  const handlePurgeTrash = async () => {
    try {
      await fetchWithTimeout('/api/history/trash/purge', { method: 'DELETE' })
      setItems([])
    } catch (err) {
      console.error('[HistoryPanel] purge trash failed:', err)
      setError(err.message || '清空回收站失败')
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

  const switchTrashMode = (on) => {
    setTrashMode(on)
    setSelectMode(false)
    setSelectedIds(new Set())
    setDetail(null)
    setError(null)
    if (on) {
      // Load trash immediately
      setLoading(true)
      fetchWithTimeout('/api/history/trash')
        .then((res) => res.json())
        .then((data) => { setItems(Array.isArray(data) ? data : []); setHasMore(false) })
        .catch((err) => setError(err.message || '加载回收站失败'))
        .finally(() => setLoading(false))
    } else {
      setItems([])
      pageRef.current = 1
    }
  }

  const switchTab = (tab) => {
    setChatTab(tab)
    setDetail(null)
    setGroupDetail(null)
    setError(null)
    setTrashMode(false)
    if (tab === 'group') loadGroups()
    if (tab === 'books') loadBooks()
  }

  async function loadGroups() {
    setLoading(true)
    try {
      const res = await fetchWithTimeout('/api/group/list')
      const data = await res.json()
      setGroupItems(data.groups || [])
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  async function openGroupDetail(group) {
    setGroupDetailLoading(true)
    setError(null)
    try {
      const res = await fetchWithTimeout(`/api/group/${group.id}/history`)
      const data = await res.json()
      const cardIds = parseCardIds(group.card_ids)
      setGroupDetail({ ...group, card_ids: cardIds, messages: data.messages || [] })
    } catch (err) {
      setError(err.message)
    } finally {
      setGroupDetailLoading(false)
    }
  }

  const setView = useAppStore((s) => s.setView)
  const setResumeGroupId = useAppStore((s) => s.setResumeGroupId)

  async function loadBooks() {
    setTextsLoading(true)
    try {
      let items = Array.isArray(texts) ? texts : []
      if (items.length === 0) {
        const res = await fetchWithTimeout('/api/text/list')
        items = await res.json()
        items = Array.isArray(items) ? items : []
      }
      setTextItems(items)

      let pMap = textProgress
      if (!pMap || Object.keys(pMap).length === 0) {
        await loadTextProgress()
        pMap = useAppStore.getState().textProgress || {}
      }
    } catch (err) {
      console.error('[HistoryPanel] load books failed:', err)
    } finally {
      setTextsLoading(false)
    }
  }

  const handleDeleteText = async (textId) => {
    try {
      await fetchWithTimeout(`/api/text/${textId}`, { method: 'DELETE' })
      setTextItems((prev) => prev.filter((t) => t.id !== textId))
    } catch (err) {
      console.error('[HistoryPanel] delete text failed:', err)
    }
  }

  const handleResumeGroup = (groupId) => {
    setResumeGroupId(groupId)
    setView('groupChat')
  }

  if (detail) {
    return (
      <HistoryDetail
        data={detail}
        loading={detailLoading}
        resumeLoading={resumeLoading}
        trashMode={trashMode}
        cardAvatars={cardAvatars}
        onBack={() => setDetail(null)}
        onContinue={() => handleContinue(detail.session.id)}
        onDelete={() => requestConfirm(
          trashMode ? 'purgeSession' : 'deleteSession',
          { run: () => trashMode ? handlePurge(detail.session.id) : handleDelete(detail.session.id) },
        )}
        onRestore={() => handleRestore(detail.session.id)}
        onExport={(fmt) => downloadExport(detail.session.id, fmt)}
      />
    )
  }

  if (groupDetail) {
    return (
      <GroupHistoryDetail
        detail={groupDetail}
        loading={groupDetailLoading}
        onBack={() => setGroupDetail(null)}
        onResume={() => handleResumeGroup(groupDetail.id)}
        onDelete={() => requestConfirm('deleteGroup', { run: () => handleDeleteGroup(groupDetail.id) })}
      />
    )
  }

  return (
    <div className="history-panel panel">
      <header className="panel-header">
        <h1 className="panel-title">历史记录</h1>
        <p className="panel-desc">{trashMode ? '回收站 — 已删除的会话' : '搜索、筛选并管理过往对话'}</p>
      </header>

      {/* Tab bar */}
      {!trashMode && (
        <div className="history-tab-bar">
          <button
            type="button"
            className={`history-tab${chatTab === 'chat' ? ' active' : ''}`}
            onClick={() => switchTab('chat')}
          >
            单聊
          </button>
          <button
            type="button"
            className={`history-tab${chatTab === 'group' ? ' active' : ''}`}
            onClick={() => switchTab('group')}
          >
            群聊
          </button>
          <button
            type="button"
            className={`history-tab${chatTab === 'books' ? ' active' : ''}`}
            onClick={() => switchTab('books')}
          >
            书籍
          </button>
        </div>
      )}

      {error && <ErrorBox message={error} onDismiss={() => setError(null)} />}

      {chatTab === 'chat' && (
      <div className="history-toolbar">
        {/* Trash toggle */}
        <button
          type="button"
          className={trashMode ? 'history-back-btn' : 'btn-secondary btn-sm'}
          onClick={() => switchTrashMode(!trashMode)}
        >
          {trashMode ? <><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5m7-7-7 7 7 7"/></svg> 返回列表</> : '回收站'}
        </button>

        {!trashMode && (
          <>
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
                className="btn-ghost-danger"
                onClick={() => requestConfirm('batchDelete', {
                  message: `确定将选中的 ${selectedIds.size} 条会话移入回收站？`,
                  run: () => handleBatchDelete(),
                })}
              >
                <Trash2 size={14} />移入回收站 ({selectedIds.size})
              </button>
            )}
            {!selectMode && items.length > 0 && (
              <button
                type="button"
                className="btn-ghost-danger"
                onClick={() => requestConfirm('clearAll', { run: () => handleClearAll() })}
              >
                <Trash2 size={14} />移入回收站
              </button>
            )}
          </>
        )}

        {trashMode && items.length > 0 && (
          <button
            type="button"
            className="btn-ghost-danger"
            onClick={() => requestConfirm('purgeTrash', { run: () => handlePurgeTrash() })}
          >
            <Trash2 size={14} /> 清空回收站
          </button>
        )}
      </div>
      )}

      {chatTab === 'chat' && (
        <>
      {loading ? (
        <div style={{ padding: '8px 0' }}>
          {[1, 2, 3, 4].map((i) => <SkeletonRow key={i} />)}
        </div>
      ) : items.length === 0 ? (
        <div className="history-empty">
          <svg className="shell-empty-svg" viewBox="0 0 64 64" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="32" cy="32" r="26" />
            <path d="M32 16v16l10 6" />
          </svg>
          <p>{trashMode ? '回收站为空' : '暂无匹配的会话'}</p>
        </div>
      ) : (
        <div className="history-grouped" ref={listRef}>
          {Object.entries(groupedItems).map(([textId, sessionList]) => {
            const text = texts.find((t) => t.id === textId)
            const textName = text?.filename || (textId === '__unknown__' ? '未关联文本' : textId.slice(0, 8))
            return (
              <div key={textId} className="history-group">
                <h3 className="history-group-title" onClick={() => toggleGroup(textId)}>
                  <span className={`history-group-arrow${collapsedGroups[textId] ? ' collapsed' : ''}`}>{collapsedGroups[textId] ? '▶' : '▼'}</span>
                  <Book size={14} /> {textName}
                </h3>
                {!collapsedGroups[textId] && (
                <ul className="history-list">
                  {sessionList.map((it, idx) => (
                    <li key={it.id} className={`history-swipe-wrapper anim-item${selectMode ? ' select-mode' : ''}`} style={{ animationDelay: `${idx * 50}ms` }}>
                      {selectMode && !trashMode && (
                        <input
                          type="checkbox"
                          className="history-checkbox"
                          checked={selectedIds.has(it.id)}
                          onChange={() => toggleSelect(it.id)}
                          onClick={(e) => e.stopPropagation()}
                        />
                      )}
                      <div className="history-swipe-actions">
                        {trashMode ? (
                          <>
                            <button
                              type="button"
                              className="history-swipe-restore"
                              onClick={(e) => {
                                e.stopPropagation()
                                handleRestore(it.id)
                              }}
                            >
                              恢复
                            </button>
                            <button
                              type="button"
                              className="history-swipe-delete"
                              onClick={(e) => {
                                e.stopPropagation()
                                requestConfirm('purgeSession', { run: () => handlePurge(it.id) })
                              }}
                            >
                              彻底删除
                            </button>
                          </>
                        ) : (
                          <button
                            type="button"
                            className="history-swipe-delete"
                            onClick={(e) => {
                              e.stopPropagation()
                              requestConfirm('deleteSession', { run: () => handleDelete(it.id) })
                            }}
                          >
                            移入回收站
                          </button>
                        )}
                      </div>
                      <button
                        type="button"
                        className="history-item"
                        onClick={() => selectMode ? toggleSelect(it.id) : openDetail(it.id)}
                      >
                        <Avatar name={it.character_name || '?'} size={40} src={cardAvatars[it.card_id]} />
                        <div className="history-item-body">
                          <div className="history-item-head">
                            <div className="history-item-name-row">
                              <span className="history-item-name">{it.character_name}</span>
                              {textName && textId !== '__unknown__' && (
                                <span className="history-item-text-label">{textName}</span>
                              )}
                            </div>
                            <span className="history-item-time">
                              {fmtTime(it.last_message_at || it.updated_at)}
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
          {!hasMore && items.length > 0 && !trashMode && (
            <p className="history-end">已加载全部会话</p>
          )}
        </div>
      )}
        </>
      )}

      {chatTab === 'group' && (
        <>
          {loading ? (
            <div style={{ padding: '8px 0' }}>
              {[1, 2, 3, 4].map((i) => <SkeletonRow key={i} />)}
            </div>
          ) : groupItems.length === 0 ? (
            <div className="history-empty">
              <svg className="shell-empty-svg" viewBox="0 0 64 64" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="32" cy="32" r="26" />
                <path d="M32 16v16l10 6" />
              </svg>
              <p>暂无群聊记录</p>
            </div>
          ) : (
            <div className="history-grouped" ref={listRef}>
              {groupItems.map((g) => {
                const cardIds = parseCardIds(g.card_ids)
                return (
                  <div key={g.id} className="history-swipe-wrapper">
                    <div className="history-swipe-actions">
                      <button
                        type="button"
                        className="history-swipe-delete"
                        onClick={(e) => {
                          e.stopPropagation()
                          requestConfirm('deleteGroup', { run: () => handleDeleteGroup(g.id) })
                        }}
                      >
                        删除
                      </button>
                    </div>
                    <button
                      type="button"
                      className="history-item"
                      onClick={() => handleResumeGroup(g.id)}
                      style={{ width: '100%', textAlign: 'left', padding: '14px 16px' }}
                    >
                      <div className="history-item-body">
                        <div className="history-item-head">
                          <span className="history-item-name">{g.name || '未命名群聊'}</span>
                          <span className="history-item-time">{fmtTime(g.created_at)}</span>
                        </div>
                        <p className="history-item-preview">{cardIds.length} 个角色</p>
                      </div>
                    </button>
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}

      {chatTab === 'books' && (
        <>
          {textsLoading ? (
            <div style={{ padding: '8px 0' }}>
              {[1, 2, 3, 4].map((i) => <SkeletonRow key={i} />)}
            </div>
          ) : textItems.length === 0 ? (
            <div className="history-empty">
              <svg className="shell-empty-svg" viewBox="0 0 64 64" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="32" cy="32" r="26" />
                <path d="M32 16v16l10 6" />
              </svg>
              <p>暂无书籍</p>
            </div>
          ) : (
            <div className="history-grouped">
              <ul className="history-list">
                {textItems.map((t) => {
                  const progress = textProgress[t.id]
                  const pct = progress ? Math.round((progress.progress || 0) * 100) : 0
                  return (
                    <li key={t.id} className="history-swipe-wrapper">
                      <div className="history-swipe-actions">
                        <button
                          type="button"
                          className="history-swipe-delete"
                          onClick={(e) => {
                            e.stopPropagation()
                            requestConfirm('deleteText', { run: () => handleDeleteText(t.id) })
                          }}
                        >
                          删除
                        </button>
                      </div>
                      <div className="history-item" style={{ cursor: 'default' }}>
                        <div className="history-item-text-icon"><Book size={20} /></div>
                        <div className="history-item-body">
                          <div className="history-item-head">
                            <span className="history-item-name">{t.title || t.filename || '未命名'}</span>
                            <span className="history-item-time">{t.char_count?.toLocaleString() || '0'} 字</span>
                          </div>
                          <div className="history-item-progress-row">
                            <div className="history-item-progress-bar">
                              <div className="history-item-progress-fill" style={{ width: `${pct}%` }} />
                            </div>
                            <span className="history-item-progress-text">{pct}%</span>
                          </div>
                          <div className="history-item-actions-row">
                            <button
                              type="button"
                              className="btn-primary btn-sm"
                              onClick={() => {
                                useAppStore.getState().setReaderTextId(t.id)
                                setView('reader')
                              }}
                            >
                              阅读
                            </button>
                          </div>
                        </div>
                      </div>
                    </li>
                  )
                })}
              </ul>
            </div>
          )}
        </>
      )}

      <ConfirmModal
        isOpen={!!pendingAction}
        title={pendingAction?.title || ''}
        message={pendingAction?.message || ''}
        confirmText={pendingAction?.confirmText || '确认'}
        onConfirm={runPendingAction}
        onCancel={() => setPendingAction(null)}
        danger={pendingAction?.danger ?? true}
      />
    </div>
  )
}

function GroupHistoryDetail({ detail, loading, onBack, onResume, onDelete }) {
  const messages = detail.messages || []
  const groupName = detail.name || '群聊'

  return (
    <div className="history-panel panel history-detail-view">
      <div className="history-detail-top">
        <button type="button" className="history-back-btn" onClick={onBack}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5m7-7-7 7 7 7"/></svg>
          返回列表
        </button>
        <div className="history-detail-actions">
          <button type="button" className="btn-primary history-action-sm" onClick={onResume}>
            继续群聊
          </button>
          <button
            type="button"
            className="history-action-sm history-action-danger"
            onClick={onDelete}
          >
            <Trash2 size={14} />删除群聊
          </button>
        </div>
      </div>

      <header className="history-detail-header">
        <Avatar name={groupName} size={75} />
        <div className="history-detail-meta-wrap">
          <h2 className="history-detail-name">{groupName}</h2>
          <p className="history-detail-meta">{detail.card_ids?.length || 0} 个角色</p>
        </div>
      </header>

      <p className="history-readonly-hint">只读预览</p>

      {loading ? (
        <Loading text="加载中…" />
      ) : (
        <div className="history-readonly-messages">
          {messages.map((msg, i) => (
            <div key={msg.id ?? i} className="group-msg group-msg-assistant history-readonly-msg">
              <div className="group-msg-speaker">{msg.speaker}</div>
              <div className="group-msg-content">{msg.content}</div>
            </div>
          ))}
          {messages.length === 0 && (
            <p style={{ textAlign: 'center', color: 'var(--text-dim)', padding: 40 }}>暂无消息</p>
          )}
        </div>
      )}
    </div>
  )
}

function HistoryDetail({ data, loading, onBack, onContinue, onDelete, onRestore, onExport, resumeLoading, trashMode, cardAvatars }) {
  const session = data.session || {}
  const messages = data.messages || []
  const charName = session.character_name || '?'

  return (
    <div className="history-panel panel history-detail-view">
      <div className="history-detail-top">
        <button type="button" className="history-back-btn" onClick={onBack}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5m7-7-7 7 7 7"/></svg>
          返回列表
        </button>
        <div className="history-detail-actions">
          {!trashMode && (
            <>
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
                <Trash2 size={14} />移入回收站
              </button>
            </>
          )}
          {trashMode && (
            <>
              <button type="button" className="btn-primary history-action-sm" onClick={onRestore}>
                恢复
              </button>
              <button
                type="button"
                className="history-action-sm history-action-danger"
                onClick={onDelete}
              >
                <Trash2 size={14} />彻底删除
              </button>
            </>
          )}
        </div>
      </div>

      <header className="history-detail-header">
        <Avatar name={charName} size={75} src={cardAvatars?.[session.card_id]} />
        <div className="history-detail-meta-wrap">
          <h2 className="history-detail-name">{charName}</h2>
          {session.user_role && (
            <span className="chat-topbar-user-badge">{session.user_role}</span>
          )}
          <p className="history-detail-meta">
            {fmtTime(session.updated_at || session.created_at)}
          </p>
        </div>
      </header>

      <p className="history-readonly-hint">只读预览</p>

      {loading ? (
        <Loading text="加载中…" />
      ) : (
        <div className="chat-area history-readonly-chat">
          <div className="chat-messages">
          {messages.map((msg, i) => {
            if (msg.role === 'summary') {
              return (
                <div key={msg.id ?? i} className="chat-summary history-readonly-summary">
                  <div className="chat-summary-toggle" style={{ cursor: 'default' }}>
                    <span><Clipboard size={14} /> 对话摘要</span>
                  </div>
                  <div className="chat-summary-body">{msg.content}</div>
                </div>
              )
            }
            const isUser = msg.role === 'user'
            const userInitial = (session.user_role || '我').charAt(0)
            return (
              <ChatBubble
                key={msg.id ?? i}
                className="history-readonly-msg"
                side={isUser ? 'right' : 'left'}
                name={isUser ? undefined : charName}
                avatar={
                  isUser
                    ? <div className="user-avatar-circle" style={session.avatar_data ? { backgroundImage: `url(${session.avatar_data})`, backgroundSize: 'cover', backgroundPosition: 'center' } : {}}>{!session.avatar_data && userInitial}</div>
                    : <Avatar name={charName} size={68} src={cardAvatars?.[session.card_id]} />
                }
                time={msg.created_at ? formatChatTime(msg.created_at) : undefined}
              >
                <span className="chat-bubble-text">{msg.content}</span>
              </ChatBubble>
            )
          })}
          </div>
        </div>
      )}
    </div>
  )
}
