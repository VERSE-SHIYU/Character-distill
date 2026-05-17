import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchWithTimeout } from '../api/client'
import { applyTheme, getTheme } from '../utils/theme'
import useAppStore from '../store/useAppStore'
import Loading from './common/Loading'
import ErrorBox from './common/ErrorBox'

const APP_VERSION = '0.0.0'
const GITHUB_URL = 'https://github.com'
const VOICE_SETUP_LINK = 'https://github.com/your-repo#voice-setup'
const SPEED_OPTIONS = [0.75, 1.0, 1.25, 1.5]

export default function SettingsPanel() {
  const [config, setConfig] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [theme, setTheme] = useState(getTheme)
  const [form, setForm] = useState({ base_url: '', model: '', api_key: '' })
  const [saving, setSaving] = useState(false)
  const [edgeTtsVoice, setEdgeTtsVoice] = useState(() => localStorage.getItem('tts_voice') || 'xiaoxiao')
  const [testing, setTesting] = useState(false)

  const currentCard = useAppStore((s) => s.currentCard)
  const voiceStatus = useAppStore((s) => s.voiceStatus)
  const voiceEnabled = useAppStore((s) => s.voiceEnabled)
  const voiceSpeed = useAppStore((s) => s.voiceSpeed)
  const voiceRefInfo = useAppStore((s) => s.voiceRefInfo)
  const voiceList = useAppStore((s) => s.voiceList)
  const loadVoices = useAppStore((s) => s.loadVoices)
  const setVoiceEnabled = useAppStore((s) => s.setVoiceEnabled)
  const setVoiceSpeed = useAppStore((s) => s.setVoiceSpeed)
  const uploadRefAudio = useAppStore((s) => s.uploadRefAudio)
  const loadVoiceRef = useAppStore((s) => s.loadVoiceRef)
  const deleteVoiceRef = useAppStore((s) => s.deleteVoiceRef)

  // ---- Ref audio upload state ----
  const [audioFile, setAudioFile] = useState(null)
  const [promptText, setPromptText] = useState('')
  const [uploadingRef, setUploadingRef] = useState(false)
  const [refMsg, setRefMsg] = useState(null)
  const fileInputRef = useRef(null)

  const cardId = currentCard?.card_id || currentCard?.id || null

  // Load voice ref when card changes
  useEffect(() => {
    if (cardId) {
      loadVoiceRef(cardId)
    } else {
      loadVoiceRef(null)
    }
  }, [cardId, loadVoiceRef])

  useEffect(() => { loadVoices() }, [loadVoices])

  // ---- Load config ----
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true)
      setError(null)
      try {
        const res = await fetchWithTimeout('/api/settings/config')
        const data = await res.json()
        if (!cancelled) {
          setConfig(data)
          setForm({ base_url: data.base_url || '', model: data.model || '', api_key: '' })
        }
      } catch (err) {
        console.error('[SettingsPanel] load config failed:', err)
        if (!cancelled) setError(err.message || '加载配置失败')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [])

  const setEdgeTtsVoiceAndSave = (v) => {
    setEdgeTtsVoice(v)
    localStorage.setItem('tts_voice', v)
  }

  const isCustomVoice = (voiceId) =>
    voiceList.some((v) => v.voice_id === voiceId)

  const testVoice = async () => {
    setTesting(true)
    try {
      if (isCustomVoice(edgeTtsVoice)) {
        const audio = new Audio(`/api/voice/preview-audio/${edgeTtsVoice}`)
        audio.onended = () => setTesting(false)
        audio.onerror = () => setTesting(false)
        await audio.play()
      } else {
        const res = await fetch('/api/tts/synthesize', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: '你好，这是音色测试。', voice: edgeTtsVoice }),
        })
        if (!res.ok) throw new Error(`TTS ${res.status}`)
        const blob = await res.blob()
        const url = URL.createObjectURL(blob)
        const audio = new Audio(url)
        audio.onended = () => { URL.revokeObjectURL(url); setTesting(false) }
        audio.onerror = () => { URL.revokeObjectURL(url); setTesting(false) }
        await audio.play()
      }
    } catch {
      setTesting(false)
    }
  }

  const setThemeMode = (mode) => {
    applyTheme(mode)
    setTheme(mode)
  }

  // ---- Ref audio handlers ----
  const handleFileChange = (e) => {
    const f = e.target.files?.[0]
    if (f) setAudioFile(f)
  }

  const handleUploadRef = useCallback(async () => {
    if (!audioFile || !promptText.trim() || !cardId) return
    setUploadingRef(true)
    setRefMsg(null)
    try {
      await uploadRefAudio(audioFile, cardId, promptText.trim())
      setRefMsg({ type: 'success', text: '参考音频上传成功' })
      setAudioFile(null)
      setPromptText('')
      if (fileInputRef.current) fileInputRef.current.value = ''
    } catch (err) {
      setRefMsg({ type: 'error', text: err.message || '上传失败' })
    } finally {
      setUploadingRef(false)
    }
  }, [audioFile, promptText, cardId, uploadRefAudio])

  const handleDeleteRef = useCallback(async () => {
    if (!cardId) return
    setRefMsg(null)
    try {
      await deleteVoiceRef(cardId)
      setRefMsg({ type: 'success', text: '参考音频已删除' })
    } catch (err) {
      setRefMsg({ type: 'error', text: err.message || '删除失败' })
    }
  }, [cardId, deleteVoiceRef])

  return (
    <div className="settings-panel panel">
      <header className="panel-header">
        <h1 className="panel-title">设置</h1>
        <p className="panel-desc">API 配置与界面主题</p>
      </header>

      {error && <ErrorBox message={error} onDismiss={() => setError(null)} />}

      <section className="settings-section">
        <h2 className="settings-section-title">API 配置</h2>
        <p className="settings-hint">
          修改后点击保存即可生效，无需重启服务。
        </p>
        {loading ? (
          <Loading text="加载配置…" />
        ) : (
          <div className="settings-fields">
            <label className="settings-field">
              <span className="settings-label">base_url</span>
              <input
                type="text"
                className="settings-input"
                value={form.base_url}
                onChange={(e) => setForm((f) => ({ ...f, base_url: e.target.value }))}
              />
            </label>
            <label className="settings-field">
              <span className="settings-label">model</span>
              <input
                type="text"
                className="settings-input"
                value={form.model}
                onChange={(e) => setForm((f) => ({ ...f, model: e.target.value }))}
              />
            </label>
            <label className="settings-field">
              <span className="settings-label">api_key</span>
              <input
                type="password"
                className="settings-input"
                placeholder="不修改请留空"
                value={form.api_key}
                onChange={(e) => setForm((f) => ({ ...f, api_key: e.target.value }))}
              />
            </label>
            <button
              type="button"
              className="btn-primary"
              disabled={saving}
              onClick={async () => {
                setSaving(true)
                setError(null)
                try {
                  const body = { base_url: form.base_url, model: form.model }
                  if (form.api_key) body.api_key = form.api_key
                  const res = await fetch('/api/settings/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                  })
                  if (!res.ok) throw new Error('保存失败')
                  const data = await res.json()
                  setConfig(data)
                  setForm((f) => ({ ...f, api_key: '' }))
                } catch (err) {
                  setError(err.message)
                } finally {
                  setSaving(false)
                }
              }}
            >
              {saving ? '保存中…' : '保存配置'}
            </button>
          </div>
        )}
      </section>

      <section className="settings-section">
        <h2 className="settings-section-title">主题</h2>
        <div className="settings-theme-row">
          <button
            type="button"
            className={`settings-theme-btn${theme === 'milktea' ? ' active' : ''}`}
            onClick={() => setThemeMode('milktea')}
          >
            {'\u{1F375} 奶油抹茶'}
          </button>
          <button
            type="button"
            className={`settings-theme-btn${theme === 'ocean' ? ' active' : ''}`}
            onClick={() => setThemeMode('ocean')}
          >
            {'\u{1F30A} 蓝色海盐'}
          </button>
        </div>
      </section>

      <section className="settings-section">
        <h2 className="settings-section-title">🔊 语音设置</h2>
        <label className="settings-field">
          <span className="settings-label">默认音色</span>
          <select className="settings-input" value={edgeTtsVoice} onChange={e => setEdgeTtsVoiceAndSave(e.target.value)}>
            <optgroup label="内置音色">
              <option value="xiaoxiao">晓晓（女，活泼）</option>
              <option value="yunxi">云希（男，青年）</option>
              <option value="xiaoyi">晓伊（女，温柔）</option>
              <option value="yunyang">云扬（男，新闻播报）</option>
            </optgroup>
            {voiceList.length > 0 && (
              <optgroup label="自定义音色">
                {voiceList.map((v) => (
                  <option key={v.voice_id} value={v.voice_id}>
                    {v.name}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
        </label>
        <button type="button" className="btn-primary settings-test-btn" onClick={testVoice} disabled={testing}>
          {testing ? '播放中…' : '▶ 试听'}
        </button>
      </section>

      {/* ---- Voice cloning section ---- */}
      {voiceStatus.gptsovits ? (
        <section className="settings-section">
          <h2 className="settings-section-title">🎙️ 语音功能</h2>

          {!cardId ? (
            <p className="settings-hint">请先选择一个角色，再配置音色克隆语音。</p>
          ) : !voiceRefInfo?.exists ? (
            /* State 2: GPT-SoVITS ready but no ref audio */
            <div className="voice-guide-card">
              <div className="voice-guide-header">
                <span className="voice-guide-badge">✅ 语音服务已就绪</span>
              </div>
              <p className="voice-guide-desc">
                上传角色参考音频即可启用语音回复。
                <br />
                建议 30秒-1分钟干净人声，1分钟效果最佳。
              </p>

              <div className="voice-upload-area">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".wav,.mp3"
                  className="voice-file-input"
                  onChange={handleFileChange}
                />
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => fileInputRef.current?.click()}
                >
                  {audioFile ? audioFile.name : '选择音频文件'}
                </button>
              </div>

              <label className="settings-field voice-prompt-field">
                <span className="settings-label">这段音频说了什么</span>
                <input
                  type="text"
                  className="settings-input voice-prompt-input"
                  placeholder="必填，如：这是角色在自我介绍时的录音"
                  value={promptText}
                  onChange={(e) => setPromptText(e.target.value)}
                />
              </label>

              <button
                type="button"
                className="btn-primary"
                disabled={!audioFile || !promptText.trim() || uploadingRef}
                onClick={handleUploadRef}
              >
                {uploadingRef ? '上传中…' : '上传'}
              </button>

              {refMsg && (
                <p className={`voice-msg ${refMsg.type === 'error' ? 'voice-msg-error' : 'voice-msg-success'}`}>
                  {refMsg.text}
                </p>
              )}
            </div>
          ) : (
            /* State 3: Ref audio configured */
            <div className="voice-configured-card">
              <div className="voice-configured-header">
                <span className="voice-guide-badge">✅ 已配置</span>
                <span className="voice-duration-tag">参考音频 {voiceRefInfo.duration}秒</span>
              </div>

              <div className="voice-toggle-row">
                <span className="voice-toggle-label">语音回复</span>
                <label className="voice-toggle">
                  <input
                    type="checkbox"
                    checked={voiceEnabled}
                    onChange={(e) => setVoiceEnabled(e.target.checked)}
                  />
                  <span className="voice-toggle-slider" />
                </label>
              </div>

              <div className="voice-speed-row">
                <span className="voice-speed-label">语速</span>
                <div className="voice-speed-group">
                  {SPEED_OPTIONS.map((s) => (
                    <button
                      key={s}
                      type="button"
                      className={`voice-speed-pill${voiceSpeed === s ? ' active' : ''}`}
                      onClick={() => setVoiceSpeed(s)}
                    >
                      {s}x
                    </button>
                  ))}
                </div>
              </div>

              <div className="voice-action-row">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => fileInputRef.current?.click()}
                >
                  更换音频
                </button>
                <button
                  type="button"
                  className="btn-secondary voice-delete-btn"
                  onClick={handleDeleteRef}
                >
                  删除音频
                </button>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".wav,.mp3"
                  className="voice-file-input"
                  onChange={(e) => {
                    const f = e.target.files?.[0]
                    if (f) { setAudioFile(f); handleUploadRef() }
                  }}
                  style={{ display: 'none' }}
                />
              </div>

              {refMsg && (
                <p className={`voice-msg ${refMsg.type === 'error' ? 'voice-msg-error' : 'voice-msg-success'}`}>
                  {refMsg.text}
                </p>
              )}

              <div className="voice-asr-status">
                <span className="voice-asr-icon">{voiceStatus.funasr ? '🎤' : '🎤'}</span>
                <span>语音输入</span>
                <span className={`voice-asr-badge ${voiceStatus.funasr ? 'available' : 'unavailable'}`}>
                  {voiceStatus.funasr ? '✅ 可用' : '❌ 不可用'}
                </span>
              </div>
            </div>
          )}
        </section>
      ) : (
        /* State 1: GPT-SoVITS not detected — only show footer link */
        <p className="voice-setup-link">
          🔗{' '}
          <a href={VOICE_SETUP_LINK} target="_blank" rel="noopener noreferrer" className="settings-link">
            如何启用语音功能 →
          </a>
        </p>
      )}

      <section className="settings-section settings-about">
        <h2 className="settings-section-title">关于</h2>
        <p className="settings-about-line">
          <span className="settings-label">版本</span>
          <span>CharSim v{APP_VERSION}</span>
        </p>
        <p className="settings-about-line">
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="settings-link"
          >
            GitHub →
          </a>
        </p>
      </section>
    </div>
  )
}
