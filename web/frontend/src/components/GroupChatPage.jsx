import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout, postJSON } from '../api/client'
import Avatar from './common/Avatar'
import Loading from './common/Loading'
import ErrorBox from './common/ErrorBox'
import ConfirmModal from './common/ConfirmModal'
import { formatChatTime } from '../utils/time'
import { useMention } from '../utils/useMention'
import MentionDropdown from './common/MentionDropdown'
import ChatHistoryPanel from './common/ChatHistoryPanel'
import { loadCardAvatar } from '../store/db'
import EmojiPicker from './common/EmojiPicker'
import { parseCardJson } from '../utils/card'

function parseCardIds(raw) {
  if (Array.isArray(raw)) return raw
  try { return JSON.parse(raw || '[]') } catch { return [] }
}

/** 通过 card_id 从后端获取单张角色卡信息 */
async function fetchCardById(cardId) {
  try {
    const res = await fetchWithTimeout(`/api/cards/${cardId}`)
    if (!res.ok) return null
    return await res.json()
  } catch { return null }
}

export default function GroupChatPage() {
  const texts = useAppStore((s) => s.texts)
  const cardAvatars = useAppStore((s) => s.cardAvatars)
  const setCardAvatar = useAppStore((s) => s.setCardAvatar)
  const resumeGroupId = useAppStore((s) => s.resumeGroupId)
  const setResumeGroupId = useAppStore((s) => s.setResumeGroupId)
  const userRole = useAppStore((s) => s.userRole)
  const authUser = useAppStore((s) => s.authUser)
  const userAvatar = useAppStore((s) => s.userAvatar)
  const setView = useAppStore((s) => s.setView)
  const viewCard = useAppStore((s) => s.viewCard)
  const [groups, setGroups] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [currentGroup, setCurrentGroup] = useState(null)
  const [messages, setMessages] = useState([])
  const [showCreate, setShowCreate] = useState(false)
  const [sending, setSending] = useState(false)
  const [isMobile, setIsMobile] = useState(window.innerWidth <= 768)
  const [showMembers, setShowMembers] = useState(() => {
    try { return localStorage.getItem('group-members-open') !== 'false' } catch { return true }
  })
  const [editingName, setEditingName] = useState(false)
  const [editNameValue, setEditNameValue] = useState('')
  const toggleMembers = () => {
    setShowMembers(prev => {
      const next = !prev
      try { localStorage.setItem('group-members-open', String(next)) } catch {}
      return next
    })
  }
  const MAX_AUTO_TURNS = 20
  const [autoMode, setAutoMode] = useState(false)
  const [autoRunning, setAutoRunning] = useState(false)
  const autoStopRef = useRef(false)
  const [autoTurn, setAutoTurn] = useState(0)
  const [generatingForName, setGeneratingForName] = useState(null)
  const msgInputRef = useRef(null)
  const messagesAreaRef = useRef(null)
  const [showEmoji, setShowEmoji] = useState(false)
  const [deleteGroupId, setDeleteGroupId] = useState(null)
  const [filterDate, setFilterDate] = useState('')
  const [filterSpeaker, setFilterSpeaker] = useState('')
  const [showFilter, setShowFilter] = useState(false)
  const [selectedCharCardInfo, setSelectedCharCardInfo] = useState(null)
  const charInfoPanelRef = useRef(null)

  // ── History sidebar split layout ──
  const [historyOpen, setHistoryOpen] = useState(false)
  const [splitRatio, setSplitRatio] = useState(0.7)
  const splitContainerRef = useRef(null)

  const onSplitterMouseDown = useCallback((e) => {
    e.preventDefault()
    const container = splitContainerRef.current
    if (!container) return
    const rect = container.getBoundingClientRect()
    const startX = e.clientX
    const startRatio = splitRatio
    const onMove = (ev) => {
      const dx = ev.clientX - startX
      const totalW = rect.width
      let ratio = (totalW * startRatio + dx) / totalW
      ratio = Math.min(0.8, Math.max(0.4, ratio))
      setSplitRatio(ratio)
    }
    const onUp = () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [splitRatio])

  // ── 引用回复 ──
  const [replyTo, setReplyTo] = useState(null) // { id, speaker, preview }

  // ── 角色缓存：按 card_id 索引，替代 allCards ──
  const [allCards, setAllCards] = useState([])
  const [cardCache, setCardCache] = useState({})
  const cardCacheRef = useRef(cardCache)
  cardCacheRef.current = cardCache

  /** 多源解析角色卡信息：优先 cardCache，其次 allCards，最后 _cards */
  function resolveCard(cardId) {
    return cardCache[cardId]
      || allCards.find(c => (c.id || c.card_id) === cardId)
      || currentGroup?._cards?.find(c => (c.id || c.card_id) === cardId)
      || null
  }

  /** 批量加载角色信息到缓存 */
  const ensureCardsLoaded = useCallback(async (cardIds) => {
    const missing = [...new Set(cardIds.filter(id => id && !cardCacheRef.current[id]))]
    if (missing.length === 0) return
    const results = await Promise.allSettled(missing.map(fetchCardById))
    const updates = {}
    results.forEach((r, i) => {
      if (r.status === 'fulfilled' && r.value) {
        const cardData = parseCardJson(r.value)
        updates[missing[i]] = { ...r.value, name: cardData.name || r.value.name || '?' }
      }
    })
    if (Object.keys(updates).length > 0) {
      setCardCache(prev => ({ ...prev, ...updates }))
    }
  }, [])

  // ── @提及（多源解析 card name） ──
  const mentionableItems = useMemo(() => {
    if (!currentGroup?.card_ids) return []
    return currentGroup.card_ids
      .map((id) => resolveCard(id))
      .filter(Boolean)
      .map((c) => ({ id: c.id, name: c.name || c.id }))
  }, [currentGroup?.card_ids, cardCache, allCards])

  const handleMentionSelect = useCallback((item, atPos) => {
    setTargetCardIds((prev) => (prev.includes(item.id) ? prev : [...prev, item.id]))
    if (atPos >= 0) {
      setMessageText((prev) => {
        const cursorAfter = msgInputRef.current?.selectionStart ?? prev.length
        return prev.slice(0, atPos) + '@' + item.name + ' ' + prev.slice(cursorAfter)
      })
    }
    setTimeout(() => msgInputRef.current?.focus(), 0)
  }, [])

  const mentionHook = useMention(mentionableItems, { onSelect: handleMentionSelect, maxResults: 6 })

  // 将 allCards 同步到 cardCache，作为 API 单卡加载的补充
  useEffect(() => {
    if (allCards.length === 0) return
    const updates = {}
    for (const card of allCards) {
      const id = card.id || card.card_id
      if (id && !cardCacheRef.current[id]) {
        const cardData = parseCardJson(card)
        updates[id] = { ...card, name: cardData.name || card.name || '?' }
      }
    }
    if (Object.keys(updates).length > 0) {
      setCardCache(prev => ({ ...prev, ...updates }))
    }
  }, [allCards])

  // ── 历史记录 ──
  const historyFetchSessions = useCallback(async (keyword) => {
    if (!groups) return []
    const otherGroups = groups.filter((g) => g.id !== currentGroup?.id)
    const list = keyword
      ? otherGroups.filter((g) => (g.name || '').toLowerCase().includes(keyword.toLowerCase()))
      : otherGroups
    return list.map((g) => ({
      id: g.id,
      title: g.name || '未命名群聊',
      preview: `共 ${g.card_ids?.length || 0} 个角色`,
      time: g.created_at,
    }))
  }, [groups, currentGroup?.id])

  const historySelectSession = useCallback((session) => {
    const g = groups.find((grp) => grp.id === session.id)
    if (g) enterGroup(g)
  }, [groups, enterGroup])

  // Create form state
  const [groupName, setGroupName] = useState('')
  const [selectedCardIds, setSelectedCardIds] = useState([])
  const [cardsByText, setCardsByText] = useState({})
  const [selectedTextId, setSelectedTextId] = useState('')

  const loadGroups = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetchWithTimeout('/api/group/list')
      const data = await res.json()
      setGroups(data.groups || [])
      // 加载所有群成员的角色信息
      const allIds = new Set()
      ;(data.groups || []).forEach(g => parseCardIds(g.card_ids).forEach(id => allIds.add(id)))
      if (allIds.size > 0) ensureCardsLoaded([...allIds])
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [ensureCardsLoaded])

  const runAutoConversation = useCallback(async () => {
    if (!currentGroup || autoRunning) return
    setAutoRunning(true)
    setAutoTurn(0)
    autoStopRef.current = false

    const cardIds = [...currentGroup.card_ids]
    let turnIndex = 0

    while (!autoStopRef.current) {
      if (turnIndex >= MAX_AUTO_TURNS) break
      const targetId = cardIds[turnIndex % cardIds.length]
      turnIndex++
      setAutoTurn(turnIndex)

      setGeneratingForName(resolveCard(targetId)?.name || '?')
      try {
        await postJSON(`/api/group/${currentGroup.id}/broadcast`, {
          target_card_ids: [targetId],
          message: '__AUTO_CONTINUE__',
          speaker: '__DIRECTOR__',
          auto_mode: true,
        })
        setGeneratingForName(null)
        await loadHistory(currentGroup.id, true)
      } catch (err) {
        setGeneratingForName(null)
        console.error('Auto conversation error:', err)
        break
      }

      await new Promise(r => setTimeout(r, 1500))
    }

    setAutoRunning(false)
    setAutoMode(false)
  }, [currentGroup, autoRunning])

  const stopAutoConversation = useCallback(() => {
    autoStopRef.current = true
    setAutoMode(false)
  }, [])

  useEffect(() => {
    loadGroups()
  }, [loadGroups])

  // 预加载所有文本的角色卡到 allCards，作为 cardCache 的备份数据源
  useEffect(() => {
    if (!texts || texts.length === 0) return
    let cancelled = false
    ;(async () => {
      const grouped = {}
      for (const text of texts) {
        try {
          const res = await fetchWithTimeout(`/api/distill/cards/by-text/${text.id}`)
          const data = await res.json()
          const cards = []
          for (const c of data) {
            const cardData = parseCardJson(c)
            cards.push({ ...c, name: cardData.name || c.name || '?' })
          }
          if (cards.length > 0) grouped[text.id] = cards
        } catch { /* skip failed texts */ }
      }
      if (!cancelled) {
        const flat = Object.values(grouped).flat()
        setAllCards(flat)
      }
    })()
    return () => { cancelled = true }
  }, [texts])

  // Auto-enter group when resuming from HistoryPanel
  useEffect(() => {
    if (resumeGroupId && groups.length > 0) {
      const g = groups.find((grp) => grp.id === resumeGroupId)
      if (g) {
        enterGroup(g)
        setResumeGroupId(null)
      }
    }
  }, [resumeGroupId, groups])

  async function loadHistory(groupId, skipLoading = false) {
    if (!skipLoading) setLoading(true)
    setError(null)
    try {
      const res = await fetchWithTimeout(`/api/group/${groupId}/history`)
      const data = await res.json()
      setMessages(data.messages || [])
    } catch (err) {
      setError(err.message)
    } finally {
      if (!skipLoading) setLoading(false)
    }
  }

  const filteredMessages = useMemo(() => {
    let result = messages
    if (filterDate) {
      result = result.filter(m => {
        const d = m.created_at ? new Date(m.created_at).toISOString().slice(0, 10) : ''
        return d === filterDate
      })
    }
    if (filterSpeaker) {
      result = result.filter(m => m.speaker === filterSpeaker)
    }
    return result
  }, [messages, filterDate, filterSpeaker])

  const uniqueSpeakers = useMemo(() => {
    return [...new Set(messages.filter(m => !m.role || m.role !== 'user').map(m => m.speaker).filter(Boolean))]
  }, [messages])

  function enterGroup(group) {
    const cardIds = parseCardIds(group.card_ids)
    setCurrentGroup({ ...group, card_ids: cardIds })
    loadHistory(group.id)
    ensureCardsLoaded(cardIds)
    const who = userRole || authUser?.username || '用户'
    setSystemMessage(`${who} 加入了群聊`)
  }

  const handleExport = useCallback(() => {
    if (!currentGroup) return
    const names = currentGroup.card_ids.map(id => {
      return cardCache[id]?.name || allCards.find(c => (c.id || c.card_id) === id)?.name || ''
    }).filter(Boolean)
    const header = `群聊: ${currentGroup.name || '未命名群聊'}\n导出时间: ${new Date().toLocaleString('zh-CN')}\n参与角色: ${names.join('、')}\n---\n`
    const body = messages.map(m => {
      const time = m.created_at ? new Date(m.created_at).toLocaleString('zh-CN') : ''
      const speaker = m.speaker || (m.role === 'user' ? '我' : '?')
      return `[${time}] ${speaker}: ${m.content || ''}`
    }).join('\n')
    const blob = new Blob([header + body], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = `群聊_${currentGroup.name || '未命名'}.txt`
    document.body.appendChild(a); a.click()
    document.body.removeChild(a); URL.revokeObjectURL(url)
  }, [currentGroup, messages, cardCache, allCards])

  async function handleRename() {
    const name = editNameValue.trim()
    if (!name || !currentGroup) return
    try {
      await fetchWithTimeout(`/api/group/${currentGroup.id}/rename`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })
      setCurrentGroup(prev => ({ ...prev, name }))
      setGroups(prev => prev.map(g => g.id === currentGroup.id ? { ...g, name } : g))
      setEditingName(false)
    } catch (err) {
      setError(err.message || '重命名失败')
    }
  }

  function startEditing() {
    setEditNameValue(currentGroup?.name || '')
    setEditingName(true)
  }

  const handleDeleteGroup = useCallback(async () => {
    const id = deleteGroupId
    setDeleteGroupId(null)
    if (!id) return
    try {
      await fetchWithTimeout(`/api/group/${id}`, { method: 'DELETE' })
      if (currentGroup?.id === id) {
        setCurrentGroup(null)
        setMessages([])
        setShowMembers(false)
      }
      loadGroups()
    } catch (err) {
      setError(err.message || '删除失败')
    }
  }, [deleteGroupId, currentGroup?.id, loadGroups])

  function backToList() {
    setCurrentGroup(null)
    setMessages([])
    setShowMembers(false)
    loadGroups()
  }

  // ── Create modal ──

  async function openCreate() {
    setShowCreate(true)
    setGroupName('')
    setSelectedCardIds([])
    setSelectedTextId('')
    setError(null)

    // Load cards grouped by text_id
    const grouped = {}
    for (const text of texts) {
      try {
        const res = await fetchWithTimeout(`/api/distill/cards/by-text/${text.id}`)
        const data = await res.json()
        const cards = []
        for (const c of data) {
          const cardData = parseCardJson(c)
          cards.push({ ...c, name: cardData.name || c.name || '?' })
        }
        if (cards.length > 0) grouped[text.id] = cards
      } catch { /* skip failed texts */ }
    }
    setCardsByText(grouped)

    // Auto-select if only one text has cards
    const textIds = Object.keys(grouped)
    if (textIds.length === 1) setSelectedTextId(textIds[0])

    // Flat list for group-list name resolution
    const flat = Object.values(grouped).flat()
    setAllCards(flat)
  }

  function toggleCard(cardId) {
    setSelectedCardIds((prev) =>
      prev.includes(cardId)
        ? prev.filter((id) => id !== cardId)
        : [...prev, cardId],
    )
  }

  async function handleCreate() {
    if (selectedCardIds.length < 2) {
      setError('请至少选择两个角色')
      return
    }
    setSending(true)
    setError(null)
    try {
      const data = await postJSON('/api/group/create', {
        name: groupName,
        card_ids: selectedCardIds,
      })
      setShowCreate(false)
      // Enter the newly created group
      setCurrentGroup({
        id: data.group_id,
        name: data.name,
        card_ids: selectedCardIds,
      })
      // Also add to sidebar list so it appears immediately
      setGroups(prev => [{ id: data.group_id, name: data.name, card_ids: JSON.stringify(selectedCardIds), created_at: new Date().toISOString() }, ...prev])
      setMessages([])
    } catch (err) {
      setError(err.message)
    } finally {
      setSending(false)
    }
  }

  const [systemMessage, setSystemMessage] = useState('')

  // ── Send message ──

  const [messageText, setMessageText] = useState('')
  const [targetCardIds, setTargetCardIds] = useState([])

  async function handleSend() {
    if (!messageText.trim() || targetCardIds.length === 0 || !currentGroup) return

    if (autoRunning) stopAutoConversation()

    setSending(true)
    setError(null)
    const speaker = userRole || authUser?.username || '我'
    try {
      // Broadcast: one director message, all targets reply in parallel
      const data = await postJSON(`/api/group/${currentGroup.id}/broadcast`, {
        target_card_ids: [...targetCardIds],
        message: messageText,
        speaker,
        reply_to_id: replyTo?.id || null,
      })
      // Reload history once after all replies
      await loadHistory(currentGroup.id)
      setMessageText('')
      setTargetCardIds([])
      setReplyTo(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setSending(false)
    }
  }

  async function reactToMessage(messageId, emoji) {
    if (!currentGroup) return
    try {
      await fetchWithTimeout(`/api/group/${currentGroup.id}/message/${messageId}/react`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ emoji }),
      })
      await loadHistory(currentGroup.id)
    } catch (err) {
      console.error('React failed:', err)
    }
  }

  function scrollToMessage(messageId) {
    const el = document.querySelector(`[data-msg-id="${messageId}"]`)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      el.classList.add('msg-highlight-flash')
      setTimeout(() => el.classList.remove('msg-highlight-flash'), 1500)
    }
  }

  // Resolve character names from card_ids for display
  function getCharName(cardId) {
    return resolveCard(cardId)?.name || '?'
  }

  // Load card avatars from cardCache whenever cache is populated
  useEffect(() => {
    const ids = new Set()
    allCards.forEach((c) => { const id = c.id || c.card_id; if (id) ids.add(id) })
    groups.forEach((g) => {
      parseCardIds(g.card_ids).forEach((id) => ids.add(id))
    })
    ids.forEach((id) => {
      if (!cardAvatars[id]) {
        const c = resolveCard(id)
        if (c?.avatar_data) {
          setCardAvatar(id, c.avatar_data)
        } else {
          loadCardAvatar(id).then((dataUrl) => {
            if (dataUrl) setCardAvatar(id, dataUrl)
          })
        }
      }
    })
  }, [allCards, groups, cardCache])

  // Targeted avatar loading when entering a group — reads from cardCache immediately
  useEffect(() => {
    if (!currentGroup?.card_ids) return
    currentGroup.card_ids.forEach((id) => {
      if (!cardAvatars[id] && cardCache[id]?.avatar_data) {
        setCardAvatar(id, cardCache[id].avatar_data)
      }
    })
  }, [currentGroup?.card_ids, cardCache])
  useEffect(() => {
    if (!currentGroup || !currentGroup.card_ids) return
    ;(async () => {
      const cards = []
      for (const cardId of currentGroup.card_ids) {
        const cardData = resolveCard(cardId)
        if (cardData) cards.push(cardData)
      }
      setCurrentGroup((prev) => ({ ...prev, _cards: cards }))
    })()
  }, [currentGroup?.id, allCards, cardCache])

  // Emoji picker outside-click
  useEffect(() => {
    if (!showEmoji) return
    const handler = (e) => {
      if (!e.target.closest('.emoji-picker') && !e.target.closest('[data-emoji-btn]')) {
        setShowEmoji(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showEmoji])

  // Character info panel outside-click
  useEffect(() => {
    if (!selectedCharCardInfo) return
    const handler = (e) => {
      if (!e.target.closest('.group-char-info-panel') && !e.target.closest('.group-chat-bubble-speaker')) {
        setSelectedCharCardInfo(null)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [selectedCharCardInfo])

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth <= 768)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // ── Render ──

  return (
    <div className="panel group-chat-page">
      <div className="messages-layout">
        {/* ── 左栏：群聊列表 ── */}
        <div
          className={`messages-sidebar hide-scrollbar${isMobile && currentGroup ? ' group-sidebar-hidden' : ''}`}
        >
          <div className="messages-sidebar-header">
            <h2 className="messages-sidebar-title">群聊</h2>
            <button type="button" className="btn-sm btn-primary" onClick={openCreate}>
              + 新建
            </button>
          </div>

          {error && <ErrorBox message={error} onDismiss={() => setError(null)} />}

          {loading && !currentGroup && <Loading text="加载中…" />}

          {!loading && groups.length === 0 && (
            <div className="messages-empty-state">
              <span className="messages-empty-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" width="48" height="48">
                  <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" />
                  <circle cx="9" cy="7" r="4" />
                  <path d="M23 21v-2a4 4 0 00-3-3.87" />
                  <path d="M16 3.13a4 4 0 010 7.75" />
                </svg>
              </span>
              <p className="messages-empty-title">还没有群聊</p>
              <p className="messages-empty-desc">创建群聊开始多角色导演模式</p>
            </div>
          )}

          {groups.map((g) => {
            const cardIds = parseCardIds(g.card_ids)
            const names = cardIds
              .map((id) => resolveCard(id)?.name)
              .filter(Boolean)
            const isActive = currentGroup?.id === g.id
            return (
              <div key={g.id} className="messages-conv-item-wrap">
                <button
                  type="button"
                  className={`messages-conv-item${isActive ? ' active' : ''}`}
                  onClick={() => enterGroup(g)}
                >
                  <div className="group-avatar-stack">
                    {cardIds.slice(0, 3).map((id) => (
                      <Avatar key={id} name={resolveCard(id)?.name || '?'}
                        src={cardAvatars[id]} size={36} />
                    ))}
                  </div>
                  <div className="messages-conv-body">
                    <div className="messages-conv-head">
                      <span className="messages-conv-name">{g.name || '未命名群聊'}</span>
                      <span className="messages-conv-time">
                        {formatChatTime(g.created_at)}
                      </span>
                    </div>
                    <p className="messages-conv-preview">{names.join('、')}</p>
                  </div>
                </button>
                <button
                  type="button"
                  className="messages-conv-delete-btn"
                  onClick={(e) => { e.stopPropagation(); setDeleteGroupId(g.id) }}
                  title="删除群聊"
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="3 6 5 6 21 6" />
                    <path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2" />
                  </svg>
                </button>
              </div>
            )
          })}
        </div>

        {/* ── 右栏：聊天区 ── */}
        <div
          className={`messages-chat-area${isMobile && !currentGroup ? ' group-chat-hidden' : ''}`}
        >
          {!currentGroup ? (
            <div className="messages-empty-chat">选择一个群聊或创建新群聊</div>
          ) : (
            <div className="private-chat group-chat-layout">
              {/* Header */}
              <div className="private-chat-header">
                {isMobile && (
                  <button type="button" className="chat-back-btn" onClick={backToList}>
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5m7-7-7 7 7 7"/></svg>
                  </button>
                )}
                {editingName ? (
                  <input
                    className="private-chat-title-input"
                    value={editNameValue}
                    onChange={e => setEditNameValue(e.target.value)}
                    onBlur={handleRename}
                    onKeyDown={e => { if (e.key === 'Enter') handleRename(); if (e.key === 'Escape') setEditingName(false) }}
                    autoFocus
                  />
                ) : (
                  <div className="private-chat-title-wrap">
                    <span className="private-chat-title">{currentGroup.name || '群聊'}</span>
                    <button type="button" className="chat-rename-btn" onClick={startEditing} title="重命名">
                      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                    </button>
                    <button type="button" className="chat-delete-btn" onClick={() => setDeleteGroupId(currentGroup.id)} title="删除群聊">
                      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="3 6 5 6 21 6" />
                        <path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2" />
                      </svg>
                    </button>
                  </div>
                )}
                <span className="group-header-count">{currentGroup.card_ids?.length || 0} 个角色</span>
                {!isMobile && (
                  <button
                    type="button"
                    className="chat-topbar-btn"
                    onClick={() => setHistoryOpen(prev => !prev)}
                    title={historyOpen ? '收起历史' : '历史记录'}
                  >
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="12" cy="12" r="10" />
                      <polyline points="12 6 12 12 16 14" />
                    </svg>
                  </button>
                )}
                {isMobile && (
                  <button
                    type="button"
                    className="chat-topbar-btn"
                    onClick={toggleMembers}
                    title="成员列表"
                  >
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <rect x="3" y="3" width="18" height="18" rx="2"/>
                      <line x1="15" y1="3" x2="15" y2="21"/>
                    </svg>
                  </button>
                )}
                {!isMobile && (
                  <button
                    type="button"
                    className="chat-topbar-btn"
                    onClick={toggleMembers}
                    title={showMembers ? '收起成员' : '展开成员'}
                  >
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="chat-topbar-btn-icon">
                      <rect x="3" y="3" width="18" height="18" rx="2.5" />
                      {showMembers ? (
                        <line x1="15" y1="3" x2="15" y2="21" />
                      ) : (
                        <line x1="15" y1="3" x2="15" y2="21" strokeDasharray="2.5 2.5" opacity="0.3" />
                      )}
                    </svg>
                  </button>
                )}
              </div>

              {/* Filter bar */}
              <div className="group-filter-bar">
                <button type="button" className={`group-filter-toggle${showFilter ? ' active' : ''}`} onClick={() => setShowFilter(!showFilter)}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>
                  筛选
                  {(filterDate || filterSpeaker) && <span className="group-filter-active-dot" />}
                </button>
                {showFilter && (
                  <div className="group-filter-panel">
                    <div className="group-filter-row">
                      <label className="group-filter-label">日期</label>
                      <input type="date" className="group-filter-date-input" value={filterDate} onChange={e => setFilterDate(e.target.value)} />
                    </div>
                    <div className="group-filter-row">
                      <label className="group-filter-label">角色</label>
                      <div className="group-filter-chips">
                        <button type="button" className={`group-filter-chip${!filterSpeaker ? ' active' : ''}`} onClick={() => setFilterSpeaker('')}>全部</button>
                        {uniqueSpeakers.map(name => (
                          <button key={name} type="button" className={`group-filter-chip${filterSpeaker === name ? ' active' : ''}`} onClick={() => setFilterSpeaker(name)}>{name}</button>
                        ))}
                      </div>
                    </div>
                    {(filterDate || filterSpeaker) && (
                      <button type="button" className="group-filter-reset" onClick={() => { setFilterDate(''); setFilterSpeaker('') }}>重置</button>
                    )}
                  </div>
                )}
              </div>

              {/* Messages */}
              <div className="private-chat-body" ref={historyOpen ? splitContainerRef : undefined}>
                <div className="group-chat-messages-area" ref={messagesAreaRef}
                     style={historyOpen ? { flex: splitRatio, minWidth: 0 } : undefined}>
                  {systemMessage && (
                    <div className="messages-time-divider">{systemMessage}</div>
                  )}
                  {filteredMessages.length === 0 && !systemMessage && (
                    <div className="messages-empty-state messages-empty-state--borderless">
                      <p className="messages-empty-title">群聊已创建</p>
                      <p className="messages-empty-desc">选择角色并发送第一条消息</p>
                    </div>
                  )}
                  {filteredMessages.map((m, i) => {
                    const isUser = m.role === 'user'
                    const showTime = i === 0 || (m.created_at && messages[i-1]?.created_at
                      && (new Date(m.created_at) - new Date(messages[i-1].created_at)) > 5 * 60 * 1000)
                    const reactions = m.reactions || []
                    const QUICK_EMOJIS = ['👍','❤️','😂','😮','😢','🔥']
                    return (
                      <div key={m.id || i} data-msg-id={m.id}>
                        {showTime && (
                          <div className="time-divider">{formatChatTime(m.created_at)}</div>
                        )}
                        <div className={`messages-row${isUser ? ' mine' : ' other'}`}>
                          {isUser ? (
                            <>
                              <div className="messages-bubble mine group-msg-bubble">
                                <div className="group-msg-bubble-actions">
                                  <button type="button" className="msg-action-btn" title="引用"
                                    onClick={() => {
                                      setReplyTo({ id: m.id, speaker: '我', preview: m.content?.slice(0, 60) })
                                      msgInputRef.current?.focus()
                                    }}>
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
                                  </button>
                                  <div className="msg-quick-reactions">
                                    {QUICK_EMOJIS.map(e => (
                                      <button key={e} type="button" className="msg-quick-reaction-btn"
                                        onClick={() => reactToMessage(m.id, e)}>{e}</button>
                                    ))}
                                  </div>
                                </div>
                                {m.reply_to_id && m.reply_to_preview && (
                                  <div className="msg-reply-quote" onClick={() => scrollToMessage(m.reply_to_id)}>
                                    <div className="msg-reply-quote-speaker">{m.reply_to_preview.split(':')[0]}</div>
                                    <div className="msg-reply-quote-text">{m.reply_to_preview.split(':').slice(1).join(':')}</div>
                                  </div>
                                )}
                                <span className="messages-msg-text">{m.content}</span>
                                {m.created_at && (
                                  <div className="msg-time msg-time-user">{formatChatTime(m.created_at)}</div>
                                )}
                                {reactions.length > 0 && (
                                  <div className="msg-reactions">
                                    {reactions.map((r, ri) => (
                                      <button key={ri} type="button"
                                        className={`msg-reaction-badge${r.users?.includes(authUser?.id || '') ? ' mine' : ''}`}
                                        onClick={() => reactToMessage(m.id, r.emoji)}>
                                        {r.emoji} {r.count}
                                      </button>
                                    ))}
                                  </div>
                                )}
                              </div>
                              <Avatar name={authUser?.username || '我'} size={40} src={userAvatar} />
                            </>
                          ) : (
                            <>
                              <Avatar name={m.speaker || '?'} size={40} src={cardAvatars[m.card_id || m.speaker_card_id]} />
                              <div>
                                <div className="group-chat-bubble">
                                  <div className="group-chat-bubble-header">
                                    <span className="group-chat-bubble-speaker" style={{ cursor: 'pointer' }} onClick={() => {
                                      const cardId = m.card_id || m.speaker_card_id
                                      let cardData = cardId ? resolveCard(cardId) : null
                                      // Fallback: search by speaker name across all sources
                                      if (!cardData && m.speaker) {
                                        const name = m.speaker.toLowerCase()
                                        cardData = allCards.find(c => (c.name || '').toLowerCase() === name)
                                          || Object.values(cardCache).find(c => (c.name || '').toLowerCase() === name)
                                          || currentGroup?._cards?.find(c => (c.name || '').toLowerCase() === name)
                                          || null
                                      }
                                      const fallbackId = cardData?.id || cardData?.card_id || cardId
                                      if (cardData) {
                                        const parsed = parseCardJson(cardData)
                                        setSelectedCharCardInfo({
                                          cardId: fallbackId,
                                          name: parsed.name || cardData.name || m.speaker || '?',
                                          identity: parsed.identity || '',
                                          personality_traits: parsed.personality_traits || [],
                                          avatar_data: cardData.avatar_data || cardAvatars[fallbackId],
                                          rawCard: cardData,
                                        })
                                      } else {
                                        setSelectedCharCardInfo({
                                          cardId: null,
                                          name: m.speaker || '?',
                                          identity: '',
                                          personality_traits: [],
                                          avatar_data: null,
                                          rawCard: null,
                                        })
                                      }
                                    }}>{m.speaker || '?'}</span>
                                    <div className="group-msg-bubble-actions">
                                      <button type="button" className="msg-action-btn" title="引用"
                                        onClick={() => {
                                          setReplyTo({ id: m.id, speaker: m.speaker, preview: m.content?.slice(0, 60) })
                                          msgInputRef.current?.focus()
                                        }}>
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
                                      </button>
                                      <div className="msg-quick-reactions">
                                        {QUICK_EMOJIS.map(e => (
                                          <button key={e} type="button" className="msg-quick-reaction-btn"
                                            onClick={() => reactToMessage(m.id, e)}>{e}</button>
                                        ))}
                                      </div>
                                    </div>
                                  </div>
                                  <div className="group-chat-bubble-body">
                                    {m.reply_to_id && m.reply_to_preview && (
                                      <div className="msg-reply-quote" onClick={() => scrollToMessage(m.reply_to_id)}>
                                        <div className="msg-reply-quote-speaker">{m.reply_to_preview.split(':')[0]}</div>
                                        <div className="msg-reply-quote-text">{m.reply_to_preview.split(':').slice(1).join(':')}</div>
                                      </div>
                                    )}
                                    <span className="messages-msg-text">{m.content}</span>
                                  </div>
                                  {m.created_at && (
                                    <div className="group-chat-bubble-time">{formatChatTime(m.created_at)}</div>
                                  )}
                                  {reactions.length > 0 && (
                                    <div className="msg-reactions" style={{ padding: '0 12px 6px' }}>
                                      {reactions.map((r, ri) => (
                                        <button key={ri} type="button"
                                          className={`msg-reaction-badge${r.users?.includes(authUser?.id || '') ? ' mine' : ''}`}
                                          onClick={() => reactToMessage(m.id, r.emoji)}>
                                          {r.emoji} {r.count}
                                        </button>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              </div>
                            </>
                          )}
                        </div>
                      </div>
                    )
                  })}
                  {sending && <Loading text="加载中…" />}
                </div>

                {/* 右侧栏：历史记录 + 成员列表 + 角色信息 */}
                {(!isMobile || showMembers || historyOpen) && (
                  <>
                    {historyOpen && <div className="chat-splitter" onMouseDown={onSplitterMouseDown} />}
                    <div className={`group-right-panel${showMembers || historyOpen ? '' : ' collapsed'}${historyOpen ? ' history-sidebar-mode' : ''}`}
                         style={historyOpen ? { flex: 1 - splitRatio, minWidth: 280, maxWidth: '50vw', width: 'auto', transition: 'none' } : undefined}>
                      {selectedCharCardInfo && (
                        <div className="group-char-info-panel">
                          <button type="button" className="group-char-info-close" onClick={() => setSelectedCharCardInfo(null)}>
                          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                        </button>
                        <Avatar name={selectedCharCardInfo.name} size={56} src={selectedCharCardInfo.avatar_data} />
                        <div className="group-char-info-name">{selectedCharCardInfo.name}</div>
                        {selectedCharCardInfo.identity && <div className="group-char-info-identity">{selectedCharCardInfo.identity}</div>}
                        {selectedCharCardInfo.personality_traits?.length > 0 && (
                          <div className="group-char-info-traits">
                            <div className="group-char-info-tag-label">性格特征</div>
                            <div className="group-char-info-tags">
                              {selectedCharCardInfo.personality_traits.slice(0, 3).map((t, i) => (
                                <span key={i} className="group-char-info-tag">{t}</span>
                              ))}
                            </div>
                          </div>
                        )}
                        {selectedCharCardInfo.rawCard ? (
                          <button type="button" className="btn-primary btn-sm" style={{ marginTop: 12, width: '100%' }} onClick={() => {
                            setView('character')
                            viewCard(selectedCharCardInfo.rawCard)
                            setSelectedCharCardInfo(null)
                          }}>
                            查看完整卡片
                          </button>
                        ) : (
                          <span style={{ marginTop: 12, fontSize: 12, color: 'var(--text-dim)' }}>未找到角色卡</span>
                        )}
                      </div>
                    )}
                    <div className="group-right-section">
                      {!historyOpen && <div className="group-right-section-title">历史记录</div>}
                      <ChatHistoryPanel
                        mode={historyOpen ? "sidebar" : "dropdown"}
                        open={historyOpen}
                        onClose={() => setHistoryOpen(false)}
                        fetchSessions={historyFetchSessions}
                        onSelectSession={historySelectSession}
                        placeholder="搜索历史群聊…"
                        onExport={handleExport}
                      />
                    </div>
                    {!historyOpen && <div className="group-right-section-divider" />}
                    {!historyOpen && (
                    <div className="group-right-section">
                      <div className="group-right-section-title">成员 ({currentGroup.card_ids?.length})</div>
                      {currentGroup.card_ids?.map((cardId) => {
                        const card = resolveCard(cardId)
                        let identity = ''
                        if (card) {
                          try {
                            const cj = parseCardJson(card)
                            identity = cj.identity || ''
                          } catch {}
                        }
                        return (
                          <div key={cardId} className="group-member-item">
                            <Avatar name={card?.name || '?'} size={44} src={cardAvatars[cardId]} />
                            <div className="group-member-info">
                              <span className="group-member-name">{card?.name || '?'}</span>
                              {identity && <span className="group-member-identity">{identity}</span>}
                            </div>
                          </div>
                        )
                      })}
                    </div>
                    )}
                  </div>
                </>
                )}
              </div>

              {/* Input */}
              <div className="private-chat-input-bar">
                {autoRunning && (
                  <div className="group-auto-banner">
                    <span>🎬 自动对话中… (第 {autoTurn}/{MAX_AUTO_TURNS} 轮)</span>
                    {generatingForName && <span className="group-auto-generating"> • {generatingForName} 生成中…</span>}
                    <button type="button" className="group-auto-banner-stop" onClick={stopAutoConversation}>
                      停止
                    </button>
                  </div>
                )}
                {replyTo && (
                  <div className="reply-preview-bar">
                    <div className="reply-preview-info">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
                      <span className="reply-preview-label">回复 {replyTo.speaker}:</span>
                      <span className="reply-preview-text">{replyTo.preview}</span>
                    </div>
                    <button type="button" className="reply-preview-close" onClick={() => setReplyTo(null)}>
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                    </button>
                  </div>
                )}
                <div className="group-target-selector">
                  <button
                    type="button"
                    className={`group-target-chip${targetCardIds.length === currentGroup.card_ids?.length ? ' active' : ''}`}
                    onClick={() => setTargetCardIds(
                      targetCardIds.length === currentGroup.card_ids?.length
                        ? []
                        : [...currentGroup.card_ids]
                    )}
                  >
                    全部
                  </button>
                  {currentGroup.card_ids?.map((cardId) => {
                    const card = resolveCard(cardId)
                    const selected = targetCardIds.includes(cardId)
                    return (
                      <button
                        key={cardId}
                        type="button"
                        className={`group-target-chip${selected ? ' active' : ''}`}
                        onClick={() => setTargetCardIds(prev =>
                          prev.includes(cardId)
                            ? prev.filter(id => id !== cardId)
                            : [...prev, cardId]
                        )}
                      >
                        <Avatar name={card?.name || '?'} size={28} src={cardAvatars[cardId]} />
                        {card?.name || '?'}
                      </button>
                    )
                  })}
                </div>
                <div style={{ position: 'relative' }}>
                  {showEmoji && (
                    <EmojiPicker
                      controlled
                      onEmojiSelect={(emoji) => {
                        setMessageText(prev => {
                          const ta = msgInputRef.current
                          if (ta) {
                            const start = ta.selectionStart
                            const newVal = prev.slice(0, start) + emoji + prev.slice(ta.selectionEnd)
                            setTimeout(() => {
                              ta.selectionStart = ta.selectionEnd = start + emoji.length
                              ta.focus()
                            }, 0)
                            return newVal
                          }
                          return prev + emoji
                        })
                        setShowEmoji(false)
                      }}
                    />
                  )}
                  <textarea
                    ref={msgInputRef}
                    className="messages-input"
                    rows={2}
                    placeholder={
                      targetCardIds.length > 0
                        ? `对 ${targetCardIds.map(id => resolveCard(id)?.name || '?').join('、')} 说…`
                        : '请先选择回复目标'
                    }
                    value={messageText}
                    onChange={(e) => {
                      const val = e.target.value
                      setMessageText(val)
                      mentionHook.handleMentionInput(val, e.target.selectionStart, e.target)
                    }}
                    onKeyDown={(e) => {
                      if (mentionHook.handleMentionKeyDown(e)) return
                      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
                    }}
                    disabled={targetCardIds.length === 0 || sending || autoRunning}
                  />
                  <MentionDropdown
                    show={mentionHook.mentionActive}
                    items={mentionHook.mentionItems}
                    selectedIndex={mentionHook.selectedIndex}
                    onSelect={(item) => handleMentionSelect(item, mentionHook.mentionAtPos)}
                    position={mentionHook.mentionPosition}
                  />
                </div>
                <div className="messages-input-toolbar">
                  <div className="messages-input-toolbar-left">
                    <button type="button" data-emoji-btn className="messages-toolbar-btn" title="表情"
                      onClick={() => setShowEmoji(!showEmoji)}>
                      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>
                    </button>
                    <button
                      type="button"
                      className={`group-auto-btn${autoMode ? ' active' : ''}`}
                      onClick={() => {
                        if (autoMode) {
                          stopAutoConversation()
                        } else {
                          setAutoMode(true)
                          runAutoConversation()
                        }
                      }}
                      title={autoMode ? '停止自动对话' : '自动对话模式'}
                    >
                      {autoMode ? (
                        <><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ verticalAlign: -2, marginRight: 3 }}><rect x="6" y="6" width="12" height="12" rx="2"/></svg> 停止</>
                      ) : (
                        <><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ verticalAlign: -2, marginRight: 3 }}><polygon points="5 3 19 12 5 21 5 3" fill="currentColor" opacity="0.9"/></svg> 自动对话</>
                      )}
                    </button>
                  </div>
                  <button
                    type="button"
                    className="messages-send-btn"
                    disabled={targetCardIds.length === 0 || !messageText.trim() || sending}
                    onClick={handleSend}
                  >
                    {sending ? '…' : targetCardIds.length > 1 ? `发送 (${targetCardIds.length})` : '发送'}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── Create modal ── */}
      {showCreate && (
        <div className="modal-overlay" onClick={() => setShowCreate(false)}>
          <div className="modal-card group-create-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-title">创建群聊</div>

            <div className="group-create-section">
              <label className="modal-label">群聊名称（可选）</label>
              <input
                className="modal-input"
                placeholder="输入群聊名称…"
                value={groupName}
                onChange={(e) => setGroupName(e.target.value)}
                maxLength={30}
              />
            </div>

            <div className="group-create-section">
              <label className="modal-label">选择文本</label>
              <div className="group-create-text-tabs">
                {texts.filter((t) => cardsByText[t.id]?.length).length === 0 ? (
                  <p className="group-create-empty">请先在文本管理中蒸馏角色卡</p>
                ) : (
                  texts
                    .filter((t) => cardsByText[t.id]?.length)
                    .map((t) => (
                      <button
                        key={t.id}
                        type="button"
                        className={`group-target-chip${selectedTextId === t.id ? ' active' : ''}`}
                        onClick={() => { setSelectedTextId(t.id); setSelectedCardIds([]) }}
                      >
                        {t.title || t.filename || t.id.slice(0, 8)}
                      </button>
                    ))
                )}
              </div>
            </div>

            {selectedTextId && (
              <div className="group-create-section">
                <label className="modal-label">选择角色（至少 2 个）</label>
                {selectedCardIds.length > 0 && (
                  <div className="group-create-selected-summary">
                    <span>已选 {selectedCardIds.length} 个角色</span>
                    {selectedCardIds.map((id) => {
                      const card = resolveCard(id) || cardsByText[selectedTextId]?.find(c => (c.id || c.card_id) === id)
                      return (
                        <Avatar key={id} name={card?.name || '?'} size={24} src={cardAvatars[id]} />
                      )
                    })}
                  </div>
                )}
                <div className="group-create-card-list">
                  {(cardsByText[selectedTextId] || []).length === 0 && (
                    <p className="group-create-empty">该书暂无角色卡</p>
                  )}
                  {(cardsByText[selectedTextId] || []).map((c) => {
                    const cardId = c.id || c.card_id
                    const selected = selectedCardIds.includes(cardId)
                    let identity = ''
                    try { const cj = parseCardJson(c); identity = cj.identity || '' } catch {}
                    return (
                      <div
                        key={cardId}
                        className={`group-create-card${selected ? ' selected' : ''}`}
                        onClick={() => toggleCard(cardId)}
                      >
                        <Avatar name={c.name} size={36} src={cardAvatars[cardId]} />
                        <div className="group-create-card-info">
                          <span className="group-create-card-name">{c.name}</span>
                          {identity && <span className="group-create-card-identity">{identity}</span>}
                        </div>
                        <div className={`group-create-card-checkbox${selected ? ' checked' : ''}`}>
                          {selected ? '✓' : ''}
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}

            <div className="modal-actions">
              <button type="button" className="btn-secondary" onClick={() => setShowCreate(false)}>取消</button>
              <button type="button" className="btn-primary" onClick={handleCreate}
                disabled={selectedCardIds.length < 2 || sending}>
                {sending ? '创建中…' : `创建 (${selectedCardIds.length})`}
              </button>
            </div>
          </div>
        </div>
      )}

      <ConfirmModal
        isOpen={!!deleteGroupId}
        title="删除群聊"
        message="确定将该群聊移入回收站？消息记录将保留，可后续恢复。"
        confirmText="删除"
        onConfirm={handleDeleteGroup}
        onCancel={() => setDeleteGroupId(null)}
        danger
      />
    </div>
  )
}
