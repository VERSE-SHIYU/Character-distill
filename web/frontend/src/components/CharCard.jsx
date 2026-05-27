import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import useAppStore from '../store/useAppStore'
import { postJSON, getAuthHeaders, fetchWithTimeout } from '../api/client'
import { saveAvatar, getAvatar, loadCardAvatar } from '../store/db'
import Avatar from './common/Avatar'
import Loading from './common/Loading'
import ErrorBox from './common/ErrorBox'
import { Book, User, Pin, Tag, Bookmark, Globe, Clipboard } from './common/Icon'
import { MessageSquare, Edit, Trash2 } from './common/Icon'
import { parseCardJson } from '../utils/card'
import RoleSetupModal from './RoleSetupModal'
import EditCardModal from './EditCardModal'
import ImageCropModal from './common/ImageCropModal'
import ConfirmModal from './common/ConfirmModal'

// ---- CharCard (top-level) ----

export default function CharCard() {
  const currentTextId = useAppStore((s) => s.currentTextId)
  const texts = useAppStore((s) => s.texts)
  const setView = useAppStore((s) => s.setView)

  if (!currentTextId) {
    return (
      <div className="shell-placeholder">
        <div className="shell-placeholder-inner">
          <div className="shell-placeholder-icon"><User size={28} /></div>
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
        <button type="button" className="chat-back-btn" onClick={() => setView('text')} title="返回文本管理">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5m7-7-7 7 7 7"/></svg>
          返回
        </button>
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
            <div className="char-detail-empty-icon"><User size={28} /></div>
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
  const standaloneCards = useAppStore((s) => s.standaloneCards)
  const loadStandaloneCards = useAppStore((s) => s.loadStandaloneCards)

  const [distillingName, setDistillingName] = useState(null)
  const [pinnedCards, setPinnedCards] = useState(loadPinnedCards)
  const [sharedCards, setSharedCards] = useState(new Set())
  const [shareConfirmTarget, setShareConfirmTarget] = useState(null)
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [trashMode, setTrashMode] = useState(false)
  const [deletedCards, setDeletedCards] = useState([])
  const [trashLoading, setTrashLoading] = useState(false)
  const [purgeConfirmTarget, setPurgeConfirmTarget] = useState(null)
  const [purgeAllConfirm, setPurgeAllConfirm] = useState(false)
  const [localError, setLocalError] = useState(null)
  const [publishDescription, setPublishDescription] = useState('')
  const [publishTags, setPublishTags] = useState('')
  const [publishMessage, setPublishMessage] = useState('')
  const [publishSending, setPublishSending] = useState(false)

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
      // Unshare: target the published fork, not the draft
      const card = cards.find(c => c.id === cardId)
      const targetId = card?.published_id || cardId
      try {
        await fetchWithTimeout(`/api/market/${targetId}/visibility`, {
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

  // Deduplicate by character name — no duplicate names in the list
  const uniqueCards = (() => {
    const seen = new Set()
    return sortedCards.filter((c) => {
      const d = parseCardJson(c)
      const name = d.name || c.name
      if (!name || seen.has(name)) return false
      seen.add(name)
      return true
    })
  })()

  useEffect(() => {
    // Initialize sharedCards from published_id (fork exists = card is published)
    const publicIds = cards.filter((c) => c.published_id).map((c) => c.id)
    setSharedCards((prev) => {
      const next = new Set(prev)
      publicIds.forEach((id) => next.add(id))
      return next
    })
  }, [cards])

  useEffect(() => {
    if (currentCard?.id) {
      const el = document.querySelector(`[data-card-id="${currentCard.id}"]`)
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
      }
    }
  }, [currentCard?.id])

  // Load card avatars for the sidebar list
  const avatarRequestedRef = useRef(new Set())
  useEffect(() => {
    cards.forEach((c) => {
      if (c.id && !cardAvatars[c.id] && !avatarRequestedRef.current.has(c.id)) {
        avatarRequestedRef.current.add(c.id)
        if (c.avatar_data) {
          setCardAvatar(c.id, c.avatar_data)
        } else {
          loadCardAvatar(c.id).then((dataUrl) => {
            if (dataUrl) setCardAvatar(c.id, dataUrl)
          })
        }
      }
    })
  }, [cards])

  useEffect(() => {
    if (!distilling) setDistillingName(null)
  }, [distilling])

  useEffect(() => {
    loadStandaloneCards()
  }, [loadStandaloneCards])

  const loadTrash = async () => {
    setTrashLoading(true)
    try {
      const res = await fetchWithTimeout('/api/cards/trash', { headers: { ...getAuthHeaders() } })
      const data = await res.json()
      setDeletedCards(Array.isArray(data) ? data : [])
    } catch (err) {
      console.error('[CharCard] Load trash failed:', err)
    } finally {
      setTrashLoading(false)
    }
  }

  const switchTrashMode = (on) => {
    setTrashMode(on)
    if (on) loadTrash()
  }

  const handleRestoreCard = async (cardId) => {
    try {
      await fetchWithTimeout(`/api/cards/${cardId}/restore`, {
        method: 'POST',
        headers: { ...getAuthHeaders() },
      })
      setDeletedCards((prev) => prev.filter((c) => c.id !== cardId))
      loadCards(textId)
      loadStandaloneCards()
    } catch (err) {
      setLocalError(err.message || '恢复失败')
    }
  }

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

  const hasCards = uniqueCards.length > 0
  const hasIdentified = identifiedChars.length > 0

  return (
    <div className="char-sidebar-inner">
      {localError && <ErrorBox message={localError} onDismiss={() => setLocalError(null)} />}
      <div className="char-sidebar-head">
        <h2 className="char-sidebar-title">
          {trashMode ? '回收站' : '角色列表'}
        </h2>
        <span className="char-sidebar-count">{trashMode ? deletedCards.length : cards.length}</span>
        <button
          type="button"
          className="btn-ghost btn-sm"
          style={{ marginLeft: 'auto', fontSize: 12 }}
          onClick={() => switchTrashMode(!trashMode)}
        >
          {trashMode ? <><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5m7-7-7 7 7 7"/></svg> 返回列表</> : <><Trash2 size={14} /> 回收站</>}
        </button>
      </div>

      {/* Trash view */}
      {trashMode ? (
        trashLoading ? (
          <Loading text="加载回收站…" />
        ) : deletedCards.length === 0 ? (
          <p style={{ textAlign: 'center', color: 'var(--text-dim)', padding: 20, fontSize: 13 }}>回收站为空</p>
        ) : (
          <ul className="char-list">
            {deletedCards.map((c) => {
              const cardData = parseCardJson(c)
              const name = cardData.name || c.name || '?'
              return (
                <li key={c.id} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '8px 10px' }}>
                  <Avatar name={name} size={30} />
                  <span style={{ flex: 1, fontSize: 13 }}>{name}</span>
                  <button
                    type="button"
                    className="btn-primary btn-sm"
                    style={{ height: 30, fontSize: 12, padding: '0 10px' }}
                    onClick={() => handleRestoreCard(c.id)}
                  >
                    恢复
                  </button>
                  <button
                    type="button"
                    className="btn-danger-sm"
                    onClick={() => setPurgeConfirmTarget(c.id)}
                  >
                    彻底删除
                  </button>
                </li>
              )
            })}
            <li style={{ padding: '8px 10px' }}>
              <button
                type="button"
                className="btn-danger-sm"
                style={{ width: '100%' }}
                onClick={() => setPurgeAllConfirm(true)}
              >
                清空回收站
              </button>
            </li>
          </ul>
        )
      ) : (
      <>

      {/* Distilled cards list */}
      {hasCards && (
        <div className="char-list">
          {uniqueCards.map((c, idx) => {
            const cardData = parseCardJson(c)
            const name = cardData.name || c.name
            const identity = cardData.identity || ''
            const isActive = currentCard?.id === c.id
            const textInfo = texts.find((t) => t.id === c.text_id)
            const isShared = sharedCards.has(c.id)
            const createdAt = c.created_at || ''
            return (
              <div
                key={c.id}
                role="button"
                tabIndex={0}
                className={`char-list-item${isActive ? ' active' : ''}`}
                data-card-id={c.id}
                onClick={() => onSelectCard({ ...c, ...cardData, text_id: textId })}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onSelectCard({ ...c, ...cardData, text_id: textId }) }}
              >
                <Avatar name={name} size={40} src={cardAvatars[c.id]} />
                <div className="char-list-info">
                  <div className="char-list-name">{name}</div>
                  <div className="char-list-identity">{identity}</div>
                  <div className="char-card-footer">
                    {textInfo?.filename && (
                      <span className="char-card-source-line">{textInfo.filename}</span>
                    )}
                  </div>
                </div>
                <span className={'char-card-status' + (isShared ? ' shared' : '')}>
                  {isShared ? '已公开' : '私有'}
                </span>
              </div>
            )
          })}
        </div>
      )}

      {/* Standalone cards (forked from market, no text attachment) */}
      {standaloneCards.length > 0 && (
        <div className="char-standalone">
          <h3 className="char-identified-title"><Globe size={16} /> 来自市场</h3>
          <ul className="char-list">
            {standaloneCards.map((c) => {
              const cardData = parseCardJson(c)
              const name = cardData.name || c.name
              const identity = cardData.identity || ''
              const isActive = currentCard?.id === c.id
              return (
                <li key={c.id}>
                  <div
                    role="button"
                    tabIndex={0}
                    className={`char-list-item${isActive ? ' active' : ''}`}
                    data-card-id={c.id}
                    onClick={() => onSelectCard({ ...c, ...cardData, text_id: '' })}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onSelectCard({ ...c, ...cardData, text_id: '' }) }}
                  >
                    <div className="char-card-top">
                      <Avatar name={name} size={34} src={cardAvatars[c.id]} />
                      <div className="char-list-info">
                        <div className="char-list-name">{name}</div>
                        <div className="char-list-identity">{identity}</div>
                      </div>
                    </div>
                    {c.forked_from && (
                      <div className="char-card-meta">
                        <span className="char-card-source"><Clipboard size={11} /> 来自市场</span>
                      </div>
                    )}
                    <div className="char-card-actions">
                      <div className="char-card-actions-spacer" />
                      <button
                        type="button"
                        className="char-card-action-btn danger"
                        title="删除角色"
                        onClick={(e) => {
                          e.stopPropagation()
                          setDeleteTarget(c)
                        }}
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </div>
                </li>
              )
            })}
          </ul>
        </div>
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
                const d = parseCardJson(c)
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
            className="btn-primary char-action-btn-full"
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

      {/* Share confirm modal for sidebar — portal to body */}
      {shareConfirmTarget && createPortal(
        <div className="modal-overlay" onClick={() => setShareConfirmTarget(null)}>
          <div className="modal-card" style={{ maxWidth: 460 }} onClick={(e) => e.stopPropagation()}>
            <h3 className="modal-title">分享到市场</h3>
            <div className="modal-body publish-form-body">
              <div className="publish-field">
                <label className="publish-label">角色描述</label>
                <textarea
                  className="publish-textarea"
                  value={publishDescription}
                  onChange={(e) => setPublishDescription(e.target.value)}
                  placeholder="简单描述这个角色…"
                  rows={3}
                />
              </div>
              <div className="publish-field">
                <label className="publish-label">标签（逗号分隔）</label>
                <input
                  className="publish-input"
                  value={publishTags}
                  onChange={(e) => setPublishTags(e.target.value)}
                  placeholder="古风, 玄幻, 治愈"
                />
              </div>
              <div className="publish-field">
                <label className="publish-label">发布说明</label>
                <textarea
                  className="publish-textarea"
                  value={publishMessage}
                  onChange={(e) => setPublishMessage(e.target.value)}
                  placeholder="这次更新了什么？"
                  rows={2}
                />
              </div>
            </div>
            <div className="modal-actions">
              <button className="btn-ghost" onClick={() => setShareConfirmTarget(null)}>取消</button>
              <button
                className="btn-primary"
                disabled={publishSending || !publishMessage.trim()}
                onClick={async () => {
                  const cid = shareConfirmTarget.id || shareConfirmTarget.card_id
                  setPublishSending(true)
                  try {
                    await fetchWithTimeout(`/api/market/${cid}/publish`, {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
                      body: JSON.stringify({
                        market_description: publishDescription.trim(),
                        market_tags: publishTags.trim(),
                        publish_message: publishMessage.trim(),
                      }),
                    })
                    setSharedCards((prev) => { const n = new Set(prev); n.add(cid); return n })
                    setShareConfirmTarget(null)
                    setPublishDescription('')
                    setPublishTags('')
                    setPublishMessage('')
                  } catch (err) {
                    console.error('Publish failed:', err)
                  } finally {
                    setPublishSending(false)
                  }
                }}
              >
                {publishSending ? '发布中…' : '确认发布'}
              </button>
            </div>
          </div>
        </div>,
        document.body,
      )}

      {/* Delete confirm modal — portal to body */}
      {deleteTarget && createPortal(
        <div className="modal-overlay" onClick={() => setDeleteTarget(null)}>
          <div className="modal-card" style={{ maxWidth: 380 }} onClick={(e) => e.stopPropagation()}>
            <h3 className="modal-title">删除角色</h3>
            <div className="modal-body">
              <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                确定要删除「{deleteTarget.name || '?'}」吗？此操作不可撤销。
              </p>
            </div>
            <div className="modal-actions">
              <button className="btn-ghost" onClick={() => setDeleteTarget(null)}>取消</button>
              <button className="btn-danger" onClick={async () => {
                const cid = deleteTarget.id || deleteTarget.card_id
                try {
                  await fetchWithTimeout(`/api/cards/${cid}`, {
                    method: 'DELETE',
                    headers: { ...getAuthHeaders() },
                  })
                  setDeleteTarget(null)
                  await loadCards(textId)
                  await loadStandaloneCards()
                } catch (err) {
                  setDeleteTarget(null)
                  setLocalError(err.message || '删除失败')
                }
              }}>
                移入回收站
              </button>
            </div>
          </div>
        </div>,
        document.body,
      )}

      <ConfirmModal
        isOpen={!!purgeConfirmTarget}
        title="彻底删除"
        message="确定彻底删除？此操作不可恢复。"
        confirmText="彻底删除"
        onConfirm={async () => {
          const id = purgeConfirmTarget
          try {
            await fetchWithTimeout(`/api/cards/${id}/purge`, {
              method: 'DELETE',
              headers: { ...getAuthHeaders() },
            })
            setPurgeConfirmTarget(null)
            setDeletedCards((prev) => prev.filter((c) => c.id !== id))
          } catch (err) {
            setPurgeConfirmTarget(null)
            setLocalError(err.message || '删除失败')
          }
        }}
        onCancel={() => setPurgeConfirmTarget(null)}
        danger
      />
      <ConfirmModal
        isOpen={purgeAllConfirm}
        title="清空回收站"
        message="确定清空回收站？所有角色将被彻底删除，不可恢复。"
        confirmText="清空"
        onConfirm={async () => {
          setPurgeAllConfirm(false)
          try {
            await Promise.all(deletedCards.map((c) =>
              fetchWithTimeout(`/api/cards/${c.id}/purge`, {
                method: 'DELETE',
                headers: { ...getAuthHeaders() },
              }),
            ))
            setDeletedCards([])
          } catch (err) {
            setLocalError(err.message || '清空回收站失败')
          }
        }}
        onCancel={() => setPurgeAllConfirm(false)}
        danger
      />
      </>
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
  const [shared, setShared] = useState(!!card.published_id)
  const [publishedCardId, setPublishedCardId] = useState(card.published_id || null)
  const [publishDescription, setPublishDescription] = useState(card.market_description || '')
  const [publishTags, setPublishTags] = useState(card.market_tags || '')
  const [publishMessage, setPublishMessage] = useState('')
  const [publishSending, setPublishSending] = useState(false)

  const data = parseCardJson(card)
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
    if (card.avatar_data) {
      setCardAvatar(card.id, card.avatar_data)
      return
    }
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
          className="chat-back-btn"
          onClick={() => setView('text')}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5m7-7-7 7 7 7"/></svg>
          返回文本列表
        </button>
        <button
          type="button"
          className="btn-secondary"
          onClick={() => setShowEditModal(true)}
        >
          <Edit size={16} /> 编辑
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
            setPublishDescription(card.market_description || '')
            setPublishTags(card.market_tags || '')
            setPublishMessage('')
            setShowShareConfirm(true)
          }}
        >
          {shared ? '\u{1F30D} 已分享' : '\u{1F512} 分享到市场'}
        </button>
        <button
          type="button"
          className="btn-primary card-chat-btn"
          onClick={() => setShowRoleModal(true)}
        >
          <MessageSquare size={14} /> 开始对话
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

      {/* Share confirm modal — portal to body */}
      {showShareConfirm && createPortal(
        <div className="modal-overlay" onClick={() => setShowShareConfirm(false)}>
          <div className="modal-card" style={{ maxWidth: 460 }} onClick={(e) => e.stopPropagation()}>
            <h3 className="modal-title">分享到市场</h3>
            <div className="modal-body publish-form-body">
              <div className="publish-field">
                <label className="publish-label">角色描述</label>
                <textarea
                  className="publish-textarea"
                  value={publishDescription}
                  onChange={(e) => setPublishDescription(e.target.value)}
                  placeholder="简单描述这个角色…"
                  rows={3}
                />
              </div>
              <div className="publish-field">
                <label className="publish-label">标签（逗号分隔）</label>
                <input
                  className="publish-input"
                  value={publishTags}
                  onChange={(e) => setPublishTags(e.target.value)}
                  placeholder="古风, 玄幻, 治愈"
                />
              </div>
              <div className="publish-field">
                <label className="publish-label">发布说明</label>
                <textarea
                  className="publish-textarea"
                  value={publishMessage}
                  onChange={(e) => setPublishMessage(e.target.value)}
                  placeholder="这次更新了什么？"
                  rows={2}
                />
              </div>
            </div>
            <div className="modal-actions">
              <button className="btn-ghost" onClick={() => setShowShareConfirm(false)}>取消</button>
              <button
                className="btn-primary"
                disabled={publishSending || !publishMessage.trim()}
                onClick={async () => {
                  const method = shared ? 'PUT' : 'POST'
                  const targetId = shared ? (publishedCardId || card.id) : card.id
                  setPublishSending(true)
                  try {
                    const res = await fetchWithTimeout(`/api/market/${targetId}/publish`, {
                      method,
                      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
                      body: JSON.stringify({
                        card_json: typeof card.card_json === 'string' ? card.card_json : JSON.stringify(card.card_json),
                        market_description: publishDescription.trim(),
                        market_tags: publishTags.trim(),
                        publish_message: publishMessage.trim(),
                      }),
                    })
                    const data = await res.json()
                    if (data.card_id) setPublishedCardId(data.card_id)
                    setShared(true)
                    setShowShareConfirm(false)
                    setPublishDescription('')
                    setPublishTags('')
                    setPublishMessage('')
                  } catch (err) {
                    console.error('Publish failed:', err)
                  } finally {
                    setPublishSending(false)
                  }
                }}
              >
                {publishSending ? '发布中…' : (shared ? '更新发布' : '确认发布')}
              </button>
            </div>
          </div>
        </div>,
        document.body,
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
