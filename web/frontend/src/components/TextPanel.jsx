import { useCallback, useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import ErrorBox from './common/ErrorBox'
import Loading from './common/Loading'

const ALLOWED_EXT = ['.txt', '.md', '.json', '.csv', '.log']
const MAX_BYTES = 100 * 1024 * 1024

function extOf(name) {
  const i = name.lastIndexOf('.')
  return i >= 0 ? name.slice(i).toLowerCase() : ''
}

function validateFile(file) {
  const ext = extOf(file.name)
  if (!ALLOWED_EXT.includes(ext)) {
    return `\u4e0d\u652f\u6301\u7684\u683c\u5f0f\uff0c\u4ec5\u5141\u8bb8\uff1a${ALLOWED_EXT.join(' ')}`
  }
  if (file.size > MAX_BYTES) {
    return '\u6587\u4ef6\u8d85\u8fc7 100MB \u4e0a\u9650'
  }
  return null
}

function formatCount(n) {
  if (n == null) return '\u2014'
  return Number(n).toLocaleString('zh-CN')
}

function formatTime(iso) {
  if (!iso) return '\u2014'
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
  const deleteText = useAppStore((s) => s.deleteText)
  const selectText = useAppStore((s) => s.selectText)
  const currentTextId = useAppStore((s) => s.currentTextId)

  const inputRef = useRef(null)
  const [dragOver, setDragOver] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [localError, setLocalError] = useState(null)
  const [deletingId, setDeletingId] = useState(null)

  useEffect(() => {
    loadTexts()
  }, [loadTexts])

  const handleFiles = useCallback(
    async (fileList) => {
      if (!fileList?.length) return
      setLocalError(null)
      const file = fileList[0]
      const err = validateFile(file)
      if (err) {
        setLocalError(err)
        return
      }
      setUploading(true)
      try {
        await uploadText(file)
      } catch (e) {
        setLocalError(e.message || '\u4e0a\u4f20\u5931\u8d25')
      } finally {
        setUploading(false)
      }
    },
    [uploadText],
  )

  const onDrop = (e) => {
    e.preventDefault()
    setDragOver(false)
    handleFiles(e.dataTransfer.files)
  }

  const onDelete = async (e, id) => {
    e.stopPropagation()
    if (!window.confirm('\u786e\u5b9a\u5220\u9664\u8be5\u6587\u672c\uff1f\u5173\u8054\u89d2\u8272\u5361\u4e0e\u4f1a\u8bdd\u5c06\u4e00\u5e76\u5220\u9664\u3002')) return
    setDeletingId(id)
    setLocalError(null)
    try {
      await deleteText(id)
    } catch (err) {
      setLocalError(err.message || '\u5220\u9664\u5931\u8d25')
    } finally {
      setDeletingId(null)
    }
  }

  const displayError = localError || error

  return (
    <div className="text-panel panel">
      <header className="panel-header">
        <h1 className="panel-title">{'\u6587\u672c\u7ba1\u7406'}</h1>
        <p className="panel-desc">
          {'\u4e0a\u4f20\u5c0f\u8bf4\u6216\u5267\u672c\uff0c\u7528\u4e8e\u89d2\u8272\u8bc6\u522b\u4e0e\u84b8\u998f'}
        </p>
      </header>

      {displayError && (
        <ErrorBox message={displayError} onDismiss={() => setLocalError(null)} />
      )}

      <section
        className={`text-upload-zone${dragOver ? ' drag-over' : ''}${uploading ? ' uploading' : ''}`}
        onDragEnter={(e) => {
          e.preventDefault()
          setDragOver(true)
        }}
        onDragOver={(e) => {
          e.preventDefault()
          setDragOver(true)
        }}
        onDragLeave={(e) => {
          e.preventDefault()
          setDragOver(false)
        }}
        onDrop={onDrop}
      >
        <input
          ref={inputRef}
          type="file"
          className="text-upload-input"
          accept={ALLOWED_EXT.join(',')}
          onChange={(e) => {
            handleFiles(e.target.files)
            e.target.value = ''
          }}
        />
        <div className="text-upload-icon">{'\u{1F4C4}'}</div>
        <p className="text-upload-hint">{'\u62d6\u62fd\u6587\u4ef6\u5230\u6b64\u5904\uff0c\u6216'}</p>
        <button
          type="button"
          className="btn-primary"
          disabled={uploading}
          onClick={() => inputRef.current?.click()}
        >
          {uploading ? '\u4e0a\u4f20\u4e2d\u2026' : '\u9009\u62e9\u6587\u4ef6\u4e0a\u4f20'}
        </button>
        <p className="text-upload-meta">
          {`\u652f\u6301 ${ALLOWED_EXT.join(' ')}\uff0c\u5355\u6587\u4ef6\u6700\u5927 100MB`}
        </p>
      </section>

      <section className="text-list-section">
        <div className="text-list-head">
          <h2 className="text-list-title">{'\u5df2\u5bfc\u5165\u6587\u672c'}</h2>
          <span className="text-list-count">{`${texts.length} \u9879`}</span>
        </div>

        {loading && texts.length === 0 ? (
          <Loading text={'\u52a0\u8f7d\u5217\u8868\u2026'} />
        ) : texts.length === 0 ? (
          <p className="text-list-empty">{'\u6682\u65e0\u6587\u672c\uff0c\u8bf7\u5148\u4e0a\u4f20'}</p>
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
                  <span className="text-list-filename" title={t.filename}>
                    {t.filename || '\u672a\u547d\u540d'}
                  </span>
                  <span className="text-list-chars">
                    {`${formatCount(t.char_count)} \u5b57`}
                  </span>
                  <span className="text-list-time">
                    {formatTime(t.created_at)}
                  </span>
                </button>
                <button
                  type="button"
                  className="text-list-delete btn-ghost"
                  disabled={deletingId === t.id}
                  onClick={(e) => onDelete(e, t.id)}
                  title={'\u5220\u9664'}
                  aria-label={'\u5220\u9664'}
                >
                  {deletingId === t.id ? '\u2026' : '\u5220\u9664'}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
