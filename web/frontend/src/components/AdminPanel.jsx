import { useState, useEffect, useCallback } from 'react'
import { adminAPI } from '../api/client'

const TABS = [
  { id: 'users', label: '用户管理' },
  { id: 'invites', label: '邀请码' },
  { id: 'usage', label: '用户用量' },
]

export default function AdminPanel() {
  const [tab, setTab] = useState('users')

  return (
    <div className="admin-panel panel">
      <header className="panel-header">
        <h2 className="panel-title">管理后台</h2>
        <p className="panel-desc">用户管理 · 邀请码 · 用量统计</p>
      </header>
      <div className="admin-body">
        <div className="admin-tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={`admin-tab${tab === t.id ? ' active' : ''}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="admin-content">
          {tab === 'users' ? <UsersTab /> : tab === 'invites' ? <InvitesTab /> : <UsageTab />}
        </div>
      </div>
    </div>
  )
}

function UsersTab() {
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [resetTarget, setResetTarget] = useState(null)
  const [newPassword, setNewPassword] = useState('')
  const [resetting, setResetting] = useState(false)
  const [resetError, setResetError] = useState('')
  const [resetOk, setResetOk] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await adminAPI.listUsers()
      setUsers(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const toggleDisable = async (user) => {
    try {
      if (user.is_disabled) {
        await adminAPI.enableUser(user.id)
      } else {
        await adminAPI.disableUser(user.id)
      }
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  const handleReset = async () => {
    if (newPassword.length < 6) {
      setResetError('新密码至少 6 个字符')
      return
    }
    setResetting(true)
    setResetError('')
    setResetOk('')
    try {
      await adminAPI.resetPassword(resetTarget.id, newPassword)
      setResetOk('密码已重置')
      setTimeout(() => {
        setResetTarget(null)
        setNewPassword('')
        setResetOk('')
      }, 1500)
    } catch (err) {
      setResetError(err.message || '重置失败')
    } finally {
      setResetting(false)
    }
  }

  if (loading) return <div className="admin-loading">加载中…</div>
  if (error) return <div className="admin-error">{error}</div>

  return (
    <div className="admin-card">
      <div className="admin-card-title">用户管理</div>
      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead>
            <tr>
              <th>用户名</th>
              <th>角色</th>
              <th>状态</th>
              <th>注册时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>{u.username}</td>
                <td>{u.is_admin ? '管理员' : '用户'}</td>
                <td>
                  <span className={`admin-status${u.is_disabled ? ' disabled' : ''}`}>
                    {u.is_disabled ? '已禁用' : '正常'}
                  </span>
                </td>
                <td>{u.created_at || '-'}</td>
                <td className="admin-actions-cell">
                  <button
                    className={`admin-action-btn${u.is_disabled ? ' enable' : ' disable'}`}
                    onClick={() => toggleDisable(u)}
                  >
                    {u.is_disabled ? '启用' : '禁用'}
                  </button>
                  <button
                    className="admin-action-btn reset"
                    onClick={() => { setResetTarget(u); setNewPassword(''); setResetError(''); setResetOk('') }}
                  >
                    重置密码
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Reset password modal */}
      {resetTarget && (
        <div className="modal-overlay" onClick={() => setResetTarget(null)}>
          <div className="modal-card" style={{ maxWidth: 380 }} onClick={(e) => e.stopPropagation()}>
            <h3 className="modal-title">重置密码 — {resetTarget.username}</h3>
            <div className="modal-body">
              <label className="login-field" style={{ margin: 0 }}>
                <span>新密码</span>
                <input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  placeholder="至少 6 个字符"
                  autoFocus
                  onKeyDown={(e) => e.key === 'Enter' && handleReset()}
                />
              </label>
              {resetError && <div className="login-error" style={{ marginTop: 8 }}>{resetError}</div>}
              {resetOk && <div style={{ color: '#22c55e', marginTop: 8, fontSize: 13 }}>{resetOk}</div>}
            </div>
            <div className="modal-actions">
              <button className="btn-ghost" onClick={() => setResetTarget(null)}>取消</button>
              <button className="btn-primary" onClick={handleReset} disabled={resetting}>
                {resetting ? '重置中…' : '确认重置'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function InvitesTab() {
  const [codes, setCodes] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [count, setCount] = useState(1)
  const [generating, setGenerating] = useState(false)
  const [copied, setCopied] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await adminAPI.listInvites()
      setCodes(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const generate = async () => {
    setGenerating(true)
    try {
      await adminAPI.generateInvites(count)
      await load()
    } catch (err) {
      setError(err.message)
    } finally {
      setGenerating(false)
    }
  }

  const copyCode = (code) => {
    navigator.clipboard.writeText(code).then(() => {
      setCopied(code)
      setTimeout(() => setCopied(''), 2000)
    }).catch(() => {})
  }

  if (loading) return <div className="admin-loading">加载中…</div>

  return (
    <div className="admin-card">
      <div className="admin-card-title">邀请码管理</div>
      {error && <div className="admin-error-banner">{error}</div>}

      <div className="invite-generate-row">
        <input
          type="number"
          min={1}
          max={100}
          value={count}
          onChange={(e) => setCount(Math.max(1, Math.min(100, parseInt(e.target.value) || 1)))}
          className="invite-count-input"
        />
        <button className="invite-generate-btn" onClick={generate} disabled={generating}>
          {generating ? '生成中…' : '生成邀请码'}
        </button>
      </div>

      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead>
            <tr>
              <th>邀请码</th>
              <th>状态</th>
              <th>使用者</th>
              <th>使用时间</th>
              <th>创建时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {codes.map((c) => (
              <tr key={c.id}>
                <td className="invite-code-cell">
                  <code>{c.code}</code>
                </td>
                <td>
                  <span className={`admin-status${c.used_by ? ' disabled' : ''}`}>
                    {c.used_by ? '已使用' : '未使用'}
                  </span>
                </td>
                <td>{c.used_by || '-'}</td>
                <td>{c.used_at || '-'}</td>
                <td>{c.created_at || '-'}</td>
                <td>
                  {!c.used_by && (
                    <button
                      className="admin-action-btn copy"
                      onClick={() => copyCode(c.code)}
                    >
                      {copied === c.code ? '已复制' : '复制'}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function UsageTab() {
  const [data, setData] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const results = await adminAPI.getAllUsage()
      setData(results)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const fmt = (n) => {
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M'
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K'
    return String(n)
  }

  if (loading) return <div className="admin-loading">加载中…</div>
  if (error) return <div className="admin-error">{error}</div>

  return (
    <div className="admin-card">
      <div className="admin-card-title">用户用量统计</div>
      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead>
            <tr>
              <th>用户名</th>
              <th>调用次数</th>
              <th>输入 Token</th>
              <th>输出 Token</th>
              <th>最近活跃</th>
            </tr>
          </thead>
          <tbody>
            {data.map((r) => (
              <tr key={r.user_id}>
                <td>{r.username}</td>
                <td>{r.total_calls}</td>
                <td>{fmt(r.total_prompt_tokens)}</td>
                <td>{fmt(r.total_completion_tokens)}</td>
                <td>{r.last_active || '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
