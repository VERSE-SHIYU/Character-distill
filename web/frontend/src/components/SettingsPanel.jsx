import { useEffect, useState } from 'react'
import { fetchWithTimeout, getMyUsage, updateApiConfig } from '../api/client'
import useAppStore from '../store/useAppStore'
import Loading from './common/Loading'
import ErrorBox from './common/ErrorBox'
import { AlertTriangle } from './common/Icon'

const APP_VERSION = '1.0.0'
const GITHUB_URL = 'https://github.com/VERSE-SHIYU/Character-distill'
const MASKED_KEY = '••••••••'
export default function SettingsPanel() {
  const [config, setConfig] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [apiForm, setApiForm] = useState({ base_url: '', model: '', api_key: '' })
  const [provider, setProvider] = useState('deepseek')
  const [showApiKey, setShowApiKey] = useState(false)
  const [saving, setSaving] = useState(false)
  const [hasApiKey, setHasApiKey] = useState(false)
  const [hasEmbeddingKey, setHasEmbeddingKey] = useState(false)
  const [embeddingRegion, setEmbeddingRegion] = useState('cn')
  const [embeddingKey, setEmbeddingKey] = useState('')
  const [showEmbeddingKey, setShowEmbeddingKey] = useState(false)
  const [savingEmbedding, setSavingEmbedding] = useState(false)
  const [testingEmbedding, setTestingEmbedding] = useState(false)
  const [testResult, setTestResult] = useState(null)
  const [testMsg, setTestMsg] = useState('')
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
          const savedBaseUrl = meData.base_url || ''
          const savedModel = meData.model || ''
          setProvider(savedBaseUrl.includes('deepseek.com') ? 'deepseek' : 'custom')
          setApiForm({
            base_url: savedBaseUrl || 'https://api.deepseek.com',
            model: savedModel || 'deepseek-v4-pro',
            api_key: meData.has_api_key ? MASKED_KEY : '',
          })
          setHasEmbeddingKey(meData.has_embedding_key || false)
          setEmbeddingRegion(meData.embedding_region || 'cn')
          setEmbeddingKey(meData.has_embedding_key ? MASKED_KEY : '')
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
            <AlertTriangle size={16} style={{ verticalAlign: 'middle', marginRight: 6 }} /> 请先配置 API 密钥才能使用蒸馏和对话功能
          </div>
        )}
        <p className="settings-hint">
          每个用户独立配置，互不影响。修改后点击保存即可生效。
        </p>
        {loading ? (
          <Loading text="加载配置…" />
        ) : (
          <div className="settings-fields">
            {/* Provider selector */}
            <div className="provider-selector">
              <button
                type="button"
                className={`provider-card${provider === 'deepseek' ? ' active' : ''}`}
                onClick={() => {
                  setProvider('deepseek')
                  setApiForm((f) => ({ ...f, base_url: 'https://api.deepseek.com', model: 'deepseek-v4-pro' }))
                }}
              >
                <div className="provider-card-title">DeepSeek <span className="provider-card-badge">推荐</span></div>
                <div className="provider-card-desc">自动填充，只需填写 API Key</div>
              </button>
              <button
                type="button"
                className={`provider-card${provider === 'custom' ? ' active' : ''}`}
                onClick={() => setProvider('custom')}
              >
                <div className="provider-card-title">其他模型</div>
                <div className="provider-card-desc">手动配置 base_url、model 和 API Key</div>
              </button>
            </div>

            {/* DeepSeek mode: only api_key */}
            {provider === 'deepseek' && (
              <>
                <label className="settings-field">
                  <span className="settings-label">api_key</span>
                  <div className="settings-api-key-row">
                    <input
                      type={showApiKey ? 'text' : 'password'}
                      className="settings-input"
                      placeholder={hasApiKey ? '已配置，留空不修改' : '输入 DeepSeek API Key'}
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
                <p className="settings-hint">
                  🔗 <a href="https://platform.deepseek.com/api_keys" target="_blank" rel="noopener noreferrer" className="settings-link">前往 DeepSeek 官网获取 API Key</a>
                </p>
              </>
            )}

            {/* Custom mode: all three fields */}
            {provider === 'custom' && (
              <>
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
                      placeholder={hasApiKey ? '已配置，留空不修改' : '输入 API Key'}
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
              </>
            )}

            <button
              type="button"
              className="btn-primary"
              disabled={saving}
              onClick={async () => {
                setSaving(true)
                setError(null)
                try {
                  const sentKey = apiForm.api_key === MASKED_KEY ? '' : apiForm.api_key
                  await updateApiConfig({
                    base_url: apiForm.base_url,
                    model: apiForm.model,
                    api_key: sentKey,
                  })
                  const hasKey = Boolean(sentKey || hasApiKey)
                  setApiForm((f) => ({ ...f, api_key: hasKey ? MASKED_KEY : '' }))
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
        <h2 className="settings-section-title">向量检索（RAG）配置</h2>
        <p className="settings-hint">
          用于角色记忆的语义检索，每个用户独立配置，费用由用户自己承担。
        </p>

        <div className="provider-selector">
          <button
            type="button"
            className={`provider-card${embeddingRegion === 'cn' ? ' active' : ''}`}
            onClick={() => setEmbeddingRegion('cn')}
          >
            <div className="provider-card-title">中国内地 <span className="provider-card-badge">有免费额度</span></div>
            <div className="provider-card-desc">dashscope.aliyuncs.com</div>
          </button>
          <button
            type="button"
            className={`provider-card${embeddingRegion === 'intl' ? ' active' : ''}`}
            onClick={() => setEmbeddingRegion('intl')}
          >
            <div className="provider-card-title">国际（新加坡）</div>
            <div className="provider-card-desc">dashscope-intl.aliyuncs.com · 按量计费</div>
          </button>
        </div>

        <label className="settings-field">
          <span className="settings-label">阿里云百炼 API Key</span>
          <div className="settings-api-key-row">
            <input
              type={showEmbeddingKey ? 'text' : 'password'}
              className="settings-input"
              placeholder={hasEmbeddingKey ? '已配置，留空不修改' : '输入百炼 API Key（sk-xxx）'}
              value={embeddingKey}
              onChange={(e) => setEmbeddingKey(e.target.value)}
            />
            <button
              type="button"
              className="btn-ghost settings-show-key-btn"
              onClick={() => setShowEmbeddingKey(v => !v)}
            >
              {showEmbeddingKey ? '隐藏' : '显示'}
            </button>
          </div>
        </label>

        <p className="settings-hint">
          🔗 <a href="https://bailian.aliyun.com" target="_blank" rel="noopener noreferrer" className="settings-link">
            前往阿里云百炼注册并获取 API Key
          </a>
          {embeddingRegion === 'cn' && '（中国内地用户注册后有免费额度）'}
        </p>

        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
          <button
            type="button"
            className="btn-primary"
            disabled={savingEmbedding}
            onClick={async () => {
              setSavingEmbedding(true)
              setError(null)
              try {
                const sentKey = embeddingKey === MASKED_KEY ? '' : embeddingKey
                await updateApiConfig({
                  embedding_key: sentKey,
                  embedding_region: embeddingRegion,
                })
                const hasKey = Boolean(sentKey || hasEmbeddingKey)
                setEmbeddingKey(hasKey ? MASKED_KEY : '')
                setHasEmbeddingKey(hasKey)
              } catch (err) {
                setError(err.message)
              } finally {
                setSavingEmbedding(false)
              }
            }}
          >
            {savingEmbedding ? '保存中…' : '保存 RAG 配置'}
          </button>

          <button
            type="button"
            className="btn-ghost"
            disabled={testingEmbedding}
            onClick={async () => {
              setTestingEmbedding(true)
              setTestResult(null)
              setTestMsg('')
              try {
                const sentKey = embeddingKey === MASKED_KEY ? '' : embeddingKey
                const res = await fetchWithTimeout('/api/auth/test-embedding', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ embedding_key: sentKey, embedding_region: embeddingRegion }),
                })
                const data = await res.json()
                if (data.ok) {
                  setTestResult('ok')
                  setTestMsg('✓ 连接正常')
                } else {
                  setTestResult('error')
                  setTestMsg('✗ ' + (data.error || '未知错误'))
                }
              } catch (err) {
                setTestResult('error')
                setTestMsg('✗ ' + (err.message || '网络错误'))
              } finally {
                setTestingEmbedding(false)
                setTimeout(() => { setTestResult(null); setTestMsg('') }, 5000)
              }
            }}
          >
            {testingEmbedding ? '测试中…' : '测试连接'}
          </button>
        </div>

        {testResult && (
          <p className="settings-hint" style={{
            color: testResult === 'ok' ? '#22c55e' : '#ef4444',
            fontWeight: 600,
            marginTop: 8,
          }}>
            {testMsg}
          </p>
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

