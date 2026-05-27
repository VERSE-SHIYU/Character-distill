import { useState, useEffect, useCallback } from 'react'
import { adminAPI } from '../api/client'
import useAppStore from '../store/useAppStore'
import ConfirmModal from './common/ConfirmModal'
import { Trash2 } from './common/Icon'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'

const TABS = [
  { id: 'dashboard', label: '仪表盘' },
  { id: 'users', label: '用户管理' },
  { id: 'invites', label: '邀请码' },
  { id: 'usage', label: '用户用量' },
  { id: 'reports', label: '举报管理' },
  { id: 'audit', label: '内容审核' },
  { id: 'system', label: '系统日志' },
  { id: 'announcements', label: '公告' },
  { id: 'export', label: '数据导出' },
]

export default function AdminPanel() {
  const [tab, setTab] = useState('users')

  return (
    <div className="admin-panel panel">
      <header className="panel-header">
        <h2 className="panel-title">管理后台</h2>
        <p className="panel-desc">仪表盘 · 用户管理 · 邀请码 · 用量统计</p>
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
          {tab === 'dashboard' ? <DashboardTab /> : tab === 'users' ? <UsersTab /> : tab === 'invites' ? <InvitesTab /> : tab === 'usage' ? <UsageTab /> : tab === 'reports' ? <ReportsTab /> : tab === 'audit' ? <ContentAuditTab /> : tab === 'system' ? <SystemLogTab /> : tab === 'announcements' ? <AnnouncementsTab /> : <ExportTab />}
        </div>
      </div>
    </div>
  )
}

