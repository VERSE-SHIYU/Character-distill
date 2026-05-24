import { useCallback, useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import ErrorBox from './common/ErrorBox'
import Loading from './common/Loading'
import ConfirmModal from './common/ConfirmModal'
import { fetchWithTimeout } from '../api/client'

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
  if (n <= 100000) return { icon: '⚡', text: '预计蒸馏 1-2 分钟' }
  if (n <= 500000) return { icon: '📖', text: '预计蒸馏 3-5 分钟' }
  if (n <= limit) return { icon: '📚', text: `大文本，预计蒸馏 5-8 分钟` }
  return { icon: '❌', text: `超出 ${limitText} 字上限，请分卷上传` }
}

function formatTime(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('zh-CN', {
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
  const deleteText = useAppStore((s) => s.deleteText)
  const selectText = useAppStore((s) => s.selectText)
  const currentTextId = useAppStore((s) => s.currentTextId)
  const setCurrentTextDetailId = useAppStore((s) => s.setCurrentTextDetailId)
  const setView = useAppStore((s) => s.setView)

  const inputRef = useRef(null)
  const [dragOver, setDragOver] = useState(false)
  const [uploading, setUploading] = useState(false)
  const isUploading = uploading || uploadProgress !== null
  const [localError, setLocalError] = useState(null)
  const [deletingId, setDeletingId] = useState(null)
  const [deleteConfirmId, setDeleteConfirmId] = useState(null)
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
  }

  const handleConfirmDelete = async () => {
    const id = deleteConfirmId
    setDeleteConfirmId(null)
    setDeletingId(id)
    setLocalError(null)
    try {
      await deleteText(id)
    } catch (err) {
      setLocalError(err.message || '删除失败')
    } finally {
      setDeletingId(null)
    }
  }

  const displayError = localError || error

  return (
    <div className="text-panel panel">
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
        <div className="text-upload-icon">{'\u{1F4C4}'}</div>
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
          <ul className="text-list">
            {texts.map((t) => (
              <li
                key={t.id}
                className={`text-list-item${currentTextId === t.id ? ' selected' : ''}`}
              >
                <button
                  type="button"
                  className="text-list-main"
                  onClick={() => selectText(t.id)}
                >
                  <div className="text-list-headline">
                    <span className="text-list-filename" title={t.title || t.filename}>
                      {t.title || t.filename || '未命名'}
                    </span>
                    {(() => {
                      const est = timeEstimate(t.char_count, t.text_type)
                      return est ? <span className={`text-meta${t.char_count > (t.text_type === 'chat' ? 2000000 : 1000000) ? ' chars-red' : ''}`}>{est.icon} {est.text}</span> : null
                    })()}
                    {cardCounts[t.id] > 0 && (
                      <span className="text-list-badge">{cardCounts[t.id]} 个角色</span>
                    )}
                  </div>
                  <div className="text-list-meta">
                    <span className={`text-list-chars ${charCountClass(t.char_count)}`}>
                      {`${formatCount(t.char_count)} 字`}
                    </span>
                    {t.text_type === 'chat' && t.original_char_count != null && t.original_char_count !== t.char_count && (
                      <span className="text-list-cleaned">
                        {`已清洗：${formatCount(t.original_char_count)} 字 → ${formatCount(t.char_count)} 字（保留 ${(t.char_count / t.original_char_count * 100).toFixed(0)}%）`}
                      </span>
                    )}
                    <span className="text-list-time">
                      {formatTime(t.created_at)}
                    </span>
                  </div>
                  <button
                    type="button"
                    className="text-list-expand-toggle"
                    onClick={(e) => {
                      e.stopPropagation()
                      setExpandedId(expandedId === t.id ? null : t.id)
                    }}
                    title={expandedId === t.id ? '收起预览' : '展开预览'}
                  >
                    {expandedId === t.id ? '▲' : '▼'}
                  </button>
                </button>

                {t.description && (
                  <div className="text-list-desc">{t.description}</div>
                )}

                {expandedId === t.id && t.preview && (
                  <div className="text-list-preview">
                    <p className="text-list-preview-text">{t.preview}{t.char_count > 300 ? '…' : ''}</p>
                    <p className="text-list-preview-meta">
                      {`${formatCount(t.char_count)} 字 · 已导入 ${formatTime(t.created_at)}`}
                    </p>
                  </div>
                )}

                <div className="text-list-actions">
                  <button
                    type="button"
                    className="btn-ghost text-list-action-btn"
                    onClick={(e) => {
                      e.stopPropagation()
                      selectText(t.id)
                    }}
                  >
                    管理角色
                  </button>
                  <button
                    type="button"
                    className="btn-ghost text-list-action-btn"
                    onClick={(e) => {
                      e.stopPropagation()
                      setCurrentTextDetailId(t.id)
                      setView('textDetail')
                    }}
                    title="查看详情"
                  >
                    详情
                  </button>
                  {t.text_type === 'chat' && (
                    <button
                      type="button"
                      className="btn-ghost text-list-action-btn"
                      onClick={async (e) => {
                        e.stopPropagation()
                        try {
                          const res = await fetchWithTimeout(`/api/text/${t.id}/download-cleaned`)
                          const blob = await res.blob()
                          const url = URL.createObjectURL(blob)
                          const a = document.createElement('a')
                          a.href = url
                          a.download = `${t.title || 'chat'}_cleaned.txt`
                          document.body.appendChild(a)
                          a.click()
                          a.remove()
                          URL.revokeObjectURL(url)
                        } catch { /* ignore */ }
                      }}
                      title="下载清洗后文本"
                    >
                      {'\u{1F4E5}'} 下载清洗文本
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn-ghost text-list-action-btn text-list-action-danger"
                    disabled={deletingId === t.id}
                    onClick={(e) => onDelete(e, t.id)}
                    title="删除"
                  >
                    {deletingId === t.id ? '…' : '删除'}
                  </button>
                </div>
              </li>
            ))}
          </ul>
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
                  <span className="text-type-icon">{'\u{1F4D6}'}</span>
                  <span className="text-type-label">小说/故事/剧本</span>
                </button>
                <button
                  type="button"
                  className={`text-type-option${textType === 'chat' ? ' active' : ''}`}
                  onClick={() => setTextType('chat')}
                >
                  <span className="text-type-icon">{'\u{1F4AC}'}</span>
                  <span className="text-type-label">聊天记录</span>
                </button>
              </div>
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

      <ConfirmModal
        isOpen={!!deleteConfirmId}
        title="删除文本"
        message="确定删除该文本？关联角色卡与会话将一并删除。"
        confirmText="删除"
        onConfirm={handleConfirmDelete}
        onCancel={() => setDeleteConfirmId(null)}
        danger
      />
    </div>
  )
}
