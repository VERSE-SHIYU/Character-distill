import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useAutoScroll } from '../hooks/useAutoScroll'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout, postJSON } from '../api/client'
import Avatar from './common/Avatar'
import Loading from './common/Loading'
import ErrorBox from './common/ErrorBox'
import ConfirmModal from './common/ConfirmModal'
import { formatChatTime } from '../utils/time'
import { checkRepeat } from '../utils/repeatGuard'
import ChatInputBar from './common/ChatInputBar'
import ChatBubble from './common/ChatBubble'
import { Calendar } from './common/ChatHistoryPanel'
import { loadCardAvatar } from '../store/db'
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
  const setPreviousView = useAppStore((s) => s.setPreviousView)
  const clearPreviousView = useAppStore((s) => s.clearPreviousView)
  const setCurrentMarketCardId = useAppStore((s) => s.setCurrentMarketCardId)
  const [groups, setGroups] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [currentGroup, setCurrentGroup] = useState(null)
  const [groupAffinities, setGroupAffinities] = useState({})
  const [messages, setMessages] = useState([])
  const [showCreate, setShowCreate] = useState(false)
  const [sending, setSending] = useState(false)
  const [isMobile, setIsMobile] = useState(window.innerWidth <= 768)
  const [editingName, setEditingName] = useState(false)
  const [editNameValue, setEditNameValue] = useState('')
  const [moreMenuOpen, setMoreMenuOpen] = useState(false)
  const moreMenuRef = useRef(null)
  const [rounds, setRounds] = useState(3)
  const [autoMode, setAutoMode] = useState(false)
  const [autoRunning, setAutoRunning] = useState(false)
  const autoStopRef = useRef(false)
  const autoAbortRef = useRef(null)
  const currentGroupRef = useRef(null)
  const [autoTurn, setAutoTurn] = useState(0)
  const [dropOpen, setDropOpen] = useState(false)
  const dropRef = useRef(null)
  const [autoTotal, setAutoTotal] = useState(0)
  const [targetCardIds, setTargetCardIds] = useState([])
  const [generatingForName, setGeneratingForName] = useState(null)
  const [rightTab, setRightTab] = useState('history') // 'history' | 'members' | 'date'
  const [historySelectedDate, setHistorySelectedDate] = useState('')
  const [historyDateGroups, setHistoryDateGroups] = useState([])
  const messagesAreaRef = useRef(null)
  const bottomRef = useRef(null)
  const inputBarRef = useRef(null)
  const [deleteGroupId, setDeleteGroupId] = useState(null)
  const [filterDate, setFilterDate] = useState('')
  const [filterSpeaker, setFilterSpeaker] = useState('')
  const [showFilter, setShowFilter] = useState(false)
  const [historyFilterMember, setHistoryFilterMember] = useState('')
  const [historyFilterDate, setHistoryFilterDate] = useState('')
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
      } else {
        updates[missing[i]] = { _notFound: true, name: '?' }
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
      .map((c) => ({ id: c.id, name: c.name || c.id, avatar: cardAvatars[c.id] || cardCache[c.id]?.avatar_data || null }))
  }, [currentGroup?.card_ids, cardCache, allCards, cardAvatars])

  const replyingNames = useMemo(() => {
    const ids = targetCardIds.length > 0 ? targetCardIds : (currentGroup?.card_ids || [])
    return ids
      .filter(id => !(currentGroup?.user_persona_type === 'character' && id === currentGroup?.user_persona_card_id))
      .map(id => resolveCard(id)?.name)
      .filter(Boolean)
  }, [targetCardIds, currentGroup?.card_ids, currentGroup?.user_persona_type, currentGroup?.user_persona_card_id, cardCache])

  const { handleScroll } = useAutoScroll(messagesAreaRef, bottomRef, [messages])

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

  // Compute date groups from current group messages for calendar
  useEffect(() => {
    if (!messages.length) return
    const dates = new Set()
    for (const m of messages) {
      if (m.created_at) {
        try { dates.add(new Date(m.created_at).toISOString().slice(0, 10)) } catch {}
      }
    }
    setHistoryDateGroups([...dates].sort().reverse())
  }, [messages])

  // Create form state
  const [groupName, setGroupName] = useState('')
  const [selectedCardIds, setSelectedCardIds] = useState([])
  const [cardsByText, setCardsByText] = useState({})
  const [selectedTextId, setSelectedTextId] = useState('')
  const [personaStep, setPersonaStep] = useState(false)  // Second step: identity selection
  const [personaType, setPersonaType] = useState('director') // 'director' | 'character' | 'stranger'
  const [personaCardId, setPersonaCardId] = useState('')
  const [personaName, setPersonaName] = useState('')
  const [personaDesc, setPersonaDesc] = useState('')

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

    // 没 @ 角色 → 提示拦截
    if (targetCardIds.length === 0) {
      alert('请先 @ 角色，且发言顺序按照角色 @ 顺序')
      return
    }

    setAutoRunning(true)
    setAutoTurn(0)
    autoStopRef.current = false

    const totalTurns = targetCardIds.length * rounds
    setAutoTotal(totalTurns)
    let turnIndex = 0

    while (!autoStopRef.current) {
      if (turnIndex >= totalTurns) break
      const targetId = targetCardIds[turnIndex % targetCardIds.length]
      turnIndex++
      setAutoTurn(turnIndex)

      const ac = new AbortController()
      autoAbortRef.current = ac
      setGeneratingForName(resolveCard(targetId)?.name || '?')
      try {
        await postJSON(`/api/group/${currentGroup.id}/broadcast`, {
          target_card_ids: [targetId],
          message: '__AUTO_CONTINUE__',
          speaker: '__DIRECTOR__',
          auto_mode: true,
        }, undefined, ac.signal)
        setGeneratingForName(null)
        await loadHistory(currentGroup.id, true)
      } catch (err) {
        setGeneratingForName(null)
        setError(err.message || '对话出错，请检查群聊配置')
        break
      }

      // Abortable delay: resolve early when stop is triggered
      await new Promise((resolve) => {
        const timer = setTimeout(resolve, 1500)
        const onAbort = () => { clearTimeout(timer); resolve() }
        ac.signal.addEventListener('abort', onAbort, { once: true })
      })
    }

    // 完整演绎结束后清空 @ 列表，避免下次携带旧角色
    autoAbortRef.current = null
    setAutoRunning(false)
    setAutoMode(false)
  }, [currentGroup, autoRunning, targetCardIds, rounds])

  const stopAutoConversation = useCallback(() => {
    autoStopRef.current = true
    autoAbortRef.current?.abort()
    setAutoMode(false)
  }, [])

  useEffect(() => {
    currentGroupRef.current = currentGroup?.id
  }, [currentGroup?.id])

  useEffect(() => {
    loadGroups()
  }, [loadGroups])

  // Fetch affinities when switching to members tab
  useEffect(() => {
    if (rightTab === 'members' && currentGroup?.id) {
      fetchAffinities(currentGroup.id)
    }
  }, [rightTab, currentGroup?.id])

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
      if (currentGroupRef.current !== groupId) return
      setMessages(data.messages || [])
    } catch (err) {
      if (currentGroupRef.current !== groupId) return
      setError(err.message)
    } finally {
      if (!skipLoading && currentGroupRef.current === groupId) setLoading(false)
    }
  }

  async function fetchAffinities(groupId) {
    if (!groupId) return
    try {
      const res = await fetchWithTimeout(`/api/group/${groupId}/affinities`)
      const data = await res.json()
      if (currentGroupRef.current !== groupId) return
      const map = {}
      ;(data || []).forEach(item => { map[item.card_id] = item })
      setGroupAffinities(map)
    } catch (err) {
      // non-critical; fail silently
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

  const filteredHistoryMessages = useMemo(() => {
    let result = messages
    if (historyFilterDate) {
      result = result.filter(m => {
        const d = m.created_at ? new Date(m.created_at).toISOString().slice(0, 10) : ''
        return d === historyFilterDate
      })
    }
    if (historyFilterMember) {
      result = result.filter(m => m.speaker_card_id === historyFilterMember)
    }
    return result
  }, [messages, historyFilterDate, historyFilterMember])

  function enterGroup(group) {
    let cardIds = parseCardIds(group.card_ids)
    // Exclude played character from AI members
    if (group.user_persona_type === 'character' && group.user_persona_card_id) {
      cardIds = cardIds.filter(id => id !== group.user_persona_card_id)
    }
    // AI count check by persona mode
    const aiCount = cardIds.length
    if (group.user_persona_type === 'director' && aiCount < 2) {
      setError('导演模式需要至少2个AI角色')
      return
    }
    if (aiCount < 1) {
      setError('至少需要1个AI角色陪你对话')
      return
    }
    autoAbortRef.current?.abort()
    setCurrentGroup({ ...group, card_ids: cardIds })
    loadHistory(group.id)
    fetchAffinities(group.id)
    ensureCardsLoaded(cardIds)
    const personaName = (group.user_persona_type === 'character' || group.user_persona_type === 'stranger')
      ? (group.user_persona_name || '角色')
      : null
    const who = personaName || userRole || authUser?.username || '用户'
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
      }
      loadGroups()
    } catch (err) {
      setError(err.message || '删除失败')
    }
  }, [deleteGroupId, currentGroup?.id, loadGroups])

  function backToList() {
    setCurrentGroup(null)
    setMessages([])
    loadGroups()
  }

  // ── Create modal ──

  async function openCreate() {
    setShowCreate(true)
    setGroupName('')
    setSelectedCardIds([])
    setSelectedTextId('')
    setError(null)
    setPersonaStep(false)
    setPersonaType('director')
    setPersonaCardId('')
    setPersonaName('')
    setPersonaDesc('')

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
    // AI count check by persona mode
    const aiCardCount = personaType === 'character' && personaCardId
      ? selectedCardIds.filter(id => id !== personaCardId).length
      : selectedCardIds.length
    if (personaType === 'director' && aiCardCount < 2) {
      setError('导演模式需要至少2个AI角色')
      return
    }
    if (aiCardCount < 1) {
      setError('至少需要1个AI角色陪你对话')
      return
    }
    setSending(true)
    setError(null)
    try {
      const body = {
        name: groupName,
        card_ids: selectedCardIds,
        user_persona_type: personaType,
        user_persona_card_id: personaCardId,
        user_persona_name: personaName.trim(),
        user_persona_desc: personaDesc.trim(),
      }
      const data = await postJSON('/api/group/create', body)
      setShowCreate(false)
      // Reset create form
      setPersonaStep(false)
      setPersonaType('director')
      setPersonaCardId('')
      setPersonaName('')
      setPersonaDesc('')
      // Enter the newly created group — exclude played character from AI members
      const aiCardIds = personaType === 'character' && personaCardId
        ? selectedCardIds.filter(id => id !== personaCardId)
        : selectedCardIds
      setCurrentGroup({
        id: data.group_id,
        name: data.name,
        card_ids: aiCardIds,
        user_persona_type: data.user_persona_type,
        user_persona_card_id: data.user_persona_card_id,
        user_persona_name: data.user_persona_name,
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

  async function handleSend(textArg) {
    const content = (textArg ?? messageText).trim()
    if (!content || !currentGroup) return
    const targets = targetCardIds.length > 0 ? targetCardIds : currentGroup.card_ids
    if (targets.length === 0) return

    // ★ 重复消息拦截
    const { blocked, message: blockMsg } = checkRepeat(content, messages)
    if (blocked) {
      setError(blockMsg)
      return
    }

    if (autoRunning) stopAutoConversation()

    const sendGroupId = currentGroup.id
    setSending(true)
    setError(null)
    const personaSpeaker = (currentGroup?.user_persona_type === 'character' && currentGroup?.user_persona_name)
      ? currentGroup.user_persona_name
      : (currentGroup?.user_persona_type === 'stranger' && currentGroup?.user_persona_name)
        ? currentGroup.user_persona_name
        : null
    const speaker = personaSpeaker || userRole || authUser?.username || '我'
    setMessages(prev => [...prev, {
      id: `optimistic-${Date.now()}`,
      role: 'user',
      speaker,
      content,
      created_at: new Date().toISOString(),
    }])
    setMessageText('')
    try {
      // Broadcast: one director message, all targets reply in parallel
      const data = await postJSON(`/api/group/${currentGroup.id}/broadcast`, {
        target_card_ids: [...targets],
        message: content,
        speaker,
        reply_to_id: replyTo?.id || null,
      })
      void data
      if (currentGroupRef.current !== sendGroupId) return
      // Reload history once after all replies
      await loadHistory(sendGroupId)
      fetchAffinities(sendGroupId)
      setTargetCardIds([])
      setReplyTo(null)
    } catch (err) {
      if (currentGroupRef.current !== sendGroupId) return
      setError(err.message)
    } finally {
      if (currentGroupRef.current === sendGroupId) setSending(false)
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

  function reactionUsersLabel(users) {
    if (!users || users.length === 0) return ''
    return [...new Set(users)].map(uid => {
      if (uid === authUser?.id) return '你'
      if (uid?.startsWith?.('char:')) return resolveCard(uid.slice(5))?.name || '角色' // 未来角色点赞
      return '其他用户'
    }).join('、')
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

  // Character info panel outside-click
  useEffect(() => {
    if (!selectedCharCardInfo) return
    const handler = (e) => {
      if (!e.target.closest('.group-char-info-panel') && !e.target.closest('.cbubble-name')) {
        setSelectedCharCardInfo(null)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [selectedCharCardInfo])

  // More menu outside-click
  useEffect(() => {
    if (!moreMenuOpen) return
    const handler = (e) => {
      if (moreMenuRef.current && !moreMenuRef.current.contains(e.target)) {
        setMoreMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [moreMenuOpen])

  // Round dropdown outside-click
  useEffect(() => {
    if (!dropOpen) return
    const handler = (e) => {
      if (dropRef.current && !dropRef.current.contains(e.target)) {
        setDropOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [dropOpen])

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
            <div className={`private-chat group-chat-layout`} ref={historyOpen ? splitContainerRef : undefined}>
              <div className="group-chat-main" style={historyOpen ? { flex: splitRatio, minWidth: 0 } : undefined}>
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
                  <div className="group-header-left">
                    <span className="private-chat-title" style={{ cursor: 'pointer' }} title="点击修改群名" onClick={startEditing}>{currentGroup.name || '群聊'}</span>
                    <div className="group-avatar-stack">
                      {currentGroup.card_ids?.slice(0, 5).map(id => (
                        <Avatar key={id} name={resolveCard(id)?.name || '?'} size={22} src={cardAvatars[id]} />
                      ))}
                    </div>
                    <span className="group-header-count">{currentGroup.card_ids?.length || 0} 个角色</span>
                  </div>
                )}
                {!editingName && (
                  <div className="group-header-right">
                    <div className={`group-round-dropdown${autoMode ? ' hidden' : ''}`} ref={dropRef}>
                      <button className={`group-round-trigger${dropOpen ? ' open' : ''}`}
                        onClick={() => setDropOpen(!dropOpen)}
                      >
                        <span>{rounds}轮</span>
                        <svg className="drop-chevron" width="12" height="12" viewBox="0 0 12 12" fill="none">
                          <path d="M3 5l3 3 3-3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                        </svg>
                      </button>
                      {dropOpen && (
                        <div className="group-round-menu">
                          {[1, 3, 5, 10, 20].map(n => (
                            <div key={n}
                              className={`group-round-item${rounds === n ? ' active' : ''}`}
                              onClick={() => { setRounds(n); setDropOpen(false) }}
                            >
                              {n}轮
                              {rounds === n && <span className="group-round-check">✓</span>}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                    <button
                      type="button"
                      className={`chat-topbar-btn group-auto-btn-header${autoMode ? ' active' : ''}`}
                      onClick={() => {
                        if (autoMode) {
                          stopAutoConversation()
                        } else {
                          setAutoMode(true)
                          runAutoConversation()
                        }
                      }}
                      title={autoMode ? '停止自动对话' : '自动对话'}
                    >
                      {autoMode ? (
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
                      ) : (
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polygon points="5 3 19 12 5 21 5 3" fill="currentColor" opacity="0.9"/></svg>
                      )}
                    </button>
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
                    <div className="group-more-menu" ref={moreMenuRef}>
                      <button
                        type="button"
                        className="chat-topbar-btn"
                        onClick={() => setMoreMenuOpen(prev => !prev)}
                        title="更多"
                      >
                        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <circle cx="12" cy="12" r="1" />
                          <circle cx="19" cy="12" r="1" />
                          <circle cx="5" cy="12" r="1" />
                        </svg>
                      </button>
                      {moreMenuOpen && (
                        <div className="group-more-menu-dropdown">
                          <button type="button" className="group-more-menu-item" onClick={() => { setMoreMenuOpen(false); startEditing() }}>
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                            重命名
                          </button>
                          <button type="button" className="group-more-menu-item" onClick={() => { setMoreMenuOpen(false); handleExport() }}>
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                            导出对话
                          </button>
                          <button type="button" className="group-more-menu-item group-more-menu-item-danger" onClick={() => { setMoreMenuOpen(false); setDeleteGroupId(currentGroup.id) }}>
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
                            删除群聊
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
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

              {/* 聊天区内错误提示（发消息/rebuild 失败时可见） */}
              {error && <ErrorBox message={error} onDismiss={() => setError(null)} />}

              {/* Messages */}
              <div className="private-chat-body">
                <div className="group-chat-messages-area" ref={messagesAreaRef} onScroll={handleScroll}>
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
                        {(() => {
                          const personaSpeaker = (currentGroup?.user_persona_type === 'character' && currentGroup?.user_persona_name)
                            ? currentGroup.user_persona_name
                            : (currentGroup?.user_persona_type === 'stranger' && currentGroup?.user_persona_name)
                              ? currentGroup.user_persona_name
                              : null
                          const replySpeaker = personaSpeaker || '我'
                          return (
                        <div className={`messages-row${isUser ? ' mine' : ' other'}`}>
                          {isUser ? (
                            currentGroup?.user_persona_type === 'director' ? (
                              <div className="narration-note">{m.content}</div>
                            ) : (
                            <ChatBubble
                              side="right"
                              avatar={<Avatar name={personaSpeaker || authUser?.username || '我'} size={48} src={userAvatar} />}
                              name={personaSpeaker || undefined}
                              time={m.created_at ? formatChatTime(m.created_at) : undefined}
                            >
                              <div className="group-msg-bubble-actions">
                                <button type="button" className="msg-action-btn" title="引用"
                                  onClick={() => {
                                    setReplyTo({ id: m.id, speaker: replySpeaker, preview: m.content?.slice(0, 60) })
                                    inputBarRef.current?.focus()
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
                              {reactions.length > 0 && (
                                <div className="msg-reactions">
                                  {reactions.map((r, ri) => (
                                    <button key={ri} type="button"
                                      className={`msg-reaction-badge${r.users?.includes(authUser?.id || '') ? ' mine' : ''}`}
                                      title={reactionUsersLabel(r.users)}
                                      onClick={() => reactToMessage(m.id, r.emoji)}>
                                      {r.emoji} {r.count}
                                    </button>
                                  ))}
                                </div>
                              )}
                            </ChatBubble>
                          )
                          ) : m.role === 'silent' ? (
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '4px 0' }}>
                              <Avatar name={m.speaker || '?'} size={32} src={cardAvatars[m.card_id || m.speaker_card_id]} />
                              <span className="retracted-text" style={{ fontSize: '12px' }}>（{m.speaker || '?'} 暂时不想说话）</span>
                            </div>
                          ) : (
                            <>
                              <ChatBubble
                                side="left"
                                avatar={<Avatar name={m.speaker || '?'} size={48} src={cardAvatars[m.card_id || m.speaker_card_id]} />}
                                name={m.speaker || '?'}
                                onNameClick={() => {
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
                                }}
                                time={m.created_at ? formatChatTime(m.created_at) : undefined}
                              >
                                <div className="group-msg-bubble-actions group-msg-bubble-actions--other">
                                  <button type="button" className="msg-action-btn" title="引用"
                                    onClick={() => {
                                      setReplyTo({ id: m.id, speaker: m.speaker, preview: m.content?.slice(0, 60) })
                                      inputBarRef.current?.focus()
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
                                {reactions.length > 0 && (
                                  <div className="msg-reactions" style={{ padding: '0 12px 6px' }}>
                                    {reactions.map((r, ri) => (
                                      <button key={ri} type="button"
                                        className={`msg-reaction-badge${r.users?.includes(authUser?.id || '') ? ' mine' : ''}`}
                                        title={reactionUsersLabel(r.users)}
                                        onClick={() => reactToMessage(m.id, r.emoji)}>
                                        {r.emoji} {r.count}
                                      </button>
                                    ))}
                                  </div>
                                )}
                              </ChatBubble>
                            </>
                          )}
                        </div>
                      )})()}
                      </div>
                    )
                  })}
                  {sending && <Loading text={replyingNames.length > 0 ? `${replyingNames.join('、')} 正在输入…` : '角色正在输入…'} />}
                  <div ref={bottomRef} />
                </div>
              </div>



              {/* Input */}
              <ChatInputBar
                ref={inputBarRef}
                value={messageText}
                onChange={setMessageText}
                onSend={handleSend}
                disabled={sending || autoRunning}
                sending={sending}
                placeholder={currentGroup?.user_persona_type === 'director' ? '描述此刻发生了什么…（例：窗外下起了雨）' : '输入消息…（@指定角色）'}
                mentionableItems={currentGroup?.user_persona_type === 'director' ? [] : mentionableItems}
                onMention={(item) => setTargetCardIds((prev) => (prev.includes(item.id) ? prev : [...prev, item.id]))}
                replyTo={replyTo}
                onCancelReply={() => setReplyTo(null)}
                topSlot={autoRunning ? (
                  <div className="group-auto-banner">
                    <span>🎬 自动对话中… (第 {autoTurn}/{autoTotal} 轮)</span>
                    {generatingForName && <span className="group-auto-generating"> • {generatingForName} 生成中…</span>}
                    <button type="button" className="group-auto-banner-stop" onClick={stopAutoConversation}>
                      停止
                    </button>
                  </div>
                ) : null}
              />
            </div>
              {/* 右侧栏：tab 切换 — 历史记录 / 成员 */}
              {historyOpen && (
                <>
                  <div className="chat-splitter" onMouseDown={onSplitterMouseDown} />
                  <div className="group-right-panel history-sidebar-mode"
                       style={{ flex: 1 - splitRatio, minWidth: 280, maxWidth: '50vw', width: 'auto', transition: 'none' }}>
                    {/* Tab bar */}
                    <div className="group-right-tab-bar">
                      <button
                        type="button"
                        className={`group-right-tab${rightTab === 'members' ? ' active' : ''}`}
                        onClick={() => setRightTab('members')}
                      >成员 ({currentGroup.card_ids?.length})</button>
                      <button
                        type="button"
                        className={`group-right-tab${rightTab === 'history' ? ' active' : ''}`}
                        onClick={() => setRightTab('history')}
                      >历史</button>
                      <button
                        type="button"
                        className={`group-right-tab${rightTab === 'date' ? ' active' : ''}`}
                        onClick={() => setRightTab('date')}
                      >日期</button>
                      <div className="group-right-tab-spacer" />
                      <button type="button" className="group-right-tab-btn" onClick={() => setHistoryOpen(false)} title="关闭">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                      </button>
                    </div>

                    {/* Character info overlay */}
                    {selectedCharCardInfo && (
                      <div className="group-char-info-overlay">
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
                            const raw = selectedCharCardInfo.rawCard
                            setCurrentMarketCardId(raw?.id || raw?.card_id)
                            setPreviousView('groupChat', { groupId: currentGroup?.id })
                            setView('marketCardDetail')
                            setSelectedCharCardInfo(null)
                          }}>
                            查看完整卡片
                          </button>
                        ) : (
                          <span style={{ marginTop: 12, fontSize: 12, color: 'var(--text-dim)' }}>未找到角色卡</span>
                        )}
                      </div>
                    )}

                    {/* Tab content */}
                    {rightTab === 'date' ? (
                      <div className="group-right-tab-content">
                        <Calendar
                          dateGroups={historyDateGroups}
                          selectedDate={historySelectedDate}
                          onSelectDate={(iso) => {
                            setHistorySelectedDate(iso)
                            setHistoryFilterDate(iso || '')
                            if (iso) setRightTab('history')
                          }}
                        />
                      </div>
                    ) : rightTab === 'history' ? (
                      <div className="group-right-tab-content">
                        {/* Filter indicator */}
                        {(historyFilterMember || historyFilterDate) && (
                          <div className="group-history-filter-bar">
                            <span className="group-history-filter-label">筛选：</span>
                            {historyFilterMember && (() => {
                              const card = resolveCard(historyFilterMember)
                              return (
                                <span className="group-history-filter-chip">
                                  {card?.name || historyFilterMember}
                                  <button type="button" className="group-history-filter-chip-x" onClick={() => setHistoryFilterMember('')}>
                                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                                  </button>
                                </span>
                              )
                            })()}
                            {historyFilterDate && (
                              <span className="group-history-filter-chip">
                                {historyFilterDate}
                                <button type="button" className="group-history-filter-chip-x" onClick={() => setHistoryFilterDate('')}>
                                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                                </button>
                              </span>
                            )}
                            <button type="button" className="group-history-filter-clear" onClick={() => { setHistoryFilterMember(''); setHistoryFilterDate('') }}>清除</button>
                          </div>
                        )}
                        {/* Message list */}
                        {filteredHistoryMessages.length === 0 ? (
                          <div className="group-history-empty">暂无消息</div>
                        ) : (
                          <div className="group-history-list">
                            {filteredHistoryMessages.map((m, i) => {
                              const cardId = m.card_id || m.speaker_card_id
                              const card = cardId ? resolveCard(cardId) : null
                              const speakerName = card?.name || m.speaker || '?'
                              return (
                                <div key={m.id || i} className="group-history-item">
                                  <Avatar name={speakerName} size={28} src={cardAvatars[cardId]} />
                                  <div className="group-history-item-body">
                                    <div className="group-history-item-head">
                                      <span className="group-history-item-speaker">{speakerName}</span>
                                      <span className="group-history-item-time">{m.created_at ? formatChatTime(m.created_at) : ''}</span>
                                    </div>
                                    <p className="group-history-item-text">{m.content}</p>
                                  </div>
                                </div>
                              )
                            })}
                          </div>
                        )}
                      </div>
                    ) : (
                      <div className="group-right-tab-content">
                        <div className="group-right-member-list">
                          {currentGroup.card_ids?.map((cardId) => {
                            const card = resolveCard(cardId)
                            let identity = ''
                            if (card) {
                              try {
                                const cj = parseCardJson(card)
                                identity = cj.identity || ''
                              } catch {}
                            }
                            const handleMemberClick = () => {
                              if (!card) return
                              setCurrentMarketCardId(card.id || card.card_id)
                              setPreviousView('groupChat', { groupId: currentGroup?.id })
                              setView('marketCardDetail')
                            }
                            return (
                              <div key={cardId} className="group-member-item" style={{ cursor: 'pointer' }} onClick={handleMemberClick}>
                                <Avatar name={card?.name || '?'} size={44} src={cardAvatars[cardId]} />
                                <div className="group-member-info">
                                  <span className="group-member-name">{card?.name || '?'}</span>
                                  {identity && <span className="group-member-identity">{identity}</span>}
                                  {groupAffinities[cardId] && (
                                    <span className="group-member-affinity">
                                      {groupAffinities[cardId].stage_emoji} {groupAffinities[cardId].stage_name} · {groupAffinities[cardId].affinity}
                                    </span>
                                  )}
                                </div>
                                <button
                                  type="button"
                                  className="group-member-filter-btn"
                                  title="查看TA的发言"
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    setHistoryFilterMember(cardId)
                                    setRightTab('history')
                                  }}
                                >
                                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                                </button>
                              </div>
                            )
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                </>
              )}
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

            {selectedTextId && !personaStep && (
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

            {/* Step 2: Persona identity selection */}
            {selectedTextId && personaStep && (
              <div className="group-create-section">
                <label className="modal-label">你以谁的身份参与？</label>

                <div className="persona-options">
                  <div
                    className={`persona-option${personaType === 'director' ? ' active' : ''}`}
                    onClick={() => setPersonaType('director')}
                  >
                    <div className="persona-radio" />
                    <div>
                      <div className="persona-option-title">导演 / 旁白者</div>
                      <div className="persona-option-desc">AI 角色不知道你是谁，默认模式</div>
                    </div>
                  </div>

                  <div
                    className={`persona-option${personaType === 'character' ? ' active' : ''}`}
                    onClick={() => setPersonaType('character')}
                  >
                    <div className="persona-radio" />
                    <div>
                      <div className="persona-option-title">扮演已选角色</div>
                      <div className="persona-option-desc">选一个角色亲自扮演，AI 将把你当作该角色</div>
                    </div>
                  </div>
                  {personaType === 'character' && (
                    <div className="persona-sub">
                      {selectedCardIds.map((id) => {
                        const card = resolveCard(id) || cardsByText[selectedTextId]?.find(c => (c.id || c.card_id) === id)
                        const name = card?.name || id
                        return (
                          <button
                            key={id}
                            type="button"
                            className={`persona-char-btn${personaCardId === id ? ' active' : ''}`}
                            onClick={() => setPersonaCardId(id)}
                          >
                            <Avatar name={name} size={28} src={cardAvatars[id]} />
                            <span>{name}</span>
                          </button>
                        )
                      })}
                    </div>
                  )}

                  <div
                    className={`persona-option${personaType === 'stranger' ? ' active' : ''}`}
                    onClick={() => setPersonaType('stranger')}
                  >
                    <div className="persona-radio" />
                    <div>
                      <div className="persona-option-title">路人身份</div>
                      <div className="persona-option-desc">以一个新角色的身份加入，AI 角色不认识你</div>
                    </div>
                  </div>
                  {personaType === 'stranger' && (
                    <div className="persona-sub">
                      <input
                        className="modal-input"
                        placeholder="你的名字（必填）"
                        value={personaName}
                        onChange={(e) => setPersonaName(e.target.value)}
                        maxLength={20}
                      />
                      <input
                        className="modal-input"
                        placeholder={'一句话描述身份，如"路过的记者"（选填）'}
                        value={personaDesc}
                        onChange={(e) => setPersonaDesc(e.target.value)}
                        maxLength={50}
                      />
                    </div>
                  )}
                </div>
              </div>
            )}

            <div className="modal-actions">
              <button type="button" className="btn-secondary" onClick={() => {
                if (personaStep) { setPersonaStep(false); setPersonaType('director'); setPersonaCardId(''); setPersonaName(''); setPersonaDesc('') }
                else setShowCreate(false)
              }}>{personaStep ? '上一步' : '取消'}</button>
              {!personaStep ? (
                <button type="button" className="btn-primary" onClick={() => {
                  if (selectedCardIds.length < 2) { setError('请至少选择两个角色'); return }
                  setPersonaStep(true)
                  setError(null)
                }}
                  disabled={selectedCardIds.length < 2}>
                  下一步
                </button>
              ) : (
                <button type="button" className="btn-primary" onClick={handleCreate}
                  disabled={sending || (personaType === 'stranger' && !personaName.trim())}>
                  {sending ? '创建中…' : '创建群聊'}
                </button>
              )}
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
