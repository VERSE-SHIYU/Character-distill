import { useCallback, useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout, postJSON } from '../api/client'
import Avatar from './common/Avatar'
import Loading from './common/Loading'
import ErrorBox from './common/ErrorBox'
import { loadCardAvatar } from '../store/db'

function parseCardIds(raw) {
  if (Array.isArray(raw)) return raw
  try { return JSON.parse(raw || '[]') } catch { return [] }
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
  const [groups, setGroups] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [currentGroup, setCurrentGroup] = useState(null)
  const [messages, setMessages] = useState([])
  const [showCreate, setShowCreate] = useState(false)
  const [sending, setSending] = useState(false)
  const [isMobile, setIsMobile] = useState(window.innerWidth <= 768)
  const [showMembers, setShowMembers] = useState(false)
  const MAX_AUTO_TURNS = 20
  const [autoMode, setAutoMode] = useState(false)
  const [autoRunning, setAutoRunning] = useState(false)
  const autoStopRef = useRef(false)

  // Create form state
  const [groupName, setGroupName] = useState('')
  const [allCards, setAllCards] = useState([])
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
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  const runAutoConversation = useCallback(async () => {
    if (!currentGroup || autoRunning) return
    setAutoRunning(true)
    autoStopRef.current = false

    const cardIds = [...currentGroup.card_ids]
    let turnIndex = 0

    while (!autoStopRef.current) {
      if (turnIndex >= MAX_AUTO_TURNS) break
      const targetId = cardIds[turnIndex % cardIds.length]
      turnIndex++

      try {
        await postJSON(`/api/group/${currentGroup.id}/broadcast`, {
          target_card_ids: [targetId],
          message: '__AUTO_CONTINUE__',
          speaker: '__DIRECTOR__',
          auto_mode: true,
        })
        await loadHistory(currentGroup.id)
      } catch (err) {
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

  async function loadHistory(groupId) {
    setLoading(true)
    setError(null)
    try {
      const res = await fetchWithTimeout(`/api/group/${groupId}/history`)
      const data = await res.json()
      setMessages(data.messages || [])
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  function enterGroup(group) {
    const cardIds = parseCardIds(group.card_ids)
    setCurrentGroup({ ...group, card_ids: cardIds })
    loadHistory(group.id)
    const who = userRole || authUser?.username || '用户'
    setSystemMessage(`${who} 加入了群聊`)
  }

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
          const cardData = typeof c.card_json === 'string'
            ? JSON.parse(c.card_json)
            : c.card_json || {}
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
      })
      // Reload history once after all replies
      await loadHistory(currentGroup.id)
      setMessageText('')
      setTargetCardIds([])
    } catch (err) {
      setError(err.message)
    } finally {
      setSending(false)
    }
  }

  // Resolve character names from card_ids for display
  function getCharName(cardId) {
    if (!currentGroup) return '?'
    const card = currentGroup._cards?.find((c) => c.card_id === c.id)
    return card?.name || '?'
  }

  // Load card avatars for the create modal and group list
  useEffect(() => {
    const ids = new Set()
    allCards.forEach((c) => { const id = c.id || c.card_id; if (id) ids.add(id) })
    groups.forEach((g) => {
      parseCardIds(g.card_ids).forEach((id) => ids.add(id))
    })
    ids.forEach((id) => {
      if (!cardAvatars[id]) {
        loadCardAvatar(id).then((dataUrl) => {
          if (dataUrl) setCardAvatar(id, dataUrl)
        })
      }
    })
  }, [allCards, groups])
  useEffect(() => {
    if (!currentGroup || !currentGroup.card_ids) return
    ;(async () => {
      const cards = []
      for (const cardId of currentGroup.card_ids) {
        const cardData = allCards.find((c) => (c.id || c.card_id) === cardId)
        if (cardData) cards.push(cardData)
      }
      setCurrentGroup((prev) => ({ ...prev, _cards: cards }))
    })()
  }, [currentGroup?.id, allCards])

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
              .map((id) => allCards.find((c) => (c.id || c.card_id) === id)?.name)
              .filter(Boolean)
            const isActive = currentGroup?.id === g.id
            return (
              <button
                key={g.id}
                type="button"
                className={`messages-conv-item${isActive ? ' active' : ''}`}
                onClick={() => enterGroup(g)}
              >
                <div className="group-avatar-stack">
                  {cardIds.slice(0, 3).map((id) => (
                    <Avatar key={id} name={allCards.find(c => (c.id || c.card_id) === id)?.name || '?'}
                      src={cardAvatars[id]} size={28} />
                  ))}
                </div>
                <div className="messages-conv-body">
                  <div className="messages-conv-head">
                    <span className="messages-conv-name">{g.name || '未命名群聊'}</span>
                    <span className="messages-conv-time">
                      {new Date(g.created_at.includes('T') && !g.created_at.endsWith('Z') && !g.created_at.includes('+') ? g.created_at + 'Z' : g.created_at).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric' })}
                    </span>
                  </div>
                  <p className="messages-conv-preview">{names.join('、')}</p>
                </div>
              </button>
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
            <div className="private-chat">
              {/* Header */}
              <div className="private-chat-header">
                {isMobile && (
                  <button type="button" className="chat-back-btn" onClick={backToList}>
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5m7-7-7 7 7 7"/></svg>
                  </button>
                )}
                <span className="private-chat-title">{currentGroup.name || '群聊'}</span>
                <span className="group-header-count">{currentGroup.card_ids?.length || 0} 个角色</span>
                <button
                  type="button"
                  className="chat-topbar-btn"
                  onClick={() => setShowMembers(!showMembers)}
                  title="成员列表"
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
                    <circle cx="9" cy="7" r="4"/>
                    <path d="M23 21v-2a4 4 0 0 0-3-3.87"/>
                    <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
                  </svg>
                </button>
              </div>

              {/* Messages */}
              <div className="private-chat-body">
                <div className="group-chat-messages-area">
                  {systemMessage && (
                    <div className="messages-time-divider">{systemMessage}</div>
                  )}
                  {messages.length === 0 && !systemMessage && (
                    <div className="messages-empty-state messages-empty-state--borderless">
                      <p className="messages-empty-title">群聊已创建</p>
                      <p className="messages-empty-desc">选择角色并发送第一条消息</p>
                    </div>
                  )}
                  {messages.map((m, i) => {
                    const isUser = m.role === 'user'
                    return (
                      <div key={m.id || i} className={`messages-row${isUser ? ' mine' : ' other'}`}>
                        {!isUser && (
                          <Avatar name={m.speaker || '?'} size={36} src={cardAvatars[m.card_id || m.speaker_card_id]} />
                        )}
                        <div className={`messages-bubble${isUser ? ' mine' : ' other'}`}>
                          {!isUser && <span className="messages-bubble-speaker">{m.speaker}</span>}
                          <span className="messages-msg-text">{m.content}</span>
                        </div>
                        {isUser && (
                          <Avatar name={authUser?.username || '我'} size={36} src={userAvatar} />
                        )}
                      </div>
                    )
                  })}
                  {sending && <Loading text="加载中…" />}
                </div>

                {/* 成员侧栏 */}
                {showMembers && (
                  <div className="group-members-panel">
                    <div className="group-members-title">成员</div>
                    {currentGroup.card_ids?.map((cardId) => {
                      const card = allCards.find(c => (c.id || c.card_id) === cardId)
                      return (
                        <div key={cardId} className="group-member-item">
                          <Avatar name={card?.name || '?'} size={32} src={cardAvatars[cardId]} />
                          <span className="group-member-name">{card?.name || '?'}</span>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>

              {/* Input */}
              <div className="private-chat-input-bar">
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
                    const card = allCards.find(c => (c.id || c.card_id) === cardId)
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
                        <Avatar name={card?.name || '?'} size={20} src={cardAvatars[cardId]} />
                        {card?.name || '?'}
                      </button>
                    )
                  })}
                </div>
                <textarea
                  className="messages-input"
                  rows={2}
                  placeholder={
                    targetCardIds.length > 0
                      ? `对 ${targetCardIds.map(id => allCards.find(c => (c.id || c.card_id) === id)?.name || '?').join('、')} 说…`
                      : '请先选择回复目标'
                  }
                  value={messageText}
                  onChange={(e) => setMessageText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
                  }}
                  disabled={targetCardIds.length === 0 || sending || autoRunning}
                />
                <div className="messages-input-toolbar">
                  <div className="messages-input-toolbar-left">
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
                      {autoMode ? '⏸ 暂停' : '▶ 自动对话'}
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
                <div className="group-create-card-list">
                  {(cardsByText[selectedTextId] || []).length === 0 && (
                    <p className="group-create-empty">该书暂无角色卡</p>
                  )}
                  {(cardsByText[selectedTextId] || []).map((c) => {
                    const cardId = c.id || c.card_id
                    const selected = selectedCardIds.includes(cardId)
                    return (
                      <div
                        key={cardId}
                        className={`group-create-card${selected ? ' selected' : ''}`}
                        onClick={() => toggleCard(cardId)}
                      >
                        <Avatar name={c.name} size={36} src={cardAvatars[cardId]} />
                        <span className="group-create-card-name">{c.name}</span>
                        <span className="group-create-card-check">{selected ? '✓' : ''}</span>
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
    </div>
  )
}
