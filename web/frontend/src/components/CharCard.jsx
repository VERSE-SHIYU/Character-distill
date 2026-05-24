import { useCallback, useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { postJSON, getAuthHeaders, fetchWithTimeout } from '../api/client'
import { saveAvatar, getAvatar, loadCardAvatar } from '../store/db'
import Avatar from './common/Avatar'
import Loading from './common/Loading'
import ErrorBox from './common/ErrorBox'
import RoleSetupModal from './RoleSetupModal'
import EditCardModal from './EditCardModal'
import ImageCropModal from './common/ImageCropModal'

// ---- CharCard (top-level) ----

export default function CharCard() {
  const currentTextId = useAppStore((s) => s.currentTextId)
  const texts = useAppStore((s) => s.texts)
  const setView = useAppStore((s) => s.setView)

  if (!currentTextId) {
    return (
      <div className="shell-placeholder">
        <div className="shell-placeholder-inner">
          <div className="shell-placeholder-icon">{'\u{1F464}'}</div>
          <div className="shell-placeholder-title">
            请先选择一份文本
          </div>
          <div className="shell-placeholder-sub">
            在"文本管理"中上传并选中一个文本
          </div>
          <button
            type="button"
            className="btn-primary"
            style={{ marginTop: 16 }}
            onClick={() => setView('text')}
          >
            前往文本管理
          </button>
        </div>
      </div>
    )
  }

  const currentText = texts.find((t) => t.id === currentTextId)
  const filename = currentText?.filename || currentTextId.slice(0, 8)

  return (
    <div className="char-panel panel">
      {/* Breadcrumb */}
      <nav className="breadcrumb">
        <button type="button" className="breadcrumb-item" onClick={() => setView('text')}>
          文本管理
        </button>
        <span className="breadcrumb-sep">›</span>
        <button
          type="button"
          className="breadcrumb-item breadcrumb-current"
          onClick={() => setView('text')}
        >
          {filename}
        </button>
        <span className="breadcrumb-sep">›</span>
        <span className="breadcrumb-item breadcrumb-current">角色管理</span>
      </nav>

      <header className="panel-header">
        <h1 className="panel-title">
          角色管理
        </h1>
        <p className="panel-desc">
          当前文本：{filename}
        </p>
      </header>
      <CharPanelBody textId={currentTextId} />
    </div>
  )
}

// ---- Body: cards list + detail pane ----

function CharPanelBody({ textId }) {
  const cards = useAppStore((s) => s.cards)
  const currentCard = useAppStore((s) => s.currentCard)
  const loadCards = useAppStore((s) => s.loadCards)
  const error = useAppStore((s) => s.error)
  const setError = useAppStore((s) => s.setError)
  const viewCard = useAppStore((s) => s.viewCard)
  const [detailLoading, setDetailLoading] = useState(false)

  useEffect(() => {
    loadCards(textId)
  }, [textId, loadCards])

  const handleSelectCard = (card) => {
    viewCard(card)
  }

  return (
    <div className="char-body">
      <CharSidebar textId={textId} cards={cards} currentCard={currentCard} onSelectCard={handleSelectCard} />
      <div className="char-detail-pane">
        {error && <ErrorBox message={error} onDismiss={() => setError(null)} />}
        {detailLoading ? (
          <Loading text="加载角色…" />
        ) : currentCard ? (
          <CardDetail card={currentCard} textId={textId} />
        ) : (
          <div className="char-detail-empty">
            <div className="char-detail-empty-icon">{'\u{1F464}'}</div>
            <p>选择或蒸馏一个角色查看详情</p>
          </div>
        )}
      </div>
    </div>
  )
}

// ---- Left: character list + identify/distill flow ----

function loadPinnedCards() {
  try {
    const raw = localStorage.getItem('pinnedCards')
    return raw ? JSON.parse(raw) : []
  } catch { return [] }
}

function savePinnedCards(ids) {
  localStorage.setItem('pinnedCards', JSON.stringify(ids))
}

function CharSidebar({ textId, cards, currentCard, onSelectCard }) {
  const identifiedChars = useAppStore((s) => s.identifiedChars)
  const identifying = useAppStore((s) => s.identifying)
  const distilling = useAppStore((s) => s.distilling)
  const distillTokenCount = useAppStore((s) => s.distillTokenCount)
  const distillStatus = useAppStore((s) => s.distillStatus)
  const identifyCharacters = useAppStore((s) => s.identifyCharacters)
  const distillCharacter = useAppStore((s) => s.distillCharacter)
  const cardAvatars = useAppStore((s) => s.cardAvatars)
  const setCardAvatar = useAppStore((s) => s.setCardAvatar)

  const [distillingName, setDistillingName] = useState(null)
  const [pinnedCards, setPinnedCards] = useState(loadPinnedCards)
  const [sharedCards, setSharedCards] = useState(new Set())
  const [shareConfirmTarget, setShareConfirmTarget] = useState(null)

  const togglePin = (e, cardId) => {
    e.stopPropagation()
    setPinnedCards((prev) => {
      const next = prev.includes(cardId)
        ? prev.filter((id) => id !== cardId)
        : [...prev, cardId]
      savePinnedCards(next)
      return next
    })
  }

  const handleShareToggle = async (e, cardId) => {
    e.stopPropagation()
    const isPublic = sharedCards.has(cardId)
    if (isPublic) {
      // Unshare directly
      try {
        await fetchWithTimeout(`/api/market/${cardId}/visibility`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
          body: JSON.stringify({ visibility: 'private' }),
        })
        setSharedCards((prev) => { const n = new Set(prev); n.delete(cardId); return n })
      } catch (err) { console.error('Unshare failed:', err) }
    }
  }

  // Sort: pinned first, then unpinned (stable order within groups)
  const sortedCards = [...cards].sort((a, b) => {
    const aPinned = pinnedCards.includes(a.id) ? 0 : 1
    const bPinned = pinnedCards.includes(b.id) ? 0 : 1
    return aPinned - bPinned
  })

  useEffect(() => {
    if (currentCard?.id) {
      const el = document.querySelector(`[data-card-id="${currentCard.id}"]`)
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
      }
    }
  }, [currentCard?.id])

  // Load card avatars for the sidebar list
  useEffect(() => {
    cards.forEach((c) => {
      if (c.id && !cardAvatars[c.id]) {
        loadCardAvatar(c.id).then((dataUrl) => {
          if (dataUrl) setCardAvatar(c.id, dataUrl)
        })
      }
    })
  }, [cards])

  useEffect(() => {
    if (!distilling) setDistillingName(null)
  }, [distilling])

  const handleIdentify = async () => {
    try {
      await identifyCharacters(textId)
    } catch { /* store already sets error */ }
  }

  const handleDistill = (name, force = false) => {
    setDistillingName(name)
    distillCharacter(textId, name, force)
  }

  const texts = useAppStore((s) => s.texts)

  const hasCards = cards.length > 0
  const hasIdentified = identifiedChars.length > 0

  return (
    <div className="char-sidebar-inner">
      <div className="char-sidebar-head">
        <h2 className="char-sidebar-title">
          角色列表
        </h2>
        <span className="char-sidebar-count">{cards.length}</span>
      </div>

      {/* Distilled cards list */}
      {hasCards && (
        <ul className="char-list">
          {sortedCards.map((c) => {
            const cardData = typeof c.card_json === 'string'
              ? JSON.parse(c.card_json)
              : c.card_json || c
            const name = cardData.name || c.name
            const identity = cardData.identity || ''
            const isActive = currentCard?.id === c.id
            const isPinned = pinnedCards.includes(c.id)
            const textInfo = texts.find((t) => t.id === c.text_id)
            return (
              <li key={c.id} className="char-list-li">
                <button
                  type="button"
                  className={`char-list-item${isActive ? ' active' : ''}`}
                  data-card-id={c.id}
                  onClick={() => onSelectCard({ ...c, ...cardData, text_id: textId })}
                >
                  <Avatar name={name} size={34} src={cardAvatars[c.id]} />
                  <div className="char-list-info">
                    <div className="char-list-name">{name}</div>
                    {textInfo?.filename && (
                      <span className="char-card-source">{'\u{1F4D6}'} {textInfo.filename}</span>
                    )}
                    {c.forked_from && (
                      <span className="char-card-source">{'\u{1F4CB}'} 已fork</span>
                    )}
                    <div className="char-list-identity">{identity}</div>
                  </div>
                </button>
                <button
                  type="button"
                  className={`char-pin-btn${isPinned ? ' pinned' : ''}`}
                  title={isPinned ? '取消置顶' : '置顶'}
                  onClick={(e) => togglePin(e, c.id)}
                >
                  {'\u{1F4CC}'}
                </button>
                <button
                  type="button"
                  className={`char-share-btn${sharedCards.has(c.id) ? ' shared' : ''}`}
                  title={sharedCards.has(c.id) ? '已分享到市场' : '分享到市场'}
                  onClick={(e) => {
                    if (sharedCards.has(c.id)) {
                      handleShareToggle(e, c.id)
                    } else {
                      e.stopPropagation()
                      setShareConfirmTarget(c)
                    }
                  }}
                >
                  {sharedCards.has(c.id) ? '\u{1F30D}' : '\u{1F516}'}
                </button>
              </li>
            )
          })}
        </ul>
      )}

      {/* Identified but not yet distilled */}
      {hasIdentified && (
        <div className="char-identified">
          <h3 className="char-identified-title">
            识别结果
          </h3>
          <ul className="char-identified-list">
            {identifiedChars.map((ch, i) => {
              const name = ch.name || `Character ${i + 1}`
              const already = cards.some((c) => {
                const d = typeof c.card_json === 'string'
                  ? JSON.parse(c.card_json)
                  : c.card_json || c
                return (d.name || c.name) === name
              })
              return (
                <li key={name} className="char-identified-item">
                  <div className="char-identified-info">
                    <span className="char-identified-name">{name}</span>
                    {ch.importance && (
                      <span className="char-identified-badge">
                        {ch.importance}
                      </span>
                    )}
                  </div>
                  {ch.reason && (
                    <p className="char-identified-reason">{ch.reason}</p>
                  )}
                  <button
                    type="button"
                    className={`btn-primary char-identified-btn${already ? ' char-btn-redist' : ''}`}
                    disabled={distilling}
                    onClick={() => handleDistill(name, already)}
                  >
                    {distillingName === name
                      ? [
                          distillTokenCount > 0 ? `${(distillTokenCount / 1000).toFixed(1)}k字符` : '',
                          distillStatus && distillStatus !== '正在蒸馏…' ? distillStatus : '',
                        ].filter(Boolean).join(' | ') || '蒸馏中…'
                      : already
                        ? '重新蒸馏'
                        : '蒸馏角色'}
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {/* Action buttons */}
      <div className="char-sidebar-actions">
        {!hasCards && !hasIdentified && !identifying && (
          <button
            type="button"
            className="btn-primary char-action-btn"
            onClick={handleIdentify}
          >
            开始蒸馏
          </button>
        )}
        {identifying && <Loading text="正在识别角色…" />}
        {distilling && distillingName && (
          <Loading text={[
            `正在蒸馏 ${distillingName}…`,
            distillTokenCount > 0 ? `${(distillTokenCount / 1000).toFixed(1)}k字符` : '',
            distillStatus && distillStatus !== '正在蒸馏…' ? distillStatus : '',
          ].filter(Boolean).join(' | ')} />
        )}
        {(hasCards || hasIdentified) && !identifying && (
          <button
            type="button"
            className="char-reidentify-btn"
            onClick={handleIdentify}
            disabled={identifying}
          >
            重新识别
          </button>
        )}
      </div>

      {/* Share confirm modal for sidebar */}
      {shareConfirmTarget && (
        <div className="modal-overlay" onClick={() => setShareConfirmTarget(null)}>
          <div className="modal-card" style={{ maxWidth: 420 }} onClick={(e) => e.stopPropagation()}>
            <h3 className="modal-title">分享到市场</h3>
            <div className="modal-body">
              <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 12, lineHeight: 1.6 }}>
                以下内容将对所有用户可见：
              </p>
              <div style={{ fontSize: 13, lineHeight: 1.8, paddingLeft: 8 }}>
                <div>{'\u{1F464}'} 角色名：{shareConfirmTarget.name || '?'}</div>
                <div>{'\u{1F3AF}'} 身份：{shareConfirmTarget.identity || '-'}</div>
                {shareConfirmTarget.personality_traits?.length > 0 && <div>{'\u{1F9E0}'} 性格特征</div>}
                {shareConfirmTarget.speaking_style?.tone && <div>{'\u{1F3A4}'} 语言风格</div>}
              </div>
            </div>
            <div className="modal-actions">
              <button className="btn-ghost" onClick={() => setShareConfirmTarget(null)}>取消</button>
              <button className="btn-primary" onClick={async () => {
                const cid = shareConfirmTarget.id || shareConfirmTarget.card_id
                setShareConfirmTarget(null)
                try {
                  await fetchWithTimeout(`/api/market/${cid}/visibility`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
                    body: JSON.stringify({ visibility: 'public' }),
                  })
                  setSharedCards((prev) => { const n = new Set(prev); n.add(cid); return n })
                } catch (err) { console.error('Share failed:', err) }
              }}>
                确认公开
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ---- Right: card detail view ----

function CardDetail({ card, textId }) {
  const startChat = useAppStore((s) => s.startChat)
  const userRole = useAppStore((s) => s.userRole)
  const setUserRole = useAppStore((s) => s.setUserRole)
  const setView = useAppStore((s) => s.setView)
  const updateCard = useAppStore((s) => s.updateCard)
  const [showShareConfirm, setShowShareConfirm] = useState(false)
  const [showRoleModal, setShowRoleModal] = useState(false)
  const [showEditModal, setShowEditModal] = useState(false)
  const [cropFile, setCropFile] = useState(null)
  const [shared, setShared] = useState(card.visibility === 'public')

  const data = typeof card.card_json === 'string'
    ? JSON.parse(card.card_json)
    : card.card_json || card
  const name = data.name || card.name || '?'
  const style = data.speaking_style || {}
  const rels = data.relationships || []

  const setCardAvatar = useAppStore((s) => s.setCardAvatar)
  const cardAvatars = useAppStore((s) => s.cardAvatars)

  const avatarUrl = cardAvatars[card.id] || null
  const avatarInputRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    if (cardAvatars[card.id]) return
    loadCardAvatar(card.id).then((dataUrl) => {
      if (!cancelled && dataUrl) setCardAvatar(card.id, dataUrl)
    })
    return () => { cancelled = true }
  }, [card.id, cardAvatars])

  const handleAvatarChange = useCallback(
    (e) => {
      const file = e.target.files?.[0]
      if (!file) return
      setCropFile(file)
      // Reset input so the same file can be re-selected
      e.target.value = ''
    },
    [],
  )

  const handleCropConfirm = useCallback(
    async (base64) => {
      setCropFile(null)
      // Save to IndexedDB (fast local cache)
      try {
        const res = await fetch(base64)
        const blob = await res.blob()
        await saveAvatar(card.id, blob)
      } catch { /* non-fatal */ }
      // Save to backend (permanent)
      const cid = card.id || card.card_id
      if (cid) {
        try {
          await fetch(`/api/cards/${cid}/avatar`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
            body: JSON.stringify({ data: base64 }),
          })
        } catch { /* non-fatal */ }
      }
      setCardAvatar(card.id, base64)
    },
    [card.id, setCardAvatar],
  )

  const handleCropCancel = useCallback(() => {
    setCropFile(null)
  }, [])

  const handleRoleConfirm = (role) => {
    setShowRoleModal(false)
    const originalFirstMessage = data.first_message || ''

    // Build a copy instead of mutating card (which came from Zustand store)
    const chatCard = { ...card }
    const updatedData = { ...data, first_message: '…' }
    chatCard.card_json = typeof card.card_json === 'string'
      ? JSON.stringify(updatedData)
      : { ...card.card_json, first_message: '…' }
    chatCard.first_message = '…'

    startChat(chatCard).then(() => {
      postJSON('/api/distill/generate-opening', {
        card_json: data,
        user_role: role || '',
      }, 30000)
        .then((res) => {
          const opening = res.opening || originalFirstMessage
          useAppStore.setState((s) => {
            const msgs = [...s.messages]
            if (msgs.length > 0 && msgs[0].role === 'char') {
              msgs[0] = { ...msgs[0], content: opening }
            }
            return { messages: msgs }
          })
        })
        .catch((err) => {
          console.warn('[CharCard] generate-opening failed:', err)
          useAppStore.setState((s) => {
            const msgs = [...s.messages]
            if (msgs.length > 0 && msgs[0].role === 'char') {
              msgs[0] = { ...msgs[0], content: originalFirstMessage }
            }
            return { messages: msgs }
          })
        })
    })
  }

  const handleRoleSkip = () => {
    setShowRoleModal(false)
    startChat(card)
  }

  const handleSaveEdit = async (cardJson) => {
    await updateCard(card.id || card.card_id, cardJson)
    setShowEditModal(false)
  }

  return (
    <div className="card-detail">
      <div className="card-detail-scroll">
        {/* Header: avatar + name + identity */}
        <div className="card-hero">
          <button
            type="button"
            className="card-avatar-btn"
            onClick={() => avatarInputRef.current?.click()}
            title="点击上传头像"
          >
            <Avatar name={name} src={avatarUrl} size={72} />
            <div className="card-avatar-overlay">{'\u{1F4F7}'}</div>
          </button>
          <input
            ref={avatarInputRef}
            type="file"
            accept="image/*"
            className="sr-only"
            onChange={handleAvatarChange}
          />
          <div className="card-hero-text">
            <h2 className="card-name">{name}</h2>
            {data.identity && (
              <p className="card-identity">{data.identity}</p>
            )}
            {card.forked_from && (
              <p className="card-forked-from">{'\u{1F4CB}'} 基于他人角色卡</p>
            )}
          </div>
        </div>

        {/* Personality traits */}
        {data.personality_traits?.length > 0 && (
          <CardSection label="性格特征">
            <div className="pill-list">
              {data.personality_traits.map((t, i) => (
                <span key={i} className="pill">{t}</span>
              ))}
            </div>
          </CardSection>
        )}

        {/* Speaking style */}
        {style.tone && (
          <CardSection label="语言风格">
            <div className="card-style-grid">
              <StyleChip label="语气" value={style.tone} />
              <StyleChip label="句式" value={style.sentence_pattern} />
              <StyleChip label="用词" value={style.vocabulary_level} />
            </div>
            {style.catchphrases?.length > 0 && (
              <div className="card-catchphrases">
                {style.catchphrases.map((c, i) => (
                  <p key={i} className="catchphrase">
                    "  {c}  "
                  </p>
                ))}
              </div>
            )}
          </CardSection>
        )}

        {/* Values */}
        {data.values?.length > 0 && (
          <CardSection label="核心价值观">
            <div className="pill-list">
              {data.values.map((v, i) => (
                <span key={i} className="pill pill-value">{v}</span>
              ))}
            </div>
          </CardSection>
        )}

        {/* Key memories */}
        {data.key_memories?.length > 0 && (
          <CardSection label="关键记忆">
            <ul className="card-memory-list">
              {data.key_memories.map((m, i) => (
                <li key={i} className="card-memory-item">{m}</li>
              ))}
            </ul>
          </CardSection>
        )}

        {/* Relationships */}
        {rels.length > 0 && (
          <CardSection label="人物关系">
            <div className="card-rel-list">
              {rels.map((r, i) => (
                <div key={i} className="card-rel-row">
                  <span className="card-rel-target">{r.target}</span>
                  <span className="card-rel-type pill">{r.relation}</span>
                  <span className="card-rel-attitude">{r.attitude}</span>
                </div>
              ))}
            </div>
          </CardSection>
        )}

        {/* Inner tensions */}
        {data.inner_tensions?.length > 0 && (
          <CardSection label="内在矛盾">
            <div className="pill-list">
              {data.inner_tensions.map((t, i) => (
                <span key={i} className="pill pill-tension">{t}</span>
              ))}
            </div>
          </CardSection>
        )}

        {/* Background */}
        {data.background && (
          <CardSection label="背景">
            <p className="card-background">{data.background}</p>
          </CardSection>
        )}

        {/* User identity input */}
        <CardSection label="用户身份设定">
          <input
            type="text"
            className="card-user-role-input"
            placeholder='例如："你是他的旧时好友"'
            value={userRole}
            onChange={(e) => setUserRole(e.target.value)}
          />
        </CardSection>
      </div>

      {/* Bottom: export + start chat + back */}
      <div className="card-footer">
        <button
          type="button"
          className="btn-secondary"
          onClick={() => setView('text')}
        >
          ← 返回文本列表
        </button>
        <button
          type="button"
          className="btn-secondary"
          onClick={() => setShowEditModal(true)}
        >
          {'✏️'} 编辑
        </button>
        <button
          type="button"
          className="btn-secondary card-export-btn"
          onClick={async () => {
            try {
              const res = await fetchWithTimeout(`/api/cards/${card.id}/export`)
              const blob = await res.blob()
              const url = URL.createObjectURL(blob)
              const a = document.createElement('a')
              a.href = url
              document.body.appendChild(a)
              a.click()
              a.remove()
              URL.revokeObjectURL(url)
            } catch (err) {
              console.error('Export card failed:', err)
            }
          }}
        >
          {'\u{1F4E5}'} 导出角色卡
        </button>
        <button
          type="button"
          className="btn-secondary"
          id="card-share-btn"
          onClick={() => {
            if (shared) {
              // Unshare directly — no confirm needed to withdraw
              fetchWithTimeout(`/api/market/${card.id}/visibility`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
                body: JSON.stringify({ visibility: 'private' }),
              }).then(() => setShared(false)).catch((err) => console.error('Unshare failed:', err))
            } else {
              setShowShareConfirm(true)
            }
          }}
        >
          {shared ? '\u{1F30D} 已分享' : '\u{1F512} 分享到市场'}
        </button>
        <button
          type="button"
          className="btn-primary card-chat-btn"
          onClick={() => setShowRoleModal(true)}
        >
          {'\u{1F4AC}'} 开始对话
        </button>
      </div>

      <RoleSetupModal
        isOpen={showRoleModal}
        characterName={name}
        relationships={rels}
        onConfirm={handleRoleConfirm}
        onSkip={handleRoleSkip}
      />

      <EditCardModal
        isOpen={showEditModal}
        data={data}
        cardId={card.id || card.card_id}
        onSave={handleSaveEdit}
        onClose={() => setShowEditModal(false)}
      />

      <ImageCropModal
        file={cropFile}
        onConfirm={handleCropConfirm}
        onCancel={handleCropCancel}
      />

      {/* Share confirm modal */}
      {showShareConfirm && (
        <div className="modal-overlay" onClick={() => setShowShareConfirm(false)}>
          <div className="modal-card" style={{ maxWidth: 420 }} onClick={(e) => e.stopPropagation()}>
            <h3 className="modal-title">分享到市场</h3>
            <div className="modal-body">
              <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 12, lineHeight: 1.6 }}>
                以下内容将对所有用户可见：
              </p>
              <div style={{ fontSize: 13, lineHeight: 1.8, paddingLeft: 8 }}>
                <div>{'\u{1F464}'} 角色名：{name}</div>
                {data.identity && <div>{'\u{1F3AF}'} 身份：{data.identity}</div>}
                {data.personality_traits?.length > 0 && <div>{'\u{1F9E0}'} 性格特征</div>}
                {style.tone && <div>{'\u{1F3A4}'} 语言风格</div>}
                {data.values?.length > 0 && <div>{'\u{2B50}'} 核心价值观</div>}
                {data.background && <div>{'\u{1F4D6}'} 背景设定</div>}
              </div>
            </div>
            <div className="modal-actions">
              <button className="btn-ghost" onClick={() => setShowShareConfirm(false)}>取消</button>
              <button className="btn-primary" onClick={async () => {
                setShowShareConfirm(false)
                try {
                  await fetchWithTimeout(`/api/market/${card.id}/visibility`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
                    body: JSON.stringify({ visibility: 'public' }),
                  })
                  setShared(true)
                } catch (err) {
                  console.error('Share failed:', err)
                }
              }}>
                确认公开
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ---- Helpers ----

function CardSection({ label, children }) {
  return (
    <div className="card-section">
      <div className="card-section-label">{label}</div>
      {children}
    </div>
  )
}

function StyleChip({ label, value }) {
  if (!value) return null
  return (
    <div className="card-style-chip">
      <span className="card-style-chip-label">{label}</span>
      <span className="card-style-chip-value">{value}</span>
    </div>
  )
}
