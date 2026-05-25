import { useEffect, useState } from 'react'
import { fetchWithTimeout, getMyUsage, updateApiConfig } from '../api/client'
import useAppStore from '../store/useAppStore'
import Loading from './common/Loading'
import ErrorBox from './common/ErrorBox'

const APP_VERSION = '1.0.0'
const GITHUB_URL = 'https://github.com/VERSE-SHIYU/Character-distill'
export default function SettingsPanel() {
  const [config, setConfig] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [apiForm, setApiForm] = useState({ base_url: '', model: '', api_key: '' })
  const [showApiKey, setShowApiKey] = useState(false)
  const [saving, setSaving] = useState(false)
  const [hasApiKey, setHasApiKey] = useState(false)
  const [summaryThreshold, setSummaryThreshold] = useState(50)

  const affinityEnabled = useAppStore((s) => s.affinityEnabled)
  const setAffinityEnabled = useAppStore((s) => s.setAffinityEnabled)
  const setView = useAppStore((s) => s.setView)

  // ---- Load config ----
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true)
      setError(null)
      try {
        // Load system config (summary_threshold, etc.)
        const sysRes = await fetchWithTimeout('/api/settings/config')
        const sysData = await sysRes.json()
        if (!cancelled) {
          setConfig(sysData)
          setSummaryThreshold(sysData.summary_threshold ?? 50)
        }

        // Load user API config from /api/auth/me
        const meRes = await fetchWithTimeout('/api/auth/me')
        const meData = await meRes.json()
        if (!cancelled) {
          setHasApiKey(meData.has_api_key)
          setApiForm({
            base_url: meData.base_url || 'https://api.deepseek.com',
            model: meData.model || 'deepseek-v4-pro',
            api_key: '',
          })
          useAppStore.setState({ apiConfigured: meData.has_api_key })
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

  return (
    <div className="settings-panel panel">
      <header className="panel-header">
        <button type="button" className="chat-back-btn" onClick={() => setView('profile')} title="返回"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5m7-7-7 7 7 7"/></svg>返回</button>
        <h1 className="panel-title">设置</h1>
        <p className="panel-desc">API 配置与系统设置</p>
      </header>

      {error && <ErrorBox message={error} onDismiss={() => setError(null)} />}

      <section className="settings-section">
        <h2 className="settings-section-title">API 配置</h2>
        {!hasApiKey && (
          <div className="api-config-alert">
            {'⚠️'} 请先配置 API 密钥才能使用蒸馏和对话功能
          </div>
        )}
        <p className="settings-hint">
          每个用户独立配置，互不影响。修改后点击保存即可生效。
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
                value={apiForm.base_url}
                onChange={(e) => setApiForm((f) => ({ ...f, base_url: e.target.value }))}
              />
            </label>
            <label className="settings-field">
              <span className="settings-label">model</span>
              <input
                type="text"
                className="settings-input"
                value={apiForm.model}
                onChange={(e) => setApiForm((f) => ({ ...f, model: e.target.value }))}
              />
            </label>
            <p className="settings-hint">
              模型能力直接影响角色扮演质量。推荐使用 deepseek-v4-pro 或 claude-sonnet-4-20250514。
            </p>
            <label className="settings-field">
              <span className="settings-label">api_key</span>
              <div className="settings-api-key-row">
                <input
                  type={showApiKey ? 'text' : 'password'}
                  className="settings-input"
                  placeholder="不修改请留空"
                  value={apiForm.api_key}
                  onChange={(e) => setApiForm((f) => ({ ...f, api_key: e.target.value }))}
                />
                <button
                  type="button"
                  className="btn-ghost settings-show-key-btn"
                  onClick={() => setShowApiKey((v) => !v)}
                >
                  {showApiKey ? '隐藏' : '显示'}
                </button>
              </div>
            </label>
            <button
              type="button"
              className="btn-primary"
              disabled={saving}
              onClick={async () => {
                setSaving(true)
                setError(null)
                try {
                  await updateApiConfig({
                    base_url: apiForm.base_url,
                    model: apiForm.model,
                    api_key: apiForm.api_key,
                  })
                  setApiForm((f) => ({ ...f, api_key: '' }))
                  const hasKey = apiForm.api_key || hasApiKey
                  setHasApiKey(hasKey)
                  useAppStore.setState({ apiConfigured: hasKey })
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
        <h2 className="settings-section-title">对话摘要</h2>
        <p className="settings-hint">
          对话超过指定轮数后自动压缩历史为摘要，节省上下文
        </p>
        <div className="settings-fields">
          <label className="settings-field">
            <span className="settings-label">触发阈值（消息条数）</span>
            <input
              type="number"
              className="settings-input"
              min={10}
              max={200}
              value={summaryThreshold}
              onChange={(e) => setSummaryThreshold(Number(e.target.value))}
            />
          </label>
        </div>
      </section>

      <section className="settings-section">
        <h2 className="settings-section-title">情感系统</h2>
        <p className="settings-hint">
          关闭后将不消耗 token 进行情感计算，但情感状态不会实时更新。
        </p>
        <div className="settings-fields">
          <label className="settings-field" style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
            <span className="settings-label">开启情感系统</span>
            <label className="voice-toggle">
              <input
                type="checkbox"
                checked={affinityEnabled}
                onChange={(e) => setAffinityEnabled(e.target.checked)}
              />
              <span className="voice-toggle-slider" />
            </label>
          </label>
        </div>
      </section>

      <section className="settings-section">
        <h2 className="settings-section-title">我的用量</h2>
        <UsageCard />
      </section>

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

function UsageCard() {
  const [usage, setUsage] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true)
      try {
        const res = await getMyUsage()
        const data = await res.json()
        if (!cancelled) setUsage(data)
      } catch {
        if (!cancelled) setUsage(null)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [])

  if (loading) return <Loading text="加载用量…" />
  if (!usage) return <p className="settings-hint">暂无用量数据</p>

  const fmt = (n) => {
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M'
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K'
    return String(n)
  }

  return (
    <div>
      <div className="usage-stats-grid">
        <div className="usage-stat-item">
          <span className="usage-stat-value">{usage.total_calls}</span>
          <span className="usage-stat-label">调用次数</span>
        </div>
        <div className="usage-stat-item">
          <span className="usage-stat-value">{fmt(usage.total_prompt_tokens)}</span>
          <span className="usage-stat-label">输入 Token</span>
        </div>
        <div className="usage-stat-item">
          <span className="usage-stat-value">{fmt(usage.total_completion_tokens)}</span>
          <span className="usage-stat-label">输出 Token</span>
        </div>
      </div>
      {usage.by_action && Object.keys(usage.by_action).length > 0 && (
        <div className="usage-action-list">
          {Object.entries(usage.by_action).map(([action, stats]) => (
            <div key={action} className="usage-action-row">
              <span className="usage-action-name">{action}</span>
              <span className="usage-action-count">{stats.calls} 次</span>
              <span className="usage-action-tokens">
                {fmt(stats.prompt_tokens)} + {fmt(stats.completion_tokens)}
              </span>
            </div>
          ))}
        </div>
      )}
      {usage.by_model && Object.keys(usage.by_model).length > 0 && (
        <div className="usage-action-list">
          <p className="settings-hint" style={{ marginBottom: 8 }}>按模型</p>
          {Object.entries(usage.by_model).map(([model, stats]) => (
            <div key={model} className="usage-action-row">
              <span className="usage-action-name">{model}</span>
              <span className="usage-action-count">{stats.calls} 次</span>
              <span className="usage-action-tokens">
                {fmt(stats.prompt_tokens)} + {fmt(stats.completion_tokens)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

