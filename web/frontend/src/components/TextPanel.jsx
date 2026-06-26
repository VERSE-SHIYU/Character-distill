import { useCallback, useEffect, useRef, useState, Fragment } from 'react'
import { createPortal } from 'react-dom'
import useAppStore from '../store/useAppStore'
import ErrorBox from './common/ErrorBox'
import Loading from './common/Loading'
import ConfirmModal from './common/ConfirmModal'
import { fetchWithTimeout, getAuthHeaders } from '../api/client'
import { MessageSquare, Book, File } from './common/Icon'
import { parseCardJson } from '../utils/card'
import Avatar from './common/Avatar'
import EditCardModal from './EditCardModal'

const ALLOWED_EXT = ['.txt', '.md', '.json', '.csv', '.log', '.pdf', '.docx']
const MAX_BYTES = 100 * 1024 * 1024

function extOf(name) {
  const i = name.lastIndexOf('.')
  return i >= 0 ? name.slice(i).toLowerCase() : ''
}

function validateFile(file) {
  const ext = extOf(file.name)
  if (!ALLOWED_EXT.includes(ext)) {
    return `不支持格式，仅允许：${ALLOWED_EXT.join(' ')}`
  }
  if (file.size > MAX_BYTES) {
    return '文件超过 100MB 上限'
  }
  return null
}

function formatCount(n) {
  if (n == null) return '—'
  return Number(n).toLocaleString('zh-CN')
}

function charCountClass(n) {
  if (n == null) return ''
  if (n <= 100000) return 'chars-green'
  if (n <= 500000) return 'chars-blue'
  if (n <= 1000000) return 'chars-orange'
  return 'chars-red'
}

function timeEstimate(n, textType) {
  if (n == null) return null
  const isChat = textType === 'chat'
  const limit = isChat ? 2000000 : 1000000
  const limitText = isChat ? '200 万' : '100 万'
  const iconSize = 14
  if (n <= 100000) return { icon: null, text: '⚡ 预计蒸馏 1-2 分钟' }
  if (n <= 500000) return { icon: <Book size={iconSize} />, text: '预计蒸馏 3-5 分钟' }
  if (n <= limit) return { icon: <Book size={iconSize} />, text: `大文本，预计蒸馏 5-8 分钟` }
  return { icon: null, text: `❌ 超出 ${limitText} 字上限，请分卷上传` }
}

