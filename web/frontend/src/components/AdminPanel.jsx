import { useState, useEffect, useCallback } from 'react'
import { adminAPI } from '../api/client'
import useAppStore from '../store/useAppStore'

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
  const [actionError, setActionError] = useState('')
  const [resetTarget, setResetTarget] = useState(null)
  const [newPassword, setNewPassword] = useState('')
  const [resetting, setResetting] = useState(false)
  const [resetError, setResetError] = useState('')
  const [resetOk, setResetOk] = useState('')
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  const [confirmName, setConfirmName] = useState('')
  const [emailTarget, setEmailTarget] = useState(null)
  const [newEmail, setNewEmail] = useState('')
  const [emailError, setEmailError] = useState('')
  const [emailOk, setEmailOk] = useState('')
  const [emailSetting, setEmailSetting] = useState(false)
  const authUser = useAppStore((s) => s.authUser)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
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
    if (!user.is_disabled) {
      if (!window.confirm('确定禁用该用户？')) return
    }
    try {
      if (user.is_disabled) {
        await adminAPI.enableUser(user.id)
      } else {
        await adminAPI.disableUser(user.id)
      }
      await load()
    } catch (err) {
      setActionError(err.message)
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

  const handleSetEmail = async () => {
    setEmailSetting(true)
    setEmailError('')
    setEmailOk('')
    try {
      await adminAPI.setUserEmail(emailTarget.id, newEmail)
      setEmailOk('邮箱已设置')
      setTimeout(() => {
        setEmailTarget(null)
        setNewEmail('')
        setEmailOk('')
      }, 1500)
      await load()
    } catch (err) {
      setEmailError(err.message || '设置失败')
    } finally {
      setEmailSetting(false)
    }
  }

  const handleClearEmail = async (user) => {
    if (!window.confirm('确定清除该用户的邮箱？')) return
    try {
      await adminAPI.clearUserEmail(user.id)
      await load()
    } catch (err) {
      setActionError(err.message || '清除失败')
    }
  }

  const handleDelete = async () => {
    setDeleting(true)
    setDeleteError('')
    try {
      await adminAPI.deleteUser(deleteTarget.id)
      setDeleteTarget(null)
      setConfirmName('')
      await load()
    } catch (err) {
      setDeleteError(err.message || '删除失败')
    } finally {
      setDeleting(false)
    }
  }

  const fmtDate = (iso) => {
    if (!iso) return '-'
    return iso.slice(0, 10)
  }

  return (
    <div className="admin-card">
      <div className="admin-card-title">用户管理</div>
      {error && (
        <div className="admin-error-banner">
          <span>{error}</span>
          <button className="admin-error-close" onClick={() => setError('')}>✕</button>
        </div>
      )}
      {actionError && (
        <div className="admin-error-banner">
          <span>{actionError}</span>
          <button className="admin-error-close" onClick={() => setActionError('')}>✕</button>
        </div>
      )}
      {loading ? (
        <div className="admin-loading">加载中…</div>
      ) : (
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th style={{ minWidth: 120 }}>用户名</th>
                <th style={{ minWidth: 150 }}>邮箱</th>
                <th style={{ minWidth: 70 }}>角色</th>
                <th style={{ minWidth: 70 }}>状态</th>
                <th style={{ minWidth: 100 }}>注册时间</th>
                <th style={{ minWidth: 200 }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>{u.username}</td>
                  <td>{u.email || <span style={{ color: 'var(--text-secondary)', fontSize: 13 }}>未绑定</span>}</td>
                  <td>{u.is_admin ? '管理员' : '用户'}</td>
                  <td>
                    <span className={`admin-status${u.is_disabled ? ' disabled' : ''}`}>
                      {u.is_disabled ? '已禁用' : '正常'}
                    </span>
                  </td>
                  <td>{fmtDate(u.created_at)}</td>
                  <td className="admin-actions-cell">
                    <button
                      className={`admin-action-btn${u.is_disabled ? ' enable' : ' disable'}`}
                      onClick={() => toggleDisable(u)}
                    >
                      {u.is_disabled ? '启用' : '禁用'}
                    </button>
                    <button
                      className="admin-action-btn"
                      onClick={() => { setEmailTarget(u); setNewEmail(u.email || ''); setEmailError(''); setEmailOk('') }}
                    >
                      设置邮箱
                    </button>
                    {u.email && (
                      <button
                        className="admin-action-btn delete-user"
                        onClick={() => handleClearEmail(u)}
                        title="清除邮箱"
                      >
                        清除邮箱
                      </button>
                    )}
                    <button
                      className="admin-action-btn reset"
                      onClick={() => { setResetTarget(u); setNewPassword(''); setResetError(''); setResetOk('') }}
                    >
                      重置密码
                    </button>
                    {u.id !== authUser?.id && (
                      <button
                        className="admin-action-btn delete-user"
                        onClick={() => { setDeleteTarget(u); setConfirmName(''); setDeleteError('') }}
                        title="删除用户"
                      >
                        ✕
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Set email modal */}
      {emailTarget && (
        <div className="modal-overlay" onClick={() => setEmailTarget(null)}>
          <div className="modal-card" style={{ maxWidth: 380 }} onClick={(e) => e.stopPropagation()}>
            <h3 className="modal-title">设置邮箱 — {emailTarget.username}</h3>
            <div className="modal-body">
              <label className="login-field" style={{ margin: 0 }}>
                <span>邮箱地址</span>
                <input
                  type="email"
                  value={newEmail}
                  onChange={(e) => setNewEmail(e.target.value)}
                  placeholder="user@example.com"
                  autoFocus
                  onKeyDown={(e) => e.key === 'Enter' && handleSetEmail()}
                />
              </label>
              {emailError && <div className="login-error" style={{ marginTop: 8 }}>{emailError}</div>}
              {emailOk && <div style={{ color: '#22c55e', marginTop: 8, fontSize: 13 }}>{emailOk}</div>}
            </div>
            <div className="modal-actions">
              <button className="btn-ghost" onClick={() => setEmailTarget(null)}>取消</button>
              <button className="btn-primary" onClick={handleSetEmail} disabled={emailSetting}>
                {emailSetting ? '保存中…' : '保存'}
              </button>
            </div>
          </div>
        </div>
      )}

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

      {/* Delete user confirmation modal */}
      {deleteTarget && (
        <div className="modal-overlay" onClick={() => { if (!deleting) { setDeleteTarget(null); setConfirmName('') } }}>
          <div className="modal-card" style={{ maxWidth: 420 }} onClick={(e) => e.stopPropagation()}>
            <h3 className="modal-title">删除用户 — {deleteTarget.username}</h3>
            <div className="modal-body">
              <p style={{ fontSize: 14, color: 'var(--text-secondary)', margin: '0 0 12px', lineHeight: 1.6 }}>
                删除用户将清除其所有数据（文本、角色卡、对话、记忆），不可恢复。
              </p>
              <p style={{ fontSize: 13, margin: '0 0 8px' }}>
                请输入用户名 <strong>{deleteTarget.username}</strong> 确认删除：
              </p>
              <input
                className="login-input"
                value={confirmName}
                onChange={(e) => setConfirmName(e.target.value)}
                placeholder={deleteTarget.username}
                autoFocus
                onKeyDown={(e) => e.key === 'Enter' && confirmName === deleteTarget.username && !deleting && handleDelete()}
              />
              {deleteError && <div className="login-error" style={{ marginTop: 8 }}>{deleteError}</div>}
            </div>
            <div className="modal-actions">
              <button className="btn-ghost" onClick={() => { setDeleteTarget(null); setConfirmName('') }} disabled={deleting}>取消</button>
              <button className="btn-danger" onClick={handleDelete} disabled={deleting || confirmName !== deleteTarget.username}>
                {deleting ? '删除中…' : '确认删除'}
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
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [deleting, setDeleting] = useState(false)
  const [batchDeleting, setBatchDeleting] = useState(false)

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

  const handleDelete = async () => {
    setDeleting(true)
    try {
      await adminAPI.deleteInvite(deleteTarget)
      setDeleteTarget(null)
      await load()
    } catch (err) {
      setError(err.message)
    } finally {
      setDeleting(false)
    }
  }

  const handleBatchDelete = async () => {
    if (!window.confirm('确定批量删除所有已使用的邀请码？')) return
    setBatchDeleting(true)
    try {
      await adminAPI.deleteUsedInvites()
      await load()
    } catch (err) {
      setError(err.message)
    } finally {
      setBatchDeleting(false)
    }
  }

  const usedCount = codes.filter((c) => c.used_by).length

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
                <td className="admin-actions-cell">
                  {!c.used_by && (
                    <button
                      className="admin-action-btn copy"
                      onClick={() => copyCode(c.code)}
                    >
                      {copied === c.code ? '已复制' : '复制'}
                    </button>
                  )}
                  <button
                    className="admin-action-btn delete-invite"
                    onClick={() => setDeleteTarget(c.code)}
                    title="删除邀请码"
                  >
                    ✕
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {usedCount > 0 && (
        <div className="invite-batch-row">
          <span className="invite-batch-hint">{usedCount} 个已使用的邀请码</span>
          <button
            className="admin-action-btn delete-invite"
            onClick={handleBatchDelete}
            disabled={batchDeleting}
          >
            {batchDeleting ? '删除中…' : '批量删除已使用的邀请码'}
          </button>
        </div>
      )}

      {/* Delete confirmation modal */}
      {deleteTarget && (
        <div className="modal-overlay" onClick={() => setDeleteTarget(null)}>
          <div className="modal-card" style={{ maxWidth: 380 }} onClick={(e) => e.stopPropagation()}>
            <h3 className="modal-title">确定删除此邀请码？</h3>
            <div className="modal-body">
              <p style={{ fontSize: 14, color: 'var(--text-secondary)', wordBreak: 'break-all', margin: 0 }}>
                <code style={{ fontSize: 13, background: 'rgba(0,0,0,0.04)', padding: '3px 8px', borderRadius: 4 }}>
                  {deleteTarget}
                </code>
              </p>
            </div>
            <div className="modal-actions">
              <button className="btn-ghost" onClick={() => setDeleteTarget(null)}>取消</button>
              <button className="btn-primary" onClick={handleDelete} disabled={deleting}>
                {deleting ? '删除中…' : '确认删除'}
              </button>
            </div>
          </div>
        </div>
      )}
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

  return (
    <div className="admin-card">
      <div className="admin-card-title">用户用量统计</div>
      {error && (
        <div className="admin-error-banner">
          <span>{error}</span>
          <button className="admin-error-close" onClick={() => setError('')}>✕</button>
        </div>
      )}
      {loading ? (
        <div className="admin-loading">加载中…</div>
      ) : (
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
      )}
    </div>
  )
}
