import { useCallback, useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import ErrorBox from './common/ErrorBox'
import Loading from './common/Loading'
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

  const inputRef = useRef(null)
  const [dragOver, setDragOver] = useState(false)
  const [uploading, setUploading] = useState(false)
  const isUploading = uploading || uploadProgress !== null
  const [localError, setLocalError] = useState(null)
  const [deletingId, setDeletingId] = useState(null)
  const [expandedId, setExpandedId] = useState(null)
  const [cardCounts, setCardCounts] = useState({})

  // Upload metadata modal state
  const [pendingFile, setPendingFile] = useState(null)
  const [metaTitle, setMetaTitle] = useState('')
  const [metaDesc, setMetaDesc] = useState('')
  const [metaTitleError, setMetaTitleError] = useState('')

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
      await uploadText(pendingFile, metaTitle.trim(), metaDesc.trim())
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
  }, [])

  const onDrop = (e) => {
    e.preventDefault()
    setDragOver(false)
    handleFiles(e.dataTransfer.files)
  }

  const onDelete = async (e, id) => {
    e.stopPropagation()
    if (!window.confirm('确定删除该文本？关联角色卡与会话将一并删除。')) return
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
          上传小说或剧本，用于角色识别与蒸馏
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
          {`支持 ${ALLOWED_EXT.join(' ')}，单文件最大 100MB`}
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
                    {t.char_count > 80000 && (
                      <span className="text-meta">大文本蒸馏可能需要较长时间</span>
                    )}
                    {cardCounts[t.id] > 0 && (
                      <span className="text-list-badge">{cardCounts[t.id]} 个角色</span>
                    )}
                  </div>
                  <div className="text-list-meta">
                    <span className="text-list-chars">
                      {`${formatCount(t.char_count)} 字`}
                    </span>
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
    </div>
  )
}