function fmtBytes(bytes) {
  if (bytes >= 1073741824) return (bytes / 1073741824).toFixed(1) + ' GB'
  if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + ' MB'
  if (bytes >= 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return bytes + ' B'
}

function isOnline(iso) {
  if (!iso) return false
  const then = new Date(iso)
  const now = new Date()
  return (now - then) < 20 * 60 * 1000
}

function fmtDateTime(iso) {
  if (!iso) return '-'
  return iso.slice(0, 16).replace('T', ' ')
}

function DashboardTab() {
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await adminAPI.getDashboard()
      setStats(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  if (loading) return <div className="admin-loading">加载中…</div>
  if (error) return <div className="admin-error-banner"><span>{error}</span></div>
  if (!stats) return null

  const cards = [
    { label: '今日活跃用户', value: stats.today_active_users },
    { label: '今日新注册', value: stats.today_new_users },
    { label: '总用户数', value: stats.total_users },
    { label: '今日 API 调用', value: stats.today_api_calls },
    { label: '今日 Token 消耗', value: stats.today_tokens?.toLocaleString() },
  ]

  const sys = stats.system
  const resources = sys ? [
    { label: '内存', used: sys.memory_used, total: sys.memory_total, percent: sys.memory_percent },
    { label: '磁盘', used: sys.disk_used, total: sys.disk_total, percent: sys.disk_percent },
  ] : []

  return (
    <div className="admin-card">
      <div className="admin-card-title">系统仪表盘</div>
      <div className="dashboard-stats-grid">
        {cards.map((c) => (
          <div key={c.label} className="dashboard-stat-card">
            <span className="dashboard-stat-value">{c.value}</span>
            <span className="dashboard-stat-label">{c.label}</span>
          </div>
        ))}
      </div>

      {resources.length > 0 && (
        <div className="dashboard-section">
          <div className="dashboard-section-title">系统资源</div>
          <div className="dashboard-resources">
            {resources.map((r) => (
              <div key={r.label} className="dashboard-resource-item">
                <div className="dashboard-resource-header">
                  <span>{r.label}</span>
                  <span>{fmtBytes(r.used)} / {fmtBytes(r.total)}</span>
                </div>
                <div className="dashboard-progress-bar">
                  <div
                    className={`dashboard-progress-fill${r.percent > 80 ? ' danger' : r.percent > 60 ? ' warn' : ''}`}
                    style={{ width: `${Math.min(r.percent, 100)}%` }}
                  />
                </div>
                <span className="dashboard-progress-label">{r.percent}%</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {stats.trend && stats.trend.length > 0 && (
        <div className="dashboard-section">
          <div className="dashboard-section-title">近 7 天趋势</div>
          <div className="dashboard-chart">
            <ResponsiveContainer width="100%" height={200}>
              <LineChart data={stats.trend}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--glass-border)" />
                <XAxis dataKey="day" tick={{ fontSize: 12 }} stroke="var(--text-secondary)" />
                <YAxis tick={{ fontSize: 12 }} stroke="var(--text-secondary)" />
                <Tooltip
                  contentStyle={{
                    background: 'var(--glass-bg)',
                    border: '1px solid var(--glass-border)',
                    borderRadius: 8,
                    fontSize: 13,
                  }}
                />
                <Line type="monotone" dataKey="calls" stroke="var(--primary)" strokeWidth={2} dot={{ r: 3 }} name="调用次数" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
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
  const [disableConfirm, setDisableConfirm] = useState(null)
  const [clearEmailConfirm, setClearEmailConfirm] = useState(null)
  const [detailTarget, setDetailTarget] = useState(null)
  const [detailData, setDetailData] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')
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
      setDisableConfirm(user)
      return
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
    setClearEmailConfirm(user)
    return
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
                <th style={{ minWidth: 70 }}>在线</th>
                <th style={{ minWidth: 140 }}>最后登录</th>
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
                  <td>
                    <span className={`online-badge${isOnline(u.last_login_at) ? ' online' : ''}`}>
                      {isOnline(u.last_login_at) ? '在线' : '离线'}
                    </span>
                  </td>
                  <td style={{ fontSize: 13, color: 'var(--text-secondary)' }}>{fmtDateTime(u.last_login_at)}</td>
                  <td>{fmtDate(u.created_at)}</td>
                  <td className="admin-actions-cell">
                    <button
                      className="btn-ghost-sm"
                      onClick={() => { setDetailTarget(u); setDetailLoading(true); setDetailError(''); adminAPI.getUserDetail(u.id).then(d => { setDetailData(d); setDetailLoading(false) }).catch(e => { setDetailError(e.message); setDetailLoading(false) }) }}
                    >
                      详情
                    </button>
                    <button
                      className="btn-ghost-sm"
                      onClick={() => toggleDisable(u)}
                    >
                      {u.is_disabled ? '启用' : '禁用'}
                    </button>
                    <button
                      className="btn-ghost-sm"
                      onClick={() => { setEmailTarget(u); setNewEmail(u.email || ''); setEmailError(''); setEmailOk('') }}
                    >
                      邮箱
                    </button>
                    {u.email && (
                      <button
                        className="btn-ghost-danger btn-sm"
                        onClick={() => handleClearEmail(u)}
                        title="清除邮箱"
                      >
                        清除邮箱
                      </button>
                    )}
                    <button
                      className="btn-ghost-sm"
                      onClick={() => { setResetTarget(u); setNewPassword(''); setResetError(''); setResetOk('') }}
                    >
                      重置密码
                    </button>
                    {u.id !== authUser?.id && (
                      <button
                        className="btn-ghost-danger btn-sm"
                        onClick={() => { setDeleteTarget(u); setConfirmName(''); setDeleteError('') }}
                        title="删除用户"
                      >
                        <Trash2 size={14} />
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

      <ConfirmModal
        isOpen={!!disableConfirm}
        title="禁用用户"
        message={`确定禁用用户「${disableConfirm?.username}」？`}
        confirmText="确定"
        onConfirm={async () => {
          const user = disableConfirm
          setDisableConfirm(null)
          try {
            await adminAPI.disableUser(user.id)
            await load()
          } catch (err) {
            setActionError(err.message)
          }
        }}
        onCancel={() => setDisableConfirm(null)}
        danger
      />
      <ConfirmModal
        isOpen={!!clearEmailConfirm}
        title="清除邮箱"
        message={`确定清除用户「${clearEmailConfirm?.username}」的邮箱？`}
        confirmText="确定"
        onConfirm={async () => {
          const user = clearEmailConfirm
          setClearEmailConfirm(null)
          try {
            await adminAPI.clearUserEmail(user.id)
            await load()
          } catch (err) {
            setActionError(err.message)
          }
        }}
        onCancel={() => setClearEmailConfirm(null)}
        danger
      />

      {/* User Detail Modal */}
      {detailTarget && (
        <div className="modal-overlay" onClick={() => { setDetailTarget(null); setDetailData(null); setDetailError('') }}>
          <div className="modal-card" style={{ maxWidth: 520 }} onClick={(e) => e.stopPropagation()}>
            <h3 className="modal-title">用户详情 — {detailTarget.username}</h3>
            <div className="modal-body">
              {detailLoading ? (
                <div className="admin-loading">加载中…</div>
              ) : detailError ? (
                <div className="admin-error-banner"><span>{detailError}</span></div>
              ) : detailData ? (
                <div className="user-detail-grid">
                  <div className="user-detail-field">
                    <span className="user-detail-label">ID</span>
                    <span className="user-detail-value">{detailData.id}</span>
                  </div>
                  <div className="user-detail-field">
                    <span className="user-detail-label">用户名</span>
                    <span className="user-detail-value">{detailData.username}</span>
                  </div>
                  <div className="user-detail-field">
                    <span className="user-detail-label">邮箱</span>
                    <span className="user-detail-value">{detailData.email || '-'}</span>
                  </div>
                  <div className="user-detail-field">
                    <span className="user-detail-label">角色</span>
                    <span className="user-detail-value">{detailData.is_admin ? '管理员' : '用户'}</span>
                  </div>
                  <div className="user-detail-field">
                    <span className="user-detail-label">状态</span>
                    <span className={`admin-status${detailData.is_disabled ? ' disabled' : ''}`}>
                      {detailData.is_disabled ? '已禁用' : '正常'}
                    </span>
                  </div>
                  <div className="user-detail-field">
                    <span className="user-detail-label">注册时间</span>
                    <span className="user-detail-value">{detailData.created_at?.slice(0, 16).replace('T', ' ')}</span>
                  </div>
                  <div className="user-detail-field">
                    <span className="user-detail-label">最后登录</span>
                    <span className="user-detail-value">{detailData.last_login_at ? detailData.last_login_at.slice(0, 16).replace('T', ' ') : '-'}</span>
                  </div>
                  <div className="user-detail-divider" />
                  <div className="user-detail-field">
                    <span className="user-detail-label">角色卡数</span>
                    <span className="user-detail-value">{detailData.cards_count}</span>
                  </div>
                  <div className="user-detail-field">
                    <span className="user-detail-label">会话数</span>
                    <span className="user-detail-value">{detailData.sessions_count}</span>
                  </div>
                  <div className="user-detail-field">
                    <span className="user-detail-label">API 调用</span>
                    <span className="user-detail-value">{detailData.usage?.calls || 0}</span>
                  </div>
                  <div className="user-detail-field">
                    <span className="user-detail-label">输入 Token</span>
                    <span className="user-detail-value">{(detailData.usage?.prompt_tokens || 0).toLocaleString()}</span>
                  </div>
                  <div className="user-detail-field">
                    <span className="user-detail-label">输出 Token</span>
                    <span className="user-detail-value">{(detailData.usage?.completion_tokens || 0).toLocaleString()}</span>
                  </div>
                  {detailData.login_history?.length > 0 && (
                    <>
                      <div className="user-detail-divider" />
                      <div className="user-detail-field" style={{ gridColumn: '1 / -1' }}>
                        <span className="user-detail-label">最近活跃</span>
                        <div className="user-detail-login-list">
                          {detailData.login_history.slice(0, 10).map((t, i) => (
                            <span key={i} className="user-detail-login-item">{t?.slice(0, 16).replace('T', ' ')}</span>
                          ))}
                        </div>
                      </div>
                    </>
                  )}
                </div>
              ) : null}
            </div>
            <div className="modal-actions">
              <button className="btn-ghost" onClick={() => { setDetailTarget(null); setDetailData(null); setDetailError('') }}>关闭</button>
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
  const [batchDeleteCodesConfirm, setBatchDeleteCodesConfirm] = useState(false)

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
    setBatchDeleteCodesConfirm(true)
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
        <button className="btn-primary invite-generate-btn" onClick={generate} disabled={generating}>
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
                      className="btn-ghost-sm"
                      onClick={() => copyCode(c.code)}
                    >
                      {copied === c.code ? '已复制' : '复制'}
                    </button>
                  )}
                  <button
                    className="btn-ghost-danger btn-sm"
                    onClick={() => setDeleteTarget(c.code)}
                    title="删除邀请码"
                  >
                    <Trash2 size={14} />
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
            className="btn-ghost-sm"
            onClick={handleBatchDelete}
            disabled={batchDeleting}
          >
            <Trash2 size={14} /> {batchDeleting ? '删除中…' : '批量删除已使用的'}
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

      <ConfirmModal
        isOpen={batchDeleteCodesConfirm}
        title="批量删除"
        message="确定批量删除所有已使用的邀请码？"
        confirmText="确定"
        onConfirm={async () => {
          setBatchDeleteCodesConfirm(false)
          setBatchDeleting(true)
          try {
            await adminAPI.deleteUsedInvites()
            await load()
          } catch (err) {
            setError(err.message)
          } finally {
            setBatchDeleting(false)
          }
        }}
        onCancel={() => setBatchDeleteCodesConfirm(false)}
        danger
      />
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
        <>
          <div className="admin-stats-grid">
            <div className="admin-stat-card">
              <span className="admin-stat-value">{data.length}</span>
              <span className="admin-stat-label">用户数</span>
            </div>
            <div className="admin-stat-card">
              <span className="admin-stat-value">{fmt(data.reduce((s, r) => s + (r.total_calls || 0), 0))}</span>
              <span className="admin-stat-label">总调用次数</span>
            </div>
            <div className="admin-stat-card">
              <span className="admin-stat-value">{fmt(data.reduce((s, r) => s + (r.total_prompt_tokens || 0) + (r.total_completion_tokens || 0), 0))}</span>
              <span className="admin-stat-label">总 Token</span>
            </div>
          </div>
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
        </>
      )}
    </div>
  )
}

function ReportsTab() {
  const [reports, setReports] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [confirmDeleteId, setConfirmDeleteId] = useState(null)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await adminAPI.listReports()
      setReports(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const handleResolve = async (commentId) => {
    try {
      await adminAPI.resolveReport(commentId)
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  const handleDeleteComment = async () => {
    const commentId = confirmDeleteId
    setConfirmDeleteId(null)
    setDeleting(true)
    try {
      await adminAPI.deleteReportedComment(commentId)
      await load()
    } catch (err) {
      setError(err.message)
    } finally {
      setDeleting(false)
    }
  }

  const fmtDate = (iso) => {
    if (!iso) return '-'
    return iso.slice(0, 16).replace('T', ' ')
  }

  return (
    <div className="admin-card">
      <div className="admin-card-title">举报管理</div>
      {error && (
        <div className="admin-error-banner">
          <span>{error}</span>
          <button className="admin-error-close" onClick={() => setError('')}>✕</button>
        </div>
      )}
      {loading ? (
        <div className="admin-loading">加载中…</div>
      ) : reports.length === 0 ? (
        <p style={{ padding: '20px 16px', color: 'var(--text-secondary)', fontSize: 13 }}>暂无待处理的举报</p>
      ) : (
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th style={{ minWidth: 200 }}>评论内容</th>
                <th style={{ minWidth: 80 }}>评论作者</th>
                <th style={{ minWidth: 80 }}>举报次数</th>
                <th style={{ minWidth: 150 }}>举报原因</th>
                <th style={{ minWidth: 120 }}>首次举报</th>
                <th style={{ minWidth: 140 }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {reports.map((r) => (
                <tr key={r.comment_id}>
                  <td style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {r.comment_content}
                  </td>
                  <td>{r.comment_author_name}</td>
                  <td><span className="admin-status" style={r.report_count > 1 ? { color: '#ef4444' } : {}}>{r.report_count}</span></td>
                  <td style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.reasons}>
                    {r.reasons}
                  </td>
                  <td>{fmtDate(r.first_reported_at)}</td>
                  <td className="admin-actions-cell">
                    <button
                      className="btn-ghost-sm"
                      onClick={() => handleResolve(r.comment_id)}
                    >
                      驳回
                    </button>
                    <button
                      className="btn-ghost-danger btn-sm"
                      onClick={() => setConfirmDeleteId(r.comment_id)}
                    >
                      <Trash2 size={14} /> 删除评论
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ConfirmModal
        isOpen={!!confirmDeleteId}
        title="删除被举报评论"
        message="确定删除此评论？删除后无法恢复，相关举报将自动关闭。"
        confirmText="删除"
        onConfirm={handleDeleteComment}
        onCancel={() => setConfirmDeleteId(null)}
        danger
      />
    </div>
  )
}

// ============================================================
// P1-1: Content Moderation
// ============================================================

function ContentAuditTab() {
  const [cards, setCards] = useState([])
  const [posts, setPosts] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [subTab, setSubTab] = useState('cards')
  const [actionMsg, setActionMsg] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [c, p] = await Promise.all([adminAPI.listCards(), adminAPI.listPosts()])
      setCards(c)
      setPosts(p)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const handleTakedown = async (cardId) => {
    try {
      await adminAPI.takedownCard(cardId)
      setActionMsg('已下架卡片')
      setTimeout(() => setActionMsg(''), 2000)
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  const handleDeletePost = async (postId) => {
    try {
      await adminAPI.deletePost(postId)
      setActionMsg('已删除帖子')
      setTimeout(() => setActionMsg(''), 2000)
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  const handleBan = async (userId) => {
    try {
      await adminAPI.banUser(userId)
      setActionMsg('已封禁用户')
      setTimeout(() => setActionMsg(''), 2000)
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  if (loading) return <div className="admin-loading">加载中…</div>

  return (
    <div className="admin-card">
      <div className="admin-card-title">内容审核</div>
      {error && (
        <div className="admin-error-banner">
          <span>{error}</span>
          <button className="admin-error-close" onClick={() => setError('')}>✕</button>
        </div>
      )}
      {actionMsg && <div className="admin-success-banner">{actionMsg}</div>}
      <div className="admin-subtabs">
        <button className={`admin-subtab${subTab === 'cards' ? ' active' : ''}`} onClick={() => setSubTab('cards')}>角色卡</button>
        <button className={`admin-subtab${subTab === 'posts' ? ' active' : ''}`} onClick={() => setSubTab('posts')}>用户帖子</button>
      </div>

      {subTab === 'cards' ? (
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th style={{ minWidth: 80 }}>ID</th>
                <th style={{ minWidth: 100 }}>角色名</th>
                <th style={{ minWidth: 80 }}>作者</th>
                <th style={{ minWidth: 70 }}>可见性</th>
                <th style={{ minWidth: 140 }}>创建时间</th>
                <th style={{ minWidth: 100 }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {cards.map((c) => (
                <tr key={c.id}>
                  <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{c.id?.slice(0, 8)}</td>
                  <td>{c.name || '-'}</td>
                  <td>{c.username}</td>
                  <td>
                    <span className={`admin-status${c.visibility === 'public' ? '' : ' disabled'}`}>
                      {c.visibility === 'public' ? '公开' : '非公开'}
                    </span>
                  </td>
                  <td style={{ fontSize: 13, color: 'var(--text-secondary)' }}>{c.created_at?.slice(0, 16).replace('T', ' ')}</td>
                  <td className="admin-actions-cell">
                    {c.visibility === 'public' && (
                      <button className="btn-ghost-danger btn-sm" onClick={() => handleTakedown(c.id)}>
                        下架
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {cards.length === 0 && (
                <tr><td colSpan={6} style={{ textAlign: 'center', color: 'var(--text-secondary)', padding: 24 }}>暂无角色卡</td></tr>
              )}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th style={{ minWidth: 80 }}>作者</th>
                <th style={{ minWidth: 300 }}>内容</th>
                <th style={{ minWidth: 70 }}>可见性</th>
                <th style={{ minWidth: 140 }}>创建时间</th>
                <th style={{ minWidth: 120 }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {posts.map((p) => (
                <tr key={p.id}>
                  <td>{p.username}</td>
                  <td style={{ maxWidth: 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.content}</td>
                  <td>{p.visibility}</td>
                  <td style={{ fontSize: 13, color: 'var(--text-secondary)' }}>{p.created_at?.slice(0, 16).replace('T', ' ')}</td>
                  <td className="admin-actions-cell">
                    <button className="btn-ghost-danger btn-sm" onClick={() => handleDeletePost(p.id)}>
                      <Trash2 size={14} /> 删除
                    </button>
                    <button className="btn-ghost-danger btn-sm" onClick={() => handleBan(p.user_id)}>
                      封禁
                    </button>
                  </td>
                </tr>
              ))}
              {posts.length === 0 && (
                <tr><td colSpan={5} style={{ textAlign: 'center', color: 'var(--text-secondary)', padding: 24 }}>暂无帖子</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ============================================================
// P1-2: System Logs & Tasks
// ============================================================

function SystemLogTab() {
  const [logs, setLogs] = useState([])
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [subTab, setSubTab] = useState('logs')
  const [logLevel, setLogLevel] = useState('all')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [l, t] = await Promise.all([adminAPI.getLogs(), adminAPI.getTasks()])
      setLogs(l)
      setTasks(t)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const filteredLogs = logLevel === 'all' ? logs : logs.filter(l => l.level === logLevel)
  const levelCounts = logs.reduce((acc, l) => { acc[l.level] = (acc[l.level] || 0) + 1; return acc }, {})

  return (
    <div className="admin-card">
      <div className="admin-card-title">系统日志与任务</div>
      {error && (
        <div className="admin-error-banner">
          <span>{error}</span>
          <button className="admin-error-close" onClick={() => setError('')}>✕</button>
        </div>
      )}
      <div className="admin-subtabs">
        <button className={`admin-subtab${subTab === 'logs' ? ' active' : ''}`} onClick={() => setSubTab('logs')}>日志</button>
        <button className={`admin-subtab${subTab === 'tasks' ? ' active' : ''}`} onClick={() => setSubTab('tasks')}>蒸馏任务</button>
      </div>

      {subTab === 'logs' ? (
        <>
          <div className="log-level-filter">
            {['all', 'WARNING', 'ERROR', 'CRITICAL'].map(l => (
              <button
                key={l}
                className={`log-level-btn${logLevel === l ? ' active' : ''}`}
                onClick={() => setLogLevel(l)}
              >
                {l === 'all' ? '全部' : l}{l !== 'all' && levelCounts[l] != null ? ` (${levelCounts[l]})` : ''}
              </button>
            ))}
          </div>
          {loading ? (
            <div className="admin-loading">加载中…</div>
          ) : filteredLogs.length === 0 ? (
            <p style={{ padding: '20px 16px', color: 'var(--text-secondary)', fontSize: 13 }}>暂无日志</p>
          ) : (
            <div className="log-list">
              {filteredLogs.map((entry, i) => (
                <div key={i} className={`log-entry log-level-${entry.level?.toLowerCase()}`}>
                  <span className="log-time">{entry.time}</span>
                  <span className={`log-badge log-badge-${entry.level?.toLowerCase()}`}>{entry.level}</span>
                  <span className="log-name">{entry.name}</span>
                  <span className="log-message">{entry.message}</span>
                </div>
              ))}
            </div>
          )}
        </>
      ) : (
        <>
          <div className="admin-stats-grid" style={{ marginBottom: 16 }}>
            <div className="admin-stat-card">
              <span className="admin-stat-value">{tasks.filter(t => t.status === 'queued' || t.status === 'identifying' || t.status === 'analyzing' || t.status === 'distilling').length}</span>
              <span className="admin-stat-label">运行中</span>
            </div>
            <div className="admin-stat-card">
              <span className="admin-stat-value">{tasks.filter(t => t.status === 'done').length}</span>
              <span className="admin-stat-label">已完成</span>
            </div>
            <div className="admin-stat-card">
              <span className="admin-stat-value">{tasks.filter(t => t.status === 'error').length}</span>
              <span className="admin-stat-label">失败</span>
            </div>
            <div className="admin-stat-card">
              <span className="admin-stat-value">{tasks.length}</span>
              <span className="admin-stat-label">总计</span>
            </div>
          </div>
          {loading ? (
            <div className="admin-loading">加载中…</div>
          ) : tasks.length === 0 ? (
            <p style={{ padding: '20px 16px', color: 'var(--text-secondary)', fontSize: 13 }}>暂无任务</p>
          ) : (
            <div className="admin-table-wrap">
              <table className="admin-table">
                <thead>
                  <tr>
                    <th style={{ minWidth: 80 }}>任务 ID</th>
                    <th style={{ minWidth: 70 }}>状态</th>
                    <th style={{ minWidth: 70 }}>进度</th>
                    <th style={{ minWidth: 80 }}>角色</th>
                    <th style={{ minWidth: 200 }}>消息</th>
                  </tr>
                </thead>
                <tbody>
                  {tasks.map((t) => (
                    <tr key={t.task_id}>
                      <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{t.task_id?.slice(0, 8)}</td>
                      <td>
                        <span className={`admin-status${t.status === 'error' ? ' disabled' : t.status === 'done' ? '' : ''}`}>
                          {t.status === 'done' ? '完成' : t.status === 'error' ? '失败' : t.status === 'queued' ? '排队中' : t.status || '-'}
                        </span>
                      </td>
                      <td>{t.progress_pct != null ? `${t.progress_pct}%` : '-'}</td>
                      <td>{t.character || '-'}</td>
                      <td style={{ fontSize: 13, color: 'var(--text-secondary)', maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.message || '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ============================================================
// P2-1: Announcements
// ============================================================

function AnnouncementsTab() {
  const [announcements, setAnnouncements] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [content, setContent] = useState('')
  const [creating, setCreating] = useState(false)
  const [actionMsg, setActionMsg] = useState('')
  const [deleteTarget, setDeleteTarget] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await adminAPI.listAnnouncements()
      setAnnouncements(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const handleCreate = async () => {
    if (!content.trim()) return
    setCreating(true)
    setError('')
    try {
      await adminAPI.createAnnouncement(content.trim())
      setContent('')
      setActionMsg('公告已发布')
      setTimeout(() => setActionMsg(''), 2000)
      await load()
    } catch (err) {
      setError(err.message)
    } finally {
      setCreating(false)
    }
  }

  const handleDelete = async () => {
    try {
      await adminAPI.deleteAnnouncement(deleteTarget)
      setDeleteTarget(null)
      setActionMsg('公告已删除')
      setTimeout(() => setActionMsg(''), 2000)
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  const fmtDate = (iso) => {
    if (!iso) return '-'
    return iso.slice(0, 16).replace('T', ' ')
  }

  return (
    <div className="admin-card">
      <div className="admin-card-title">公告管理</div>
      {error && (
        <div className="admin-error-banner">
          <span>{error}</span>
          <button className="admin-error-close" onClick={() => setError('')}>✕</button>
        </div>
      )}
      {actionMsg && <div className="admin-success-banner">{actionMsg}</div>}

      <div className="announcement-create">
        <textarea
          className="announcement-textarea"
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="输入公告内容…"
          rows={3}
        />
        <button className="btn-primary" onClick={handleCreate} disabled={creating || !content.trim()}>
          {creating ? '发布中…' : '发布公告'}
        </button>
      </div>

      {loading ? (
        <div className="admin-loading">加载中…</div>
      ) : announcements.length === 0 ? (
        <p style={{ padding: '20px 16px', color: 'var(--text-secondary)', fontSize: 13 }}>暂无公告</p>
      ) : (
        <div className="announcement-list">
          {announcements.map((a) => (
            <div key={a.id} className={`announcement-item${a.is_active ? ' active' : ''}`}>
              <div className="announcement-item-header">
                <span className={`announcement-badge${a.is_active ? ' active' : ''}`}>
                  {a.is_active ? '当前' : '历史'}
                </span>
                <span className="announcement-time">{fmtDate(a.created_at)}</span>
                <button className="btn-ghost-danger btn-sm" onClick={() => setDeleteTarget(a.id)}>
                  <Trash2 size={14} />
                </button>
              </div>
              <div className="announcement-item-content">{a.content}</div>
            </div>
          ))}
        </div>
      )}

      {deleteTarget && (
        <div className="modal-overlay" onClick={() => setDeleteTarget(null)}>
          <div className="modal-card" style={{ maxWidth: 380 }} onClick={(e) => e.stopPropagation()}>
            <h3 className="modal-title">确定删除此公告？</h3>
            <div className="modal-body">
              <p style={{ fontSize: 14, color: 'var(--text-secondary)', margin: 0 }}>删除后无法恢复。</p>
            </div>
            <div className="modal-actions">
              <button className="btn-ghost" onClick={() => setDeleteTarget(null)}>取消</button>
              <button className="btn-primary" onClick={handleDelete}>确认删除</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ============================================================
// P2-3: Data Export
// ============================================================

function ExportTab() {
  const [loadingUsers, setLoadingUsers] = useState(false)
  const [loadingUsage, setLoadingUsage] = useState(false)
  const [msg, setMsg] = useState('')

  const downloadCSV = (text, filename) => {
    const blob = new Blob([text], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
  }

  const exportUsers = async () => {
    setLoadingUsers(true)
    setMsg('')
    try {
      const csv = await adminAPI.exportUsersCSV()
      downloadCSV(csv, `users_${new Date().toISOString().slice(0, 10)}.csv`)
      setMsg('用户数据已导出')
    } catch (err) {
      setMsg(`导出失败: ${err.message}`)
    } finally {
      setLoadingUsers(false)
    }
  }

  const exportUsage = async () => {
    setLoadingUsage(true)
    setMsg('')
    try {
      const csv = await adminAPI.exportUsageCSV()
      downloadCSV(csv, `usage_${new Date().toISOString().slice(0, 10)}.csv`)
      setMsg('用量数据已导出')
    } catch (err) {
      setMsg(`导出失败: ${err.message}`)
    } finally {
      setLoadingUsage(false)
    }
  }

  return (
    <div className="admin-card">
      <div className="admin-card-title">数据导出</div>
      {msg && <div className={`${msg.includes('失败') ? 'admin-error-banner' : 'admin-success-banner'}`}>{msg}</div>}
      <div className="export-grid">
        <div className="export-card">
          <div className="export-card-title">用户数据</div>
          <p className="export-card-desc">导出所有用户的基本信息为 CSV 文件。</p>
          <button className="btn-primary" onClick={exportUsers} disabled={loadingUsers}>
            {loadingUsers ? '导出中…' : '导出用户 CSV'}
          </button>
        </div>
        <div className="export-card">
          <div className="export-card-title">用量统计</div>
          <p className="export-card-desc">导出所有用户的 API 用量统计为 CSV 文件。</p>
          <button className="btn-primary" onClick={exportUsage} disabled={loadingUsage}>
            {loadingUsage ? '导出中…' : '导出用量 CSV'}
          </button>
        </div>
      </div>
    </div>
  )
}
