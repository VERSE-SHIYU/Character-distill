import { useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout } from '../api/client'

const PRESET_VOICES = [
  { id: 'xiaoxiao', label: '晓晓（女，活泼）' },
  { id: 'yunxi',    label: '云希（男，青年）' },
  { id: 'xiaoyi',   label: '晓伊（女，温柔）' },
  { id: 'yunyang',  label: '云扬（男，新闻播报）' },
]

export default function VoicePanel() {
  const voiceStatus = useAppStore((s) => s.voiceStatus)
  const checkVoiceStatus = useAppStore((s) => s.checkVoiceStatus)
  const voiceEnabled = useAppStore((s) => s.voiceEnabled)
  const setVoiceEnabled = useAppStore((s) => s.setVoiceEnabled)
  const voiceList = useAppStore((s) => s.voiceList)
  const loadVoices = useAppStore((s) => s.loadVoices)
  const currentCard = useAppStore((s) => s.currentCard)
  const voiceRefInfo = useAppStore((s) => s.voiceRefInfo)
  const loadVoiceRef = useAppStore((s) => s.loadVoiceRef)
  const uploadRefAudio = useAppStore((s) => s.uploadRefAudio)
  const deleteVoiceRef = useAppStore((s) => s.deleteVoiceRef)
  const uploadCustomVoice = useAppStore((s) => s.uploadCustomVoice)
  const deleteCustomVoice = useAppStore((s) => s.deleteCustomVoice)

  const [selectedVoice, setSelectedVoice] = useState(() =>
    localStorage.getItem('tts_voice') || 'xiaoxiao'
  )

  // Custom voice upload state
  const [customFile, setCustomFile] = useState(null)
  const [customName, setCustomName] = useState('')
  const [customUploading, setCustomUploading] = useState(false)
  const [customError, setCustomError] = useState('')
  const customInputRef = useRef(null)

  // Ref audio state
  const [refFile, setRefFile] = useState(null)
  const [refPromptText, setRefPromptText] = useState('')
  const [refUploading, setRefUploading] = useState(false)
  const [refError, setRefError] = useState('')
  const [refSuccess, setRefSuccess] = useState('')
  const refInputRef = useRef(null)

  // Preview state
  const [previewingId, setPreviewingId] = useState(null)
  const previewAudioRef = useRef(null)

  // Deleting state
  const [deletingId, setDeletingId] = useState(null)

  useEffect(() => { loadVoices() }, [loadVoices])
  useEffect(() => { checkVoiceStatus() }, [checkVoiceStatus])

  const cardId = currentCard?.id || currentCard?.card_id || null
  useEffect(() => { loadVoiceRef(cardId) }, [cardId, loadVoiceRef])

  const handleVoiceChange = (voice) => {
    setSelectedVoice(voice)
    localStorage.setItem('tts_voice', voice)
  }

  // Custom voice list (filter out presets)
  const customVoices = voiceList.filter((v) => v.type === 'custom')

  // ---- Custom voice upload ----
  const handleCustomFileSelect = (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    const ext = file.name.split('.').pop()?.toLowerCase()
    if (!['wav', 'mp3', 'flac', 'ogg', 'm4a', 'mp4', 'mov', 'avi', 'mkv', 'webm'].includes(ext)) {
      setCustomError('仅支持 wav/mp3/flac/ogg/m4a 音频和 mp4/mov 等视频格式')
      return
    }
    if (file.size > 20 * 1024 * 1024) {
      setCustomError('文件大小不能超过 20MB')
      return
    }
    setCustomFile(file)
    setCustomError('')
    if (!customName) setCustomName(file.name.replace(/\.\w+$/, ''))
  }

  const handleCustomUpload = async () => {
    if (!customFile || !customName.trim()) {
      setCustomError('请选择文件并填写音色名称')
      return
    }
    setCustomUploading(true)
    setCustomError('')
    try {
      await uploadCustomVoice(customFile, customName.trim())
      setCustomFile(null)
      setCustomName('')
      if (customInputRef.current) customInputRef.current.value = ''
    } catch (err) {
      setCustomError(err.message || '上传失败')
    } finally {
      setCustomUploading(false)
    }
  }

  const handleCustomDelete = async (voiceId) => {
    if (!window.confirm('确定删除该音色？')) return
    setDeletingId(voiceId)
    try {
      await deleteCustomVoice(voiceId)
      if (selectedVoice === voiceId) {
        handleVoiceChange('xiaoxiao')
      }
    } catch { /* store handles */ }
    setDeletingId(null)
  }

  // ---- Preview ----
  const handlePreview = async (voiceId) => {
    if (previewingId === voiceId) {
      if (previewAudioRef.current) previewAudioRef.current.pause()
      setPreviewingId(null)
      return
    }
    setPreviewingId(voiceId)
    try {
      const res = await fetchWithTimeout(`/api/voice/preview-audio/${voiceId}`)
      if (!res.ok) throw new Error()
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      if (previewAudioRef.current) previewAudioRef.current.pause()
      const audio = new Audio(url)
      audio.onended = () => { setPreviewingId(null); URL.revokeObjectURL(url) }
      audio.onerror = () => { setPreviewingId(null); URL.revokeObjectURL(url) }
      audio.play()
      previewAudioRef.current = audio
    } catch {
      setPreviewingId(null)
    }
  }

  useEffect(() => {
    return () => { if (previewAudioRef.current) previewAudioRef.current.pause() }
  }, [])

  // ---- Ref audio upload ----
  const handleRefFileSelect = (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    const ext = file.name.split('.').pop()?.toLowerCase()
    if (!['wav', 'mp3', 'flac', 'ogg', 'm4a', 'mp4', 'mov', 'avi', 'mkv', 'webm'].includes(ext)) {
      setRefError('不支持的音频/视频格式')
      return
    }
    if (file.size > 20 * 1024 * 1024) {
      setRefError('文件过大，最大 20MB')
      return
    }
    setRefFile(file)
    setRefError('')
    setRefSuccess('')
  }

  const handleRefUpload = async () => {
    if (!refFile || !cardId) {
      setRefError(cardId ? '请选择音频文件' : '请先在聊天页选择一个角色')
      return
    }
    setRefUploading(true)
    setRefError('')
    setRefSuccess('')
    try {
      await uploadRefAudio(refFile, cardId, refPromptText.trim())
      setRefFile(null)
      setRefPromptText('')
      setRefSuccess(`参考音频已绑定到「${currentCard?.name || cardId}」`)
      if (refInputRef.current) refInputRef.current.value = ''
      setTimeout(() => setRefSuccess(''), 4000)
    } catch (err) {
      setRefError(err.message || '上传失败')
    } finally {
      setRefUploading(false)
    }
  }

  const handleRefDelete = async () => {
    if (!cardId) return
    if (!window.confirm('确定删除参考音频？')) return
    try {
      await deleteVoiceRef(cardId)
      setRefSuccess(`「${currentCard?.name || cardId}」的参考音频已移除`)
      setTimeout(() => setRefSuccess(''), 3000)
    } catch { /* store handles */ }
  }

  const hasRef = voiceRefInfo && voiceRefInfo.exists

  return (
    <div className="voice-panel panel">
      <header className="panel-header">
        <h1 className="panel-title">语音设置</h1>
        <p className="panel-desc">TTS 音色管理、音色克隆与语音服务状态</p>
      </header>

      {/* ===== Section 1: Enable + Preset voices ===== */}
      <div className="voice-section">
        <label className="voice-toggle-row">
          <span className="voice-toggle-label">启用语音</span>
          <input
            type="checkbox"
            checked={voiceEnabled}
            onChange={(e) => setVoiceEnabled(e.target.checked)}
          />
        </label>

        {voiceEnabled && (
          <div className="voice-options">
            <h3 className="voice-options-title">预设音色</h3>
            {PRESET_VOICES.map(({ id, label }) => (
              <label key={id} className="voice-option">
                <input
                  type="radio"
                  name="voice"
                  value={id}
                  checked={selectedVoice === id}
                  onChange={() => handleVoiceChange(id)}
                />
                <span className="voice-option-label">{label}</span>
                <button
                  type="button"
                  className={`btn-ghost btn-sm voice-preview-btn${previewingId === id ? ' voice-preview-active' : ''}`}
                  onClick={(e) => { e.preventDefault(); handlePreview(id) }}
                >
                  {previewingId === id ? '\u23F9 停止' : '\u25B6 试听'}
                </button>
              </label>
            ))}

            {/* Custom voices with preview + delete */}
            {customVoices.length > 0 && (
              <>
                <h3 className="voice-options-title">
                  自定义音色
                  <span className="voice-list-count">{customVoices.length}</span>
                </h3>
                <ul className="voice-list">
                  {customVoices.map((v) => (
                    <li key={v.voice_id} className="voice-list-item">
                      <label className="voice-list-radio">
                        <input
                          type="radio"
                          name="voice"
                          value={v.voice_id}
                          checked={selectedVoice === v.voice_id}
                          onChange={() => handleVoiceChange(v.voice_id)}
                        />
                      </label>
                      <div className="voice-list-info">
                        <span className="voice-list-name">{v.name}</span>
                        <span className="voice-list-meta">
                          {v.duration ? `${v.duration}s` : v.ext || 'wav'}
                        </span>
                      </div>
                      <div className="voice-list-actions">
                        <button
                          type="button"
                          className={`btn-ghost btn-sm${previewingId === v.voice_id ? ' voice-preview-active' : ''}`}
                          onClick={() => handlePreview(v.voice_id)}
                        >
                          {previewingId === v.voice_id ? '\u23F9' : '\u25B6'}
                        </button>
                        <button
                          type="button"
                          className="btn-ghost btn-sm text-list-action-danger"
                          disabled={deletingId === v.voice_id}
                          onClick={() => handleCustomDelete(v.voice_id)}
                        >
                          {deletingId === v.voice_id ? '…' : '\u2715'}
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}
      </div>

      {/* ===== Section 2: Upload custom voice ===== */}
      <div className="voice-section">
        <h3 className="voice-options-title">上传自定义音色</h3>
        <p className="voice-guide-desc">
          支持音频（wav/mp3/flac/ogg/m4a）和视频（mp4/mov）文件，视频将自动提取音频。
        </p>
        <div className="voice-upload-section">
          <div className="voice-upload-area">
            <input
              ref={customInputRef}
              type="file"
              className="voice-file-input"
              accept=".wav,.mp3,.flac,.ogg,.m4a,.mp4,.mov,.webm"
              onChange={handleCustomFileSelect}
            />
            <button
              type="button"
              className="btn-secondary btn-sm"
              onClick={() => customInputRef.current?.click()}
              disabled={customUploading}
            >
              {customFile ? `\u{1F3B5} ${customFile.name}` : '\u{1F4C2} 选择音频文件'}
            </button>
          </div>
          {customFile && (
            <div className="voice-upload-form">
              <input
                type="text"
                className="settings-input"
                placeholder="音色名称"
                value={customName}
                onChange={(e) => setCustomName(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleCustomUpload() }}
              />
              <button
                type="button"
                className="btn-primary btn-sm"
                disabled={customUploading || !customName.trim()}
                onClick={handleCustomUpload}
              >
                {customUploading ? '上传中…' : '上传'}
              </button>
            </div>
          )}
          {customError && <p className="voice-msg voice-msg-error">{customError}</p>}
        </div>
      </div>

      {/* ===== Section 3: Voice cloning ref audio (GPT-SoVITS) ===== */}
      <div className="voice-section">
        <h3 className="voice-options-title">
          音色克隆
          {voiceStatus.gptsovits
            ? <span className="voice-guide-badge"> GPT-SoVITS 已连接</span>
            : <span className="voice-guide-badge" style={{ color: '#999' }}> GPT-SoVITS 未连接</span>
          }
        </h3>

        {!cardId ? (
          <div className="voice-guide-card">
            <p className="voice-guide-desc">
              请先在聊天页选择一个角色，然后回到此处为该角色上传参考音频。
              GPT-SoVITS 会用参考音频克隆角色音色，实现角色专属语音。
            </p>
          </div>
        ) : hasRef ? (
          /* Already has ref audio */
          <div className="voice-configured-card">
            <div className="voice-configured-header">
              <span>{'\u{2705}'}</span>
              <div className="voice-list-info">
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span className="voice-list-name">
                    {currentCard?.name || '当前角色'}
                  </span>
                  <span className="voice-guide-badge" style={{ fontSize: 11, padding: '1px 6px' }}>已绑定</span>
                </div>
                <span className="voice-list-meta">
                  {voiceRefInfo.filename || '参考音频'}
                  {voiceRefInfo.ref_text && ` · "${voiceRefInfo.ref_text}"`}
                </span>
              </div>
            </div>
            <div className="voice-list-actions">
              <button
                type="button"
                className="btn-secondary btn-sm"
                onClick={() => { setRefFile(null); refInputRef.current?.click() }}
              >
                更换音频
              </button>
              <button
                type="button"
                className="btn-ghost btn-sm text-list-action-danger"
                onClick={handleRefDelete}
              >
                删除
              </button>
            </div>
            <input
              ref={refInputRef}
              type="file"
              className="voice-file-input"
              accept=".wav,.mp3,.flac,.ogg,.m4a,.mp4,.mov,.webm"
              onChange={handleRefFileSelect}
            />
            {refFile && (
              <div className="voice-upload-form">
                <input
                  type="text"
                  className="settings-input voice-prompt-input"
                  placeholder="参考音频的文字内容（提升克隆质量）"
                  value={refPromptText}
                  onChange={(e) => setRefPromptText(e.target.value)}
                />
                <button
                  type="button"
                  className="btn-primary btn-sm"
                  disabled={refUploading}
                  onClick={handleRefUpload}
                >
                  {refUploading ? '上传中…' : '上传'}
                </button>
              </div>
            )}
          </div>
        ) : (
          /* No ref audio yet */
          <div className="voice-guide-card">
            <div className="voice-guide-header">
              <span>{'\u{1F3A4}'}</span>
              <span className="voice-guide-badge">
                为「{currentCard?.name || '角色'}」上传参考音频
              </span>
            </div>
            <p className="voice-guide-desc">
              上传 30-60 秒的角色语音作为参考，GPT-SoVITS 将克隆该音色用于对话。
              支持音频（wav/mp3/flac/ogg/m4a）和视频（mp4/mov）文件，视频将自动提取音频，最大 20MB。
            </p>
            <div className="voice-upload-area">
              <input
                ref={refInputRef}
                type="file"
                className="voice-file-input"
                accept=".wav,.mp3,.flac,.ogg,.m4a,.mp4,.mov,.webm"
                onChange={handleRefFileSelect}
              />
              <button
                type="button"
                className="btn-secondary btn-sm"
                onClick={() => refInputRef.current?.click()}
                disabled={refUploading}
              >
                {refFile ? `\u{1F3B5} ${refFile.name}` : '\u{1F4C2} 选择参考音频'}
              </button>
            </div>
            {refFile && (
              <div className="voice-prompt-field">
                <input
                  type="text"
                  className="settings-input voice-prompt-input"
                  placeholder="参考音频对应的文字内容（可选，提升克隆效果）"
                  value={refPromptText}
                  onChange={(e) => setRefPromptText(e.target.value)}
                />
                <button
                  type="button"
                  className="btn-primary btn-sm"
                  disabled={refUploading}
                  onClick={handleRefUpload}
                  style={{ marginTop: 8 }}
                >
                  {refUploading ? '上传中…' : '上传参考音频'}
                </button>
              </div>
            )}
            {refError && <p className="voice-msg voice-msg-error">{refError}</p>}
            {refSuccess && <p className="voice-msg voice-msg-success">{refSuccess}</p>}
          </div>
        )}
      </div>

      {/* ===== Section 4: Service status ===== */}
      <div className="voice-section">
        <h3 className="voice-options-title">服务状态</h3>
        <div className="voice-status">
          <p>
            <span className="voice-status-dot">{'\u{1F7E2}'}</span>
            Edge TTS: 可用（微软免费）
          </p>
          <p>
            <span className="voice-status-dot">{voiceStatus.gptsovits ? '\u{1F7E2}' : '\u{1F534}'}</span>
            GPT-SoVITS: {voiceStatus.gptsovits ? '已连接' : '未连接'}
          </p>
          <p>
            <span className="voice-status-dot">{voiceStatus.funasr ? '\u{1F7E2}' : '\u{1F534}'}</span>
            FunASR: {voiceStatus.funasr ? '已连接' : '未连接'}
          </p>
          <button
            type="button"
            className="btn-ghost btn-sm"
            onClick={checkVoiceStatus}
            style={{ marginTop: 8 }}
          >
            刷新状态
          </button>
        </div>
      </div>
    </div>
  )
}