function formatTime(iso) {
  if (!iso) return '—'
  try {
    const s = iso.includes('T') && !iso.endsWith('Z') && !iso.includes('+') ? iso + 'Z' : iso
    return new Date(s).toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

export default function TextPanel() {
  const texts = useAppStore((s) => s.texts)
  const loading = useAppStore((s) => s.loading)
  const error = useAppStore((s) => s.error)
  const loadTexts = useAppStore((s) => s.loadTexts)
  const uploadText = useAppStore((s) => s.uploadText)
  const uploadProgress = useAppStore((s) => s.uploadProgress)
  const uploadTaskProgress = useAppStore((s) => s.uploadTaskProgress)
  const deleteText = useAppStore((s) => s.deleteText)
  const selectText = useAppStore((s) => s.selectText)
  const currentTextId = useAppStore((s) => s.currentTextId)
  const setCurrentTextDetailId = useAppStore((s) => s.setCurrentTextDetailId)
  const setView = useAppStore((s) => s.setView)
  const setCurrentMarketCardId = useAppStore((s) => s.setCurrentMarketCardId)
  const startChat = useAppStore((s) => s.startChat)

  const [activeTab, setActiveTab] = useState('text') // 'text' | 'character'

  const inputRef = useRef(null)
  const [dragOver, setDragOver] = useState(false)
  const [uploading, setUploading] = useState(false)
  const isUploading = uploading || uploadProgress !== null
  const [localError, setLocalError] = useState(null)
  const [deletingId, setDeletingId] = useState(null)
  const [deleteConfirmId, setDeleteConfirmId] = useState(null)
  const [deleteImpact, setDeleteImpact] = useState(null)
  const [keepCards, setKeepCards] = useState(false)
  const [expandedId, setExpandedId] = useState(null)
  const [cardCounts, setCardCounts] = useState({})

  // Upload metadata modal state
  const [pendingFile, setPendingFile] = useState(null)
  const [metaTitle, setMetaTitle] = useState('')
  const [metaDesc, setMetaDesc] = useState('')
  const [metaTitleError, setMetaTitleError] = useState('')
  const [textType, setTextType] = useState('story')

  useEffect(() => {
    loadTexts()
  }, [loadTexts])

  // Load card counts for each text
  useEffect(() => {
    texts.forEach(async (t) => {
      try {
        const res = await fetchWithTimeout(`/api/distill/cards/by-text/${t.id}`)
        if (res.ok) {
          const data = await res.json()
          setCardCounts((cc) => ({ ...cc, [t.id]: data.length }))
        }
      } catch { /* ignore */ }
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [texts])

  const handleFiles = useCallback(
    (fileList) => {
      if (!fileList?.length) return
      setLocalError(null)
      const file = fileList[0]
      const err = validateFile(file)
      if (err) {
        setLocalError(err)
        return
      }
      setPendingFile(file)
      setMetaTitle('')
      setMetaDesc('')
      setMetaTitleError('')
      setTextType('story')
    },
    [],
  )

  const handleConfirmUpload = useCallback(async () => {
    if (!metaTitle.trim()) {
      setMetaTitleError('请输入标题')
      return
    }
    if (!pendingFile) return
    setUploading(true)
    setPendingFile(null)
    try {
      await uploadText(pendingFile, metaTitle.trim(), metaDesc.trim(), textType)
    } catch (e) {
      setLocalError(e.message || '上传失败')
    } finally {
      setUploading(false)
    }
  }, [pendingFile, metaTitle, metaDesc, uploadText])

  const handleCancelUpload = useCallback(() => {
    setPendingFile(null)
    setMetaTitle('')
    setMetaDesc('')
    setMetaTitleError('')
    setTextType('story')
  }, [])

  const onDrop = (e) => {
    e.preventDefault()
    setDragOver(false)
    handleFiles(e.dataTransfer.files)
  }

  const onDelete = async (e, id) => {
    e.stopPropagation()
    setDeleteConfirmId(id)
    setKeepCards(false)
    // Fetch impact stats
    try {
      const res = await fetchWithTimeout(`/api/text/${id}/deletion-impact`)
      if (res.ok) {
        const data = await res.json()
        setDeleteImpact(data)
      } else {
        setDeleteImpact(null)
      }
    } catch {
      setDeleteImpact(null)
    }
  }

  const handleConfirmDelete = async () => {
    const id = deleteConfirmId
    setDeleteConfirmId(null)
    setDeleteImpact(null)
    setDeletingId(id)
    setLocalError(null)
    try {
      await deleteText(id, keepCards)
    } catch (err) {
      setLocalError(err.message || '删除失败')
    } finally {
      setDeletingId(null)
    }
  }

  const displayError = localError || error

  return (
    <div className="creation-panel panel">
      {/* ── Desktop Tab bar ── */}
      <div className="creation-tab-bar">
        <button
          type="button"
          className={`creation-tab${activeTab === 'text' ? ' active' : ''}`}
          onClick={() => setActiveTab('text')}
        >
          文本管理
        </button>
        <button
          type="button"
          className={`creation-tab${activeTab === 'character' ? ' active' : ''}`}
          onClick={() => setActiveTab('character')}
        >
          角色管理
        </button>
      </div>

      {activeTab === 'text' ? (
        <>
      <header className="panel-header">
        <h1 className="panel-title">文本管理</h1>
        <p className="panel-desc">
          上传小说、剧本或聊天记录，用于角色识别与蒸馏
        </p>
      </header>

      {displayError && (
        <ErrorBox message={displayError} onDismiss={() => setLocalError(null)} />
      )}

      <section
        className={`text-upload-zone${dragOver ? ' drag-over' : ''}${isUploading ? ' uploading' : ''}`}
        onDragEnter={(e) => { e.preventDefault(); setDragOver(true) }}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={(e) => { e.preventDefault(); setDragOver(false) }}
        onDrop={onDrop}
      >
        <input
          ref={inputRef}
          type="file"
          className="text-upload-input"
          accept={ALLOWED_EXT.join(',')}
          onChange={(e) => { handleFiles(e.target.files); e.target.value = '' }}
        />
        <div className="text-upload-icon"><File size={24} /></div>
        <p className="text-upload-hint">拖拽文件到此处，或</p>
        <button
          type="button"
          className="btn-primary"
          disabled={isUploading}
          onClick={() => inputRef.current?.click()}
        >
          {isUploading ? '上传中…' : '选择文件上传'}
        </button>
        <p className="text-upload-meta">
          {`支持 ${ALLOWED_EXT.join(' ')} · 小说上限 100 万字 · 聊天记录上限 200 万字 · 单文件最大 100MB`}
        </p>
      </section>

      {uploadProgress !== null && (
        <div className="upload-progress">
          <div className="progress-bar" style={{ width: `${uploadProgress}%` }} />
          <span>{uploadProgress}% 上传中...</span>
        </div>
      )}
      {uploadProgress === null && uploadTaskProgress !== null && (
        <div className="upload-progress">
          <div className="progress-bar" style={{ width: `${uploadTaskProgress.progress_pct || 0}%` }} />
          <span>{uploadTaskProgress.message || '预处理中…'}</span>
        </div>
      )}

      <section className="text-list-section">
        <div className="text-list-head">
          <h2 className="text-list-title">已导入文本</h2>
          <span className="text-list-count">{`${texts.length} 项`}</span>
        </div>

        {loading && texts.length === 0 ? (
          <Loading text="加载列表…" />
        ) : texts.length === 0 ? (
          <p className="text-list-empty">暂无文本，请先上传</p>
        ) : (
          <div className="creation-text-grid">
            {texts.map((t) => {
              const ext = extOf(t.filename || '')
              const iconColor = ext === '.txt' ? '#4a90d9' : ext === '.md' ? '#4caf50' : '#999'
              const statusClass = cardCounts[t.id] > 0 ? 'done' : 'pending'
              const statusLabel = cardCounts[t.id] > 0 ? '已完成' : '待蒸馏'
              return (
                <div key={t.id} className="creation-text-card">
                  <div className="creation-text-icon" style={{ color: iconColor }}>
                    <Book size={22} />
                  </div>
                  <div className="creation-text-info">
                    <div className="creation-text-title" title={t.title || t.filename}>
                      {t.title || t.filename || '未命名'}
                    </div>
                    <div className="creation-text-meta">
                      <span className="creation-text-filename">{t.filename}</span>
                      <span className="creation-text-chars">{formatCount(t.char_count)} 字</span>
                      <span className={`creation-text-status status-${statusClass}`}>{statusLabel}</span>
                    </div>
                  </div>
                  <div className="creation-text-actions">
                    <button
                      type="button"
                      className="btn-ghost btn-sm"
                      onClick={(e) => {
                        e.stopPropagation()
                        useAppStore.getState().setReaderTextId(t.id)
                        useAppStore.getState().setView('reader')
                      }}
                      title="阅读"
                    >
                      阅读
                    </button>
                    <button
                      type="button"
                      className="btn-ghost btn-sm"
                      onClick={(e) => {
                        e.stopPropagation()
                        selectText(t.id)
                      }}
                      title="蒸馏角色"
                    >
                      蒸馏
                    </button>
                    <button
                      type="button"
                      className="btn-ghost btn-sm creation-action-danger"
                      disabled={deletingId === t.id}
                      onClick={(e) => onDelete(e, t.id)}
                      title="删除"
                    >
                      {deletingId === t.id ? '…' : '删除'}
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </section>

      {/* Upload metadata modal */}
      {pendingFile && (
        <div className="modal-overlay" onClick={handleCancelUpload}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <h2 className="modal-title">填写文本信息</h2>
            <p className="modal-file-hint">已选择：{pendingFile.name}</p>

            <label className="modal-field">
              <span className="modal-label">
                故事标题 <span className="modal-required">*</span>
              </span>
              <input
                type="text"
                className={`modal-input${metaTitleError ? ' modal-input-error' : ''}`}
                placeholder="请输入故事标题"
                value={metaTitle}
                onChange={(e) => {
                  setMetaTitle(e.target.value)
                  if (metaTitleError) setMetaTitleError('')
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleConfirmUpload()
                }}
                autoFocus
              />
              {metaTitleError && (
                <span className="modal-field-error">{metaTitleError}</span>
              )}
            </label>

            <label className="modal-field">
              <span className="modal-label">描述/简介</span>
              <textarea
                className="modal-textarea"
                placeholder="可选，简要描述故事背景或内容"
                rows={3}
                value={metaDesc}
                onChange={(e) => setMetaDesc(e.target.value)}
              />
            </label>

            <div className="modal-field">
              <span className="modal-label">文本类型</span>
              <div className="text-type-picker">
                <button
                  type="button"
                  className={`text-type-option${textType === 'story' ? ' active' : ''}`}
                  onClick={() => setTextType('story')}
                >
                  <span className="text-type-icon"><Book size={14} /></span>
                  <span className="text-type-label">小说/故事/剧本</span>
                </button>
                <button
                  type="button"
                  className={`text-type-option${textType === 'classic' ? ' active' : ''}`}
                  onClick={() => setTextType('classic')}
                >
                  <span className="text-type-icon"><Book size={14} /></span>
                  <span className="text-type-label">名著/严肃文学</span>
                </button>
                <button
                  type="button"
                  className={`text-type-option${textType === 'chat' ? ' active' : ''}`}
                  onClick={() => setTextType('chat')}
                >
                  <span className="text-type-icon"><MessageSquare size={14} /></span>
                  <span className="text-type-label">聊天记录</span>
                </button>
              </div>
              {textType === 'classic' && (
                <p style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 6, lineHeight: 1.5 }}>
                  名著模式将进行深度文本预处理，上传时间较长
                </p>
              )}
            </div>

            <div className="modal-actions">
              <button
                type="button"
                className="btn-secondary modal-cancel-btn"
                onClick={handleCancelUpload}
              >
                取消
              </button>
              <button
                type="button"
                className="btn-primary modal-confirm-btn"
                onClick={handleConfirmUpload}
              >
                确认上传
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete confirmation modal with impact stats + keep_cards option */}
      {deleteConfirmId && (
        <div className="modal-overlay" onClick={() => { setDeleteConfirmId(null); setDeleteImpact(null) }}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <h2 className="modal-title" style={{ color: 'var(--danger)' }}>删除文本</h2>

            {deleteImpact ? (
              <p className="modal-message">
                该操作将影响 <strong>{deleteImpact.card_count}</strong> 个角色卡、
                <strong>{deleteImpact.session_count}</strong> 段对话、
                共 <strong>{deleteImpact.message_count}</strong> 条消息。
              </p>
            ) : (
              <p className="modal-message">正在获取影响范围…</p>
            )}

            <label className="modal-checkbox" style={{
              display: 'flex', alignItems: 'center', gap: 8, margin: '12px 0',
              cursor: 'pointer', fontSize: 14,
            }}>
              <input
                type="checkbox"
                checked={keepCards}
                onChange={(e) => setKeepCards(e.target.checked)}
              />
              保留角色卡与对话（角色将脱离本书独立存在）
            </label>

            <div className="modal-actions" style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => { setDeleteConfirmId(null); setDeleteImpact(null) }}
              >
                取消
              </button>
              <button
                type="button"
                className="btn-primary"
                style={{ background: 'var(--danger)', borderColor: 'var(--danger)', color: '#fff' }}
                onClick={handleConfirmDelete}
              >
                删除
              </button>
            </div>
          </div>
        </div>
      )}
        </>
      ) : (
        <CharacterManagement
          setView={setView}
          selectText={selectText}
          startChat={startChat}
          setCurrentMarketCardId={setCurrentMarketCardId}
        />
      )}
    </div>
  )
}

/* ==============================
   角色管理 Tab（全部角色卡网格）
   ============================== */

function CharacterManagement({ setView, selectText, startChat, setCurrentMarketCardId }) {
  const texts = useAppStore((s) => s.texts)
  const loadTexts = useAppStore((s) => s.loadTexts)
  const [allCards, setAllCards] = useState([])
  const [loading, setLoading] = useState(true)
  const [filterTextId, setFilterTextId] = useState('')
  const [menuOpen, setMenuOpen] = useState(null)
  const [editCard, setEditCard] = useState(null)
  const [expandedGroups, setExpandedGroups] = useState({})
  const [deleteTarget, setDeleteTarget] = useState(null)
  const menuRef = useRef(null)

  // Close menu on outside click
  useEffect(() => {
    const handler = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setMenuOpen(null)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  // Load all cards
  useEffect(() => {
    if (texts.length === 0) { loadTexts(); return }
    let cancelled = false
    ;(async () => {
      setLoading(true)
      const all = []
      // Per-text cards
      for (const t of texts) {
        try {
          const res = await fetchWithTimeout(`/api/distill/cards/by-text/${t.id}`)
          if (res.ok) {
            const cards = await res.json()
            for (const c of cards) {
              all.push({ ...c, _textInfo: t, _source: t.title || t.filename })
            }
          }
        } catch {}
      }
      // Standalone cards
      try {
        const res = await fetchWithTimeout('/api/distill/cards/standalone', { headers: { ...getAuthHeaders() } })
        if (res.ok) {
          const cards = await res.json()
          for (const c of cards) {
            all.push({ ...c, _source: '来自市场' })
          }
        }
      } catch {}
      if (!cancelled) setAllCards(all)
      setLoading(false)
    })()
    return () => { cancelled = true }
  }, [texts, loadTexts])

  const filtered = filterTextId
    ? allCards.filter((c) => c.text_id === filterTextId)
    : allCards

  const sourceOptions = [...new Set(allCards.map((c) => c._source || '未知').filter(Boolean))]

  const distinctTextIds = [...new Set(allCards.filter((c) => c.text_id).map((c) => c.text_id))]

  const grouped = (() => {
    const map = new Map()
    filtered.forEach(c => {
      const data = parseCardJson(c)
      const name = data.name || c.name || '?'
      if (!map.has(name)) map.set(name, [])
      map.get(name).push(c)
    })
    const result = []
    map.forEach((cards, name) => {
      cards.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))
      result.push({ name, latest: cards[0], versions: cards })
    })
    return result
  })()

  return (
    <div className="creation-char-section">
      <header className="panel-header">
        <h1 className="panel-title">角色管理</h1>
        <p className="panel-desc">管理所有蒸馏角色卡，点击 ⋯ 进行操作</p>
      </header>

      {/* Source filter */}
      {sourceOptions.length > 0 && (
        <div className="creation-char-filter-bar">
          <button
            type="button"
            className={`creation-char-filter-pill${filterTextId === '' ? ' active' : ''}`}
            onClick={() => setFilterTextId('')}
          >
            全部 ({allCards.length})
          </button>
          {distinctTextIds.map((tid) => {
            const t = texts.find((tx) => tx.id === tid)
            const label = t?.title || t?.filename || tid.slice(0, 8)
            const shortLabel = label.length > 8 ? label.slice(0, 8) + '…' : label
            const count = allCards.filter((c) => c.text_id === tid).length
            return (
              <button
                key={tid}
                type="button"
                className={`creation-char-filter-pill${filterTextId === tid ? ' active' : ''}`}
                onClick={() => setFilterTextId(tid)}
                title={label}
              >
                {shortLabel} ({count})
              </button>
            )
          })}
        </div>
      )}

      {loading ? (
        <Loading text="加载角色…" />
      ) : filtered.length === 0 ? (
        <div className="creation-char-empty">
          <p>暂无角色卡，先去上传文本蒸馏角色吧</p>
          <button className="btn-primary" onClick={() => setView('text')}>前往文本管理</button>
        </div>
      ) : (
        <div className="creation-char-grid">
          {grouped.map((group) => {
            const c = group.latest
            const data = parseCardJson(c)
            const name = data.name || c.name || '?'
            const identity = data.identity || ''
            const isPublic = !!c.published_id
            const sourceText = c._source || ''
            const createdAt = c.created_at || ''
            const hasVersions = group.versions.length > 1
            const isExpanded = !!expandedGroups[name]
            return (
              <Fragment key={c.id}>
                <div className="creation-char-card">
                  <Avatar name={name} src={null} size={48} />
                  <div className="creation-char-info">
                    <div className="creation-char-name-row">
                      <span className="creation-char-name">{name}</span>
                      <span className={`creation-char-status${isPublic ? ' public' : ''}`}>
                        <span className={`status-dot ${isPublic ? 'public' : 'private'}`} />
                        {isPublic ? '已公开' : '私有'}
                      </span>
                    </div>
                    <div className="creation-char-identity">{identity}</div>
                    <div className="creation-char-footer">
                      {sourceText && <span className="creation-char-source" title={sourceText}>{sourceText.length > 10 ? sourceText.slice(0, 10) + '…' : sourceText}</span>}
                      {createdAt && <span className="creation-char-time">{formatTime(createdAt)}</span>}
                      {hasVersions && (
                        <span className="creation-char-version-badge" onClick={(e) => { e.stopPropagation(); setExpandedGroups(prev => ({ ...prev, [name]: !isExpanded })) }}>
                          {isExpanded ? '收起' : `共${group.versions.length}个版本`}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="creation-char-menu-wrap">
                    <button
                      type="button"
                      className="creation-char-menu-btn"
                      onClick={(e) => { e.stopPropagation(); setMenuOpen(menuOpen === c.id ? null : c.id) }}
                    >
                      ⋯
                    </button>
                    {menuOpen === c.id && (
                      <div className="creation-char-dropdown" ref={menuRef}>
                        <button type="button" onClick={() => { setMenuOpen(null); setEditCard(c) }}>
                          编辑
                        </button>
                        <button type="button" onClick={async () => {
                          setMenuOpen(null)
                          try {
                            await startChat(c)
                          } catch {}
                        }}>
                          聊天
                        </button>
                        <button type="button" onClick={() => {
                          setMenuOpen(null)
                          setCurrentMarketCardId(c.id)
                          setView('marketCardDetail')
                        }}>
                          发布到市场
                        </button>
                        <button type="button" className="danger" onClick={() => {
                          setMenuOpen(null)
                          setDeleteTarget(c.id)
                        }}>
                          删除
                        </button>
                      </div>
                    )}
                  </div>
                </div>
                {hasVersions && isExpanded && (
                  <div className="creation-char-version-list">
                    {group.versions.slice(1).map((vc) => {
                      const vData = parseCardJson(vc)
                      const vIdentity = vData.identity || ''
                      const vIsPublic = !!vc.published_id
                      const vSourceText = vc._source || ''
                      const vCreatedAt = vc.created_at || ''
                      return (
                        <div key={vc.id} className="creation-char-card-version">
                          <Avatar name={name} src={null} size={32} />
                          <div className="creation-char-info">
                            <div className="creation-char-name-row">
                              <span className="creation-char-name">{name}</span>
                              <span className={`creation-char-status${vIsPublic ? ' public' : ''}`}>
                                <span className={`status-dot ${vIsPublic ? 'public' : 'private'}`} />
                                {vIsPublic ? '已公开' : '私有'}
                              </span>
                            </div>
                            <div className="creation-char-identity">{vIdentity}</div>
                            <div className="creation-char-footer">
                              {vSourceText && <span className="creation-char-source" title={vSourceText}>{vSourceText.length > 10 ? vSourceText.slice(0, 10) + '…' : vSourceText}</span>}
                              {vCreatedAt && <span className="creation-char-time">{formatTime(vCreatedAt)}</span>}
                            </div>
                          </div>
                          <div className="creation-char-menu-wrap">
                            <button
                              type="button"
                              className="creation-char-menu-btn"
                              onClick={(e) => { e.stopPropagation(); setMenuOpen(menuOpen === vc.id ? null : vc.id) }}
                            >
                              ⋯
                            </button>
                            {menuOpen === vc.id && (
                              <div className="creation-char-dropdown" ref={menuRef}>
                                <button type="button" onClick={() => { setMenuOpen(null); setEditCard(vc) }}>
                                  编辑
                                </button>
                                <button type="button" onClick={async () => {
                                  setMenuOpen(null)
                                  try { await startChat(vc) } catch {}
                                }}>
                                  聊天
                                </button>
                                <button type="button" onClick={() => {
                                  setMenuOpen(null)
                                  setCurrentMarketCardId(vc.id)
                                  setView('marketCardDetail')
                                }}>
                                  发布到市场
                                </button>
                                <button type="button" className="danger" onClick={() => {
                                  setMenuOpen(null)
                                  setDeleteTarget(vc.id)
                                }}>
                                  删除
                                </button>
                              </div>
                            )}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )}
              </Fragment>
            )
          })}
        </div>
      )}

      {editCard && (
        <EditCardModal
          isOpen={!!editCard}
          data={parseCardJson(editCard)}
          cardId={editCard.id || editCard.card_id}
          onSave={async (cardJson) => {
            try {
              await fetchWithTimeout(`/api/distill/card/${editCard.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
                body: JSON.stringify({ card_json: JSON.stringify(cardJson) }),
              })
              setEditCard(null)
              // Refresh all cards
              const res = await fetchWithTimeout(`/api/distill/cards/by-text/${editCard.text_id}`)
              if (res.ok) {
                const cards = await res.json()
                setAllCards((prev) => prev.map((c) => c.text_id === editCard.text_id
                  ? cards.find((nc) => nc.id === c.id) || c
                  : c
                ))
              }
            } catch {}
          }}
          onClose={() => setEditCard(null)}
        />
      )}

      <ConfirmModal
        isOpen={!!deleteTarget}
        title="移入回收站"
        message={`确定删除该角色卡？将移入回收站，可在回收站中恢复。`}
        confirmText="移入回收站"
        onConfirm={async () => {
          const id = deleteTarget
          setDeleteTarget(null)
          try {
            await fetchWithTimeout(`/api/cards/${id}`, {
              method: 'DELETE',
              headers: { ...getAuthHeaders() },
            })
            setAllCards((prev) => prev.filter((x) => x.id !== id))
          } catch (err) {
            console.error('Delete card failed:', err)
          }
        }}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  )
}
