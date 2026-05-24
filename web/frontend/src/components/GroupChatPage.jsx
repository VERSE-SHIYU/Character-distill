import { useCallback, useEffect, useState } from 'react'
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
  const setView = useAppStore((s) => s.setView)
  const [groups, setGroups] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [currentGroup, setCurrentGroup] = useState(null)
  const [messages, setMessages] = useState([])
  const [showCreate, setShowCreate] = useState(false)
  const [sending, setSending] = useState(false)

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

  async function handleSend(e) {
    e.preventDefault()
    if (!messageText.trim() || targetCardIds.length === 0 || !currentGroup) return

    setSending(true)
    setError(null)
    const speaker = userRole || authUser?.username || '我'
    try {
      // Send to each selected target sequentially
      const targets = [...targetCardIds]
      for (const cardId of targets) {
        await postJSON(`/api/group/${currentGroup.id}/send`, {
          target_card_id: cardId,
          message: messageText,
          speaker,
        })
      }
      // Reload history once after all sends
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
      try { JSON.parse(g.card_ids || '[]').forEach((id) => ids.add(id)) } catch {}
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

  // ── Render ──

  return (
    <div className="panel group-chat-page">
      <header className="panel-header">
        <h1 className="panel-title">
          {currentGroup ? (
            <>
              <button type="button" className="chat-back-btn" onClick={backToList} title="返回群聊列表">
                {'◀'}
              </button>
              群聊 — {currentGroup.name || '未命名'}
            </>
          ) : '群聊'}
        </h1>
        <p className="panel-desc">
          {currentGroup
            ? `你正在与 ${currentGroup.card_ids?.map((id) => {
                const card = allCards.find((c) => (c.id || c.card_id) === id)
                return card?.name || id.slice(0, 4)
              }).filter(Boolean).join('、')} 群聊`
            : '创建群聊开始多角色对话'}
        </p>
      </header>

      {error && <ErrorBox message={error} onDismiss={() => setError(null)} />}

      {!currentGroup ? (
        /* ── Group list ── */
        <>
          <div style={{ padding: '12px 16px' }}>
            <button type="button" className="btn-primary" onClick={openCreate}>
              + 创建群聊
            </button>
          </div>

          {loading && <Loading text="加载群聊列表…" />}

          {!loading && groups.length === 0 && (
            <div className="shell-placeholder">
              <div className="shell-placeholder-inner">
                <div className="shell-placeholder-icon">{'\u{1F465}'}</div>
                <div className="shell-placeholder-title">还没有群聊</div>
                <div className="shell-placeholder-sub">创建群聊开始多角色对话</div>
              </div>
            </div>
          )}

          {!loading && groups.length > 0 && (
            <div className="group-list">
              {groups.map((g) => {
                const cardIds = JSON.parse(g.card_ids || '[]')
                const names = cardIds
                  .map((id) => allCards.find((c) => (c.id || c.card_id) === id)?.name)
                  .filter(Boolean)
                return (
                  <div
                    key={g.id}
                    className="group-list-item"
                    onClick={() => enterGroup(g)}
                  >
                    <div className="group-list-name">{g.name || '未命名群聊'}</div>
                    <div className="group-list-chars">{names.join('、')}</div>
                    <div className="group-list-meta">
                      {new Date(g.created_at).toLocaleString('zh-CN')}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </>
      ) : (
        /* ── Group chat view ── */
        <div className="group-chat-view">
          <div className="group-chat-messages">
            {systemMessage && (
              <div className="group-msg-system">
                <span>{systemMessage}</span>
              </div>
            )}
            {messages.length === 0 && !systemMessage && (
              <div className="shell-placeholder" style={{ padding: 40 }}>
                <div className="shell-placeholder-inner">
                  <div className="shell-placeholder-title">群聊已创建</div>
                  <div className="shell-placeholder-sub">
                    选择角色并发送第一条消息
                  </div>
                </div>
              </div>
            )}
            {messages.map((m, i) => {
              const isUser = m.role === 'user'
              const isAssistant = m.role === 'assistant'
              const userInitial = (userRole || authUser?.username || '我').charAt(0).toUpperCase()
              return (
                <div key={m.id || i} className={`chat-msg ${isUser ? 'chat-msg-user' : 'chat-msg-char'}`}>
                  {isAssistant && (
                    <div className="chat-msg-avatar">
                      <Avatar name={m.speaker} size={40} src={cardAvatars[m.card_id]} />
                    </div>
                  )}
                  <div className={`chat-bubble ${isUser ? 'chat-bubble-user' : 'chat-bubble-char'}`}>
                    <span className="chat-bubble-speaker">{m.speaker}</span>
                    <span className="chat-bubble-text">{m.content}</span>
                  </div>
                  {isUser && (
                    <div className="user-avatar-circle" style={{ minWidth: 36, minHeight: 36, width: 36, height: 36, fontSize: 14 }}>{userInitial}</div>
                  )}
                </div>
              )
            })}
            {loading && <Loading text="加载中…" />}
          </div>

          <form className="chat-input-area" onSubmit={handleSend}>
            <div className="group-chat-targets">
              <span className="group-chat-target-label">回复：</span>
              {currentGroup.card_ids.map((cardId) => {
                const cardData = allCards.find((c) => (c.id || c.card_id) === cardId)
                const name = cardData?.name || cardId.slice(0, 8)
                const selected = targetCardIds.includes(cardId)
                return (
                  <button
                    key={cardId}
                    type="button"
                    className={`group-chat-target-btn${selected ? ' active' : ''}`}
                    onClick={() => setTargetCardIds((prev) =>
                      prev.includes(cardId)
                        ? prev.filter((id) => id !== cardId)
                        : [...prev, cardId]
                    )}
                  >
                    {name}
                  </button>
                )
              })}
            </div>
            <div className="chat-input-row">
              <input
                className="modal-input"
                style={{ flex: 1 }}
                placeholder={
                  targetCardIds.length > 0
                    ? `对 ${targetCardIds.map((id) => {
                        const c = allCards.find((c) => (c.id || c.card_id) === id)
                        return c?.name || id.slice(0, 4)
                      }).join('、')} 说…`
                    : '请选择回复目标'
                }
                value={messageText}
                onChange={(e) => setMessageText(e.target.value)}
                disabled={targetCardIds.length === 0 || sending}
              />
              <button
                type="submit"
                className="btn-primary"
                disabled={targetCardIds.length === 0 || !messageText.trim() || sending}
              >
                {targetCardIds.length > 1 && !sending ? `发送 (${targetCardIds.length})` : sending ? '发送中…' : '发送'}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* ── Create modal ── */}
      {showCreate && (
        <div className="modal-overlay" onClick={() => setShowCreate(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 560 }}>
            <div className="modal-title">创建群聊</div>

            <div style={{ padding: '0 20px 12px' }}>
              <label className="modal-label">群聊名称（可选）</label>
              <input
                className="modal-input"
                placeholder="输入群聊名称…"
                value={groupName}
                onChange={(e) => setGroupName(e.target.value)}
              />
            </div>

            <div style={{ padding: '0 20px 12px' }}>
              <label className="modal-label">选择文本</label>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {texts.filter((t) => cardsByText[t.id]?.length).length === 0 ? (
                  <div style={{ color: 'var(--text-dim)', fontSize: 13, padding: 8 }}>
                    请先在文本管理中蒸馏角色卡
                  </div>
                ) : (
                  texts
                    .filter((t) => cardsByText[t.id]?.length)
                    .map((t) => (
                      <button
                        key={t.id}
                        type="button"
                        className={`text-tab${selectedTextId === t.id ? ' active' : ''}`}
                        style={{
                          padding: '6px 14px',
                          borderRadius: 8,
                          border: '1px solid var(--glass-border)',
                          background: selectedTextId === t.id ? 'var(--primary)' : 'var(--glass-bg)',
                          color: selectedTextId === t.id ? '#fff' : 'var(--text)',
                          cursor: 'pointer',
                          fontSize: 13,
                        }}
                        onClick={() => { setSelectedTextId(t.id); setSelectedCardIds([]) }}
                      >
                        {t.title || t.filename || t.id.slice(0, 8)}
                      </button>
                    ))
                )}
              </div>
            </div>

            {selectedTextId && (
              <div style={{ padding: '0 20px 12px' }}>
                <label className="modal-label">选择角色（至少选 2 个）</label>
                <div className="group-create-card-list">
                  {(cardsByText[selectedTextId] || []).length === 0 && (
                    <div style={{ color: 'var(--text-dim)', fontSize: 13, padding: 8 }}>
                      该书暂无角色卡
                    </div>
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
                        <Avatar name={c.name} size={32} src={cardAvatars[c.id || c.card_id]} />
                        <span>{c.name}</span>
                        <span className="group-create-card-check">{selected ? '✓' : ''}</span>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}

            <div className="modal-actions">
              <button type="button" className="btn-secondary glass" onClick={() => setShowCreate(false)}>
                取消
              </button>
              <button
                type="button"
                className="btn-primary"
                onClick={handleCreate}
                disabled={selectedCardIds.length < 2 || sending}
              >
                {sending ? '创建中…' : '创建'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
