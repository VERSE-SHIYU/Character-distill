import { useEffect, useState } from 'react'
import { fetchWithTimeout } from '../api/client'
import { applyTheme, getTheme } from '../utils/theme'
import Loading from './common/Loading'
import ErrorBox from './common/ErrorBox'

const APP_VERSION = '0.0.0'
const GITHUB_URL = 'https://github.com'

export default function SettingsPanel() {
  const [config, setConfig] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [theme, setTheme] = useState(getTheme)
  const [voice, setVoice] = useState(() => localStorage.getItem('tts_voice') || 'xiaoxiao')
  const [testing, setTesting] = useState(false)

  const setVoiceAndSave = (v) => {
    setVoice(v)
    localStorage.setItem('tts_voice', v)
  }

  const testVoice = async () => {
    setTesting(true)
    try {
      const res = await fetch('/api/tts/synthesize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: '你好，这是音色测试。', voice }),
      })
      if (!res.ok) throw new Error(`TTS ${res.status}`)
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      audio.onended = () => { URL.revokeObjectURL(url); setTesting(false) }
      audio.onerror = () => { URL.revokeObjectURL(url); setTesting(false) }
      await audio.play()
    } catch {
      setTesting(false)
    }
  }

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true)
      setError(null)
      try {
        const res = await fetchWithTimeout('/api/settings/config')
        const data = await res.json()
        if (!cancelled) setConfig(data)
      } catch (err) {
        console.error('[SettingsPanel] load config failed:', err)
        if (!cancelled) setError(err.message || '\u52a0\u8f7d\u914d\u7f6e\u5931\u8d25')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [])

  const setThemeMode = (mode) => {
    applyTheme(mode)
    setTheme(mode)
  }

  return (
    <div className="settings-panel panel">
      <header className="panel-header">
        <h1 className="panel-title">{'\u8bbe\u7f6e'}</h1>
        <p className="panel-desc">{'API \u914d\u7f6e\u4e0e\u754c\u9762\u4e3b\u9898'}</p>
      </header>

      {error && <ErrorBox message={error} onDismiss={() => setError(null)} />}

      <section className="settings-section">
        <h2 className="settings-section-title">{'API \u914d\u7f6e'}</h2>
        <p className="settings-hint">
          {'\u4ee5\u4e0b\u4fe1\u606f\u6765\u81ea\u9879\u76ee\u6839\u76ee\u5f55 config.yaml\uff0c\u4ec5\u8bfb\u3002\u4fee\u6539\u540e\u9700\u91cd\u542f\u670d\u52a1\u3002'}
        </p>
        {loading ? (
          <Loading text={'\u52a0\u8f7d\u914d\u7f6e\u2026'} />
        ) : (
          <div className="settings-fields">
            <label className="settings-field">
              <span className="settings-label">base_url</span>
              <input
                type="text"
                className="settings-input"
                readOnly
                value={config?.base_url || '\u2014'}
              />
            </label>
            <label className="settings-field">
              <span className="settings-label">model</span>
              <input
                type="text"
                className="settings-input"
                readOnly
                value={config?.model || '\u2014'}
              />
            </label>
          </div>
        )}
      </section>

      <section className="settings-section">
        <h2 className="settings-section-title">{'\u4e3b\u9898'}</h2>
        <div className="settings-theme-row">
          <button
            type="button"
            className={`settings-theme-btn${theme === 'light' ? ' active' : ''}`}
            onClick={() => setThemeMode('light')}
          >
            {'\u2600\ufe0f \u6d45\u8272'}
          </button>
          <button
            type="button"
            className={`settings-theme-btn${theme === 'dark' ? ' active' : ''}`}
            onClick={() => setThemeMode('dark')}
          >
            {'\u{1F319} \u6df1\u8272'}
          </button>
        </div>
      </section>

      <section className="settings-section">
        <h2 className="settings-section-title">{'\u{1F50A} 语音设置'}</h2>
        <label className="settings-field">
          <span className="settings-label">{'默认音色'}</span>
          <select className="settings-input" value={voice} onChange={e => setVoiceAndSave(e.target.value)}>
            <option value="xiaoxiao">{'晓晓（女，活泼）'}</option>
            <option value="yunxi">{'云希（男，青年）'}</option>
            <option value="xiaoyi">{'晓伊（女，温柔）'}</option>
            <option value="yunyang">{'云扬（男，新闻播报）'}</option>
          </select>
        </label>
        <button type="button" className="btn-primary settings-test-btn" onClick={testVoice} disabled={testing}>
          {testing ? '播放中…' : '▶ 试听'}
        </button>
      </section>

      <section className="settings-section settings-about">
        <h2 className="settings-section-title">{'\u5173\u4e8e'}</h2>
        <p className="settings-about-line">
          <span className="settings-label">{'\u7248\u672c'}</span>
          <span>CharSim v{APP_VERSION}</span>
        </p>
        <p className="settings-about-line">
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="settings-link"
          >
            {'GitHub \u2192'}
          </a>
        </p>
      </section>
    </div>
  )
}
