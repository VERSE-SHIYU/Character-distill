import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout } from '../api/client'
import Avatar from './common/Avatar'
import Loading from './common/Loading'
import ErrorBox from './common/ErrorBox'
import ConfirmModal from './common/ConfirmModal'
import { loadCardAvatar } from '../store/db'

function parseCardIds(raw) {
  if (Array.isArray(raw)) return raw
  try { return JSON.parse(raw || '[]') } catch { return [] }
}

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
  const [trashMode, setTrashMode] = useState(false)
  const [chatTab, setChatTab] = useState('chat') // 'chat' | 'group'
  const [groupItems, setGroupItems] = useState([])
  const [groupDetail, setGroupDetail] = useState(null)
  const [groupDetailLoading, setGroupDetailLoading] = useState(false)
  const [deleteConfirmId, setDeleteConfirmId] = useState(null)
  const [deleteGroupId, setDeleteGroupId] = useState(null)
  const [purgeConfirmId, setPurgeConfirmId] = useState(null)
  const [clearAllConfirm, setClearAllConfirm] = useState(false)
  const [purgeTrashConfirm, setPurgeTrashConfirm] = useState(false)
  const [batchDeleteConfirm, setBatchDeleteConfirm] = useState(false)
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
    } catch { /* store sets error */ }
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
        onDelete={() => trashMode ? setPurgeConfirmId(detail.session.id) : setDeleteConfirmId(detail.session.id)}
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
      />
    )
  }

  return (
    <div className="history-panel panel">
      <header className="panel-header">
        <h1 className="panel-title">历史记录</h1>
        <p className="panel-desc">{trashMode ? '回收站 — 已删除的会话' : '搜索、筛选并管理过往对话'}</p>
      </header>

      {/* Discover-level tab bar */}
      {!trashMode && (
        <div className="history-tab-bar" style={{ marginBottom: 0, borderBottom: '1px solid var(--glass-border)', paddingBottom: 8 }}>
          <button className="history-tab" onClick={() => setView('market')}>角色市场</button>
          <button className="history-tab active">历史记录</button>
        </div>
      )}

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
        </div>
      )}

      {error && <ErrorBox message={error} onDismiss={() => setError(null)} />}

      {chatTab === 'chat' && (
      <div className="history-toolbar">
        {/* Trash toggle */}
        <button
          type="button"
          className={`btn-secondary btn-sm${trashMode ? ' active' : ''}`}
          onClick={() => switchTrashMode(!trashMode)}
        >
          {trashMode ? '← 返回列表' : '回收站'}
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
                className="btn-danger-sm"
                onClick={() => setBatchDeleteConfirm(true)}
              >
                移入回收站 ({selectedIds.size})
              </button>
            )}
            {!selectMode && items.length > 0 && (
              <button
                type="button"
                className="btn-danger-sm"
                onClick={() => setClearAllConfirm(true)}
              >
                移入回收站
              </button>
            )}
          </>
        )}

        {trashMode && items.length > 0 && (
          <button
            type="button"
            className="btn-danger-sm"
            onClick={() => setPurgeTrashConfirm(true)}
          >
            清空回收站
          </button>
        )}
      </div>
      )}

      {chatTab === 'chat' && (
        <>
      {loading ? (
        <Loading text="加载会话…" />
      ) : items.length === 0 ? (
        <p className="history-empty">{trashMode ? '回收站为空' : '暂无匹配的会话'}</p>
      ) : (
        <div className="history-grouped" ref={listRef}>
          {Object.entries(groupedItems).map(([textId, sessionList]) => {
            const text = texts.find((t) => t.id === textId)
            const textName = text?.filename || (textId === '__unknown__' ? '未关联文本' : textId.slice(0, 8))
            return (
              <div key={textId} className="history-group">
                <h3 className="history-group-title" onClick={() => toggleGroup(textId)}>
                  <span className={`history-group-arrow${collapsedGroups[textId] ? ' collapsed' : ''}`}>{collapsedGroups[textId] ? '▶' : '▼'}</span>
                  {'📖'} {textName}
                </h3>
                {!collapsedGroups[textId] && (
                <ul className="history-list">
                  {sessionList.map((it) => (
                    <li key={it.id} className={`history-swipe-wrapper${selectMode ? ' select-mode' : ''}`}>
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
                                setPurgeConfirmId(it.id)
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
                              setDeleteConfirmId(it.id)
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
            <Loading text="加载群聊…" />
          ) : groupItems.length === 0 ? (
            <p className="history-empty">暂无群聊记录</p>
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
                          setDeleteGroupId(g.id)
                        }}
                      >
                        删除
                      </button>
                    </div>
                    <button
                      type="button"
                      className="history-item"
                      onClick={() => openGroupDetail(g)}
                      style={{ width: '100%', textAlign: 'left', padding: '14px 16px' }}
                    >
                      <div className="history-item-body">
                        <div className="history-item-head">
                          <span className="history-item-name">{g.name || '未命名群聊'}</span>
                          <span className="history-item-time">{formatTime(g.created_at)}</span>
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

      <ConfirmModal
        isOpen={!!deleteConfirmId}
        title="删除会话"
        message="确定删除该会话？将移入回收站。"
        confirmText="删除"
        onConfirm={async () => {
          const id = deleteConfirmId
          setDeleteConfirmId(null)
          await handleDelete(id)
        }}
        onCancel={() => setDeleteConfirmId(null)}
        danger
      />
      <ConfirmModal
        isOpen={!!deleteGroupId}
        title="删除群聊"
        message="确定删除该群聊及其所有消息？此操作不可恢复。"
        confirmText="删除"
        onConfirm={async () => {
          const id = deleteGroupId
          setDeleteGroupId(null)
          await handleDeleteGroup(id)
        }}
        onCancel={() => setDeleteGroupId(null)}
        danger
      />
      <ConfirmModal
        isOpen={!!purgeConfirmId}
        title="彻底删除"
        message="确定彻底删除该会话？此操作不可恢复。"
        confirmText="彻底删除"
        onConfirm={async () => {
          const id = purgeConfirmId
          setPurgeConfirmId(null)
          await handlePurge(id)
        }}
        onCancel={() => setPurgeConfirmId(null)}
        danger
      />
      <ConfirmModal
        isOpen={clearAllConfirm}
        title="移入回收站"
        message="确定将所有历史记录移入回收站？"
        confirmText="确认"
        onConfirm={async () => {
          setClearAllConfirm(false)
          await handleClearAll()
        }}
        onCancel={() => setClearAllConfirm(false)}
        danger
      />
      <ConfirmModal
        isOpen={purgeTrashConfirm}
        title="清空回收站"
        message="确定清空回收站？所有记录将被彻底删除，不可恢复。"
        confirmText="清空"
        onConfirm={async () => {
          setPurgeTrashConfirm(false)
          await handlePurgeTrash()
        }}
        onCancel={() => setPurgeTrashConfirm(false)}
        danger
      />
      <ConfirmModal
        isOpen={batchDeleteConfirm}
        title="批量删除"
        message={`确定将选中的 ${selectedIds.size} 条会话移入回收站？`}
        confirmText="删除"
        onConfirm={async () => {
          setBatchDeleteConfirm(false)
          await handleBatchDelete()
        }}
        onCancel={() => setBatchDeleteConfirm(false)}
        danger
      />
    </div>
  )
}

function GroupHistoryDetail({ detail, loading, onBack, onResume }) {
  const messages = detail.messages || []
  const groupName = detail.name || '群聊'

  return (
    <div className="history-panel panel history-detail-view">
      <div className="history-detail-top">
        <button type="button" className="history-back-btn" onClick={onBack}>
          ← 返回列表
        </button>
        <div className="history-detail-actions">
          <button type="button" className="btn-primary history-action-sm" onClick={onResume}>
            继续群聊
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
          ← 返回列表
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
                移入回收站
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
                彻底删除
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
                    <span>{'📋'} 对话摘要</span>
                  </div>
                  <div className="chat-summary-body">{msg.content}</div>
                </div>
              )
            }
            const isUser = msg.role === 'user'
            const userInitial = (session.user_role || '我').charAt(0)
            return (
              <div
                key={msg.id ?? i}
                className={`chat-msg ${isUser ? 'chat-msg-user' : 'chat-msg-char'} history-readonly-msg`}
              >
                {!isUser ? (
                  <div className="chat-msg-avatar">
                    <Avatar name={charName} size={70} src={cardAvatars?.[session.card_id]} />
                  </div>
                ) : (
                  <div className="user-avatar-circle">{userInitial}</div>
                )}
                <div className={`chat-bubble ${isUser ? 'chat-bubble-user' : 'chat-bubble-char'}`}>
                  <span className="chat-bubble-text">{msg.content}</span>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
