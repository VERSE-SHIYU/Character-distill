import { useState, useEffect, useCallback } from 'react'
import { fetchWithTimeout } from '../api/client'
import HistoryPanel from './HistoryPanel'
import Avatar from './common/Avatar'
import Loading from './common/Loading'
import ConfirmModal from './common/ConfirmModal'
import { parseCardJson } from '../utils/card'

function formatTime(iso) {
  if (!iso) return '—'
  try {
    const s = iso.includes('T') && !iso.endsWith('Z') && !iso.includes('+') ? iso + 'Z' : iso
    return new Date(s).toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

export default function TrashPage() {
  const [tab, setTab] = useState('chat')
  const [cards, setCards] = useState([])
  const [cardsLoading, setCardsLoading] = useState(false)
  const [purgeId, setPurgeId] = useState(null)

  // Group trash
  const [groups, setGroups] = useState([])
  const [groupsLoading, setGroupsLoading] = useState(false)
  const [restoreGroupId, setRestoreGroupId] = useState(null)
  const [purgeGroupId, setPurgeGroupId] = useState(null)

  // Text trash
  const [texts, setTexts] = useState([])
  const [textsLoading, setTextsLoading] = useState(false)
  const [restoreTextId, setRestoreTextId] = useState(null)
  const [purgeTextId, setPurgeTextId] = useState(null)

  const loadTrashCards = async () => {
    setCardsLoading(true)
    try {
      const res = await fetchWithTimeout('/api/cards/trash')
      const data = await res.json()
      setCards(Array.isArray(data) ? data : data.cards || [])
    } catch { /* ignore */ } finally {
      setCardsLoading(false)
    }
  }

  const loadTrashGroups = useCallback(async () => {
    setGroupsLoading(true)
    try {
      const res = await fetchWithTimeout('/api/group/trash')
      const data = await res.json()
      setGroups(data.groups || [])
    } catch { /* ignore */ } finally {
      setGroupsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (tab === 'cards') loadTrashCards()
    if (tab === 'groups') loadTrashGroups()
    if (tab === 'texts') loadTrashTexts()
  }, [tab, loadTrashGroups])

  const handleRestore = async (cardId) => {
    try {
      await fetchWithTimeout(`/api/cards/${cardId}/restore`, { method: 'POST' })
      setCards((prev) => prev.filter((c) => c.id !== cardId))
    } catch { /* ignore */ }
  }

  const handlePurge = async () => {
    const id = purgeId
    setPurgeId(null)
    if (!id) return
    try {
      await fetchWithTimeout(`/api/cards/${id}/purge`, { method: 'DELETE' })
      setCards((prev) => prev.filter((c) => c.id !== id))
    } catch { /* ignore */ }
  }

  const handleRestoreGroup = async () => {
    const id = restoreGroupId
    setRestoreGroupId(null)
    if (!id) return
    try {
      await fetchWithTimeout(`/api/group/${id}/restore`, { method: 'POST' })
      setGroups((prev) => prev.filter((g) => g.id !== id))
    } catch { /* ignore */ }
  }

  const handlePurgeGroup = async () => {
    const id = purgeGroupId
    setPurgeGroupId(null)
    if (!id) return
    try {
      await fetchWithTimeout(`/api/group/${id}/permanent`, { method: 'DELETE' })
      setGroups((prev) => prev.filter((g) => g.id !== id))
    } catch { /* ignore */ }
  }

  const loadTrashTexts = async () => {
    setTextsLoading(true)
    try {
      const res = await fetchWithTimeout('/api/text/trash')
      const data = await res.json()
      setTexts(Array.isArray(data) ? data : [])
    } catch { /* ignore */ } finally {
      setTextsLoading(false)
    }
  }

  const handleRestoreText = async () => {
    const id = restoreTextId
    setRestoreTextId(null)
    if (!id) return
    try {
      await fetchWithTimeout(`/api/text/${id}/restore`, { method: 'POST' })
      setTexts((prev) => prev.filter((t) => t.id !== id))
    } catch { /* ignore */ }
  }

  const handlePurgeText = async () => {
    const id = purgeTextId
    setPurgeTextId(null)
    if (!id) return
    try {
      await fetchWithTimeout(`/api/text/${id}/permanent`, { method: 'DELETE' })
      setTexts((prev) => prev.filter((t) => t.id !== id))
    } catch { /* ignore */ }
  }

  return (
    <div className="panel" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <header className="panel-header">
        <h1 className="panel-title">回收站</h1>
        <p className="panel-desc">管理已删除的对话、角色卡和群聊</p>
      </header>

      <div className="history-tab-bar">
        <button
          type="button"
          className={`history-tab${tab === 'chat' ? ' active' : ''}`}
          onClick={() => setTab('chat')}
        >
          已删除对话
        </button>
        <button
          type="button"
          className={`history-tab${tab === 'cards' ? ' active' : ''}`}
          onClick={() => setTab('cards')}
        >
          已删除角色卡
        </button>
        <button
          type="button"
          className={`history-tab${tab === 'groups' ? ' active' : ''}`}
          onClick={() => setTab('groups')}
        >
          已删除群聊
        </button>
        <button
          type="button"
          className={`history-tab${tab === 'texts' ? ' active' : ''}`}
          onClick={() => setTab('texts')}
        >
          已删除书籍
        </button>
      </div>

      {tab === 'chat' && <HistoryPanel initialTrash />}

      {tab === 'cards' && (
        <>
          {cardsLoading ? (
            <Loading text="加载已删除角色卡…" />
          ) : cards.length === 0 ? (
            <div className="shell-placeholder" style={{ padding: 40 }}>
              <div className="shell-placeholder-inner">
                <div className="shell-placeholder-icon">
                  <svg viewBox="0 0 120 100" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" width="80" height="67" style={{ color: 'var(--text-dim)', opacity: 0.4 }}>
                    <path d="M30 30 L25 85 C25 90 30 95 35 95 L85 95 C90 95 95 90 95 85 L90 30" />
                    <path d="M20 30 L100 30" strokeWidth="1.5" />
                    <path d="M45 30 L45 18 C45 14 49 10 53 10 L67 10 C71 10 75 14 75 18 L75 30" />
                    <path d="M50 45 L50 78" />
                    <path d="M70 45 L70 78" />
                    <path d="M60 45 L60 78" opacity="0.6" />
                    <path d="M5 30 L115 30" strokeWidth="0.8" opacity="0.3" />
                    <circle cx="95" cy="15" r="3" opacity="0.3" />
                    <circle cx="105" cy="25" r="2" opacity="0.2" />
                    <circle cx="15" cy="85" r="2" opacity="0.25" />
                    <circle cx="110" cy="80" r="1.5" opacity="0.2" />
                  </svg>
                </div>
                <div className="shell-placeholder-title">回收站空空如也</div>
                <div className="shell-placeholder-sub">删除的角色卡会出现在这里</div>
              </div>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, padding: '12px 0' }}>
              {cards.map((card) => {
                const cardData = parseCardJson(card)
                const name = cardData.name || card.name || '?'
                return (
                  <div key={card.id} className="history-swipe-wrapper">
                    <div className="history-swipe-actions">
                      <button
                        type="button"
                        className="history-swipe-restore"
                        onClick={() => handleRestore(card.id)}
                      >
                        恢复
                      </button>
                      <button
                        type="button"
                        className="history-swipe-delete"
                        onClick={() => setPurgeId(card.id)}
                      >
                        彻底删除
                      </button>
                    </div>
                    <div className="history-item" style={{ cursor: 'default' }}>
                      <Avatar name={name} size={40} />
                      <div className="history-item-body">
                        <div className="history-item-head">
                          <span className="history-item-name">{name}</span>
                        </div>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}

      {tab === 'groups' && (
        <>
          {groupsLoading ? (
            <Loading text="加载已删除群聊…" />
          ) : groups.length === 0 ? (
            <div className="shell-placeholder" style={{ padding: 40 }}>
              <div className="shell-placeholder-inner">
                <div className="shell-placeholder-icon">
                  <svg viewBox="0 0 120 100" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" width="80" height="67" style={{ color: 'var(--text-dim)', opacity: 0.4 }}>
                    <path d="M30 30 L25 85 C25 90 30 95 35 95 L85 95 C90 95 95 90 95 85 L90 30" />
                    <path d="M20 30 L100 30" strokeWidth="1.5" />
                    <path d="M45 30 L45 18 C45 14 49 10 53 10 L67 10 C71 10 75 14 75 18 L75 30" />
                    <path d="M50 45 L50 78" />
                    <path d="M70 45 L70 78" />
                    <path d="M60 45 L60 78" opacity="0.6" />
                    <path d="M5 30 L115 30" strokeWidth="0.8" opacity="0.3" />
                    <circle cx="95" cy="15" r="3" opacity="0.3" />
                    <circle cx="105" cy="25" r="2" opacity="0.2" />
                    <circle cx="15" cy="85" r="2" opacity="0.25" />
                    <circle cx="110" cy="80" r="1.5" opacity="0.2" />
                  </svg>
                </div>
                <div className="shell-placeholder-title">回收站空空如也</div>
                <div className="shell-placeholder-sub">删除的群聊会出现在这里</div>
              </div>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, padding: '12px 0' }}>
              {groups.map((g) => (
                <div key={g.id} className="history-swipe-wrapper">
                  <div className="history-swipe-actions">
                    <button
                      type="button"
                      className="history-swipe-restore"
                      onClick={() => setRestoreGroupId(g.id)}
                    >
                      恢复
                    </button>
                    <button
                      type="button"
                      className="history-swipe-delete"
                      onClick={() => setPurgeGroupId(g.id)}
                    >
                      彻底删除
                    </button>
                  </div>
                  <div className="history-item" style={{ cursor: 'default' }}>
                    <Avatar name={g.name || '群聊'} size={40} />
                    <div className="history-item-body">
                      <div className="history-item-head">
                        <span className="history-item-name">{g.name || '未命名群聊'}</span>
                        <span className="history-item-time">{formatTime(g.deleted_at)}</span>
                      </div>
                      <p className="history-item-preview">
                        {Array.isArray(g.card_ids) ? g.card_ids.length : 0} 个角色
                      </p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {tab === 'texts' && (
        <>
          {textsLoading ? (
            <Loading text="加载已删除书籍…" />
          ) : texts.length === 0 ? (
            <div className="shell-placeholder" style={{ padding: 40 }}>
              <div className="shell-placeholder-inner">
                <div className="shell-placeholder-icon">
                  <svg viewBox="0 0 120 100" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" width="80" height="67" style={{ color: 'var(--text-dim)', opacity: 0.4 }}>
                    <path d="M30 30 L25 85 C25 90 30 95 35 95 L85 95 C90 95 95 90 95 85 L90 30" />
                    <path d="M20 30 L100 30" strokeWidth="1.5" />
                    <path d="M45 30 L45 18 C45 14 49 10 53 10 L67 10 C71 10 75 14 75 18 L75 30" />
                    <path d="M50 45 L50 78" />
                    <path d="M70 45 L70 78" />
                    <path d="M60 45 L60 78" opacity="0.6" />
                    <path d="M5 30 L115 30" strokeWidth="0.8" opacity="0.3" />
                    <circle cx="95" cy="15" r="3" opacity="0.3" />
                    <circle cx="105" cy="25" r="2" opacity="0.2" />
                    <circle cx="15" cy="85" r="2" opacity="0.25" />
                    <circle cx="110" cy="80" r="1.5" opacity="0.2" />
                  </svg>
                </div>
                <div className="shell-placeholder-title">回收站空空如也</div>
                <div className="shell-placeholder-sub">删除的书籍会出现在这里</div>
              </div>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, padding: '12px 0' }}>
              {texts.map((t) => (
                <div key={t.id} className="history-swipe-wrapper">
                  <div className="history-swipe-actions">
                    <button
                      type="button"
                      className="history-swipe-restore"
                      onClick={() => setRestoreTextId(t.id)}
                    >
                      恢复
                    </button>
                    <button
                      type="button"
                      className="history-swipe-delete"
                      onClick={() => setPurgeTextId(t.id)}
                    >
                      彻底删除
                    </button>
                  </div>
                  <div className="history-item" style={{ cursor: 'default' }}>
                    <div className="history-item-text-icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" /></svg></div>
                    <div className="history-item-body">
                      <div className="history-item-head">
                        <span className="history-item-name">{t.title || t.filename || '未命名'}</span>
                        <span className="history-item-time">{formatTime(t.deleted_at)}</span>
                      </div>
                      <p className="history-item-preview">{t.char_count?.toLocaleString() || '0'} 字</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      <ConfirmModal
        isOpen={!!purgeId}
        title="永久删除"
        message="⚠️ 此操作不可恢复！该角色卡及其所有关联数据（对话、记忆、版本）将被永久清除，无法找回。"
        confirmText="永久删除"
        onConfirm={handlePurge}
        onCancel={() => setPurgeId(null)}
        danger
      />

      <ConfirmModal
        isOpen={!!restoreGroupId}
        title="恢复群聊"
        message="确定恢复该群聊？恢复后可在群聊列表中查看。"
        confirmText="恢复"
        onConfirm={handleRestoreGroup}
        onCancel={() => setRestoreGroupId(null)}
      />

      <ConfirmModal
        isOpen={!!purgeGroupId}
        title="永久删除"
        message="⚠️ 此操作不可恢复！该群聊及其所有消息将被永久清除，无法找回。"
        confirmText="永久删除"
        onConfirm={handlePurgeGroup}
        onCancel={() => setPurgeGroupId(null)}
        danger
      />

      <ConfirmModal
        isOpen={!!restoreTextId}
        title="恢复书籍"
        message="确定恢复该书籍？恢复后可在书籍列表中查看。"
        confirmText="恢复"
        onConfirm={handleRestoreText}
        onCancel={() => setRestoreTextId(null)}
      />

      <ConfirmModal
        isOpen={!!purgeTextId}
        title="永久删除"
        message="⚠️ 此操作不可恢复！该书籍及其所有内容将被永久清除，无法找回。"
        confirmText="永久删除"
        onConfirm={handlePurgeText}
        onCancel={() => setPurgeTextId(null)}
        danger
      />
    </div>
  )
}
