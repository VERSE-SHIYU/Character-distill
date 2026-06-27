import { useCallback, useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout } from '../api/client'
import { displayName } from '../utils/displayName'
import Avatar from './common/Avatar'
import ImageCropModal from './common/ImageCropModal'
import { Heart, Star, Theater, Book, Mic, Lock, Mail } from './common/Icon'

export default function ProfilePage() {
  const authUser = useAppStore((s) => s.authUser)
  const userAvatar = useAppStore((s) => s.userAvatar)
  const setUserAvatar = useAppStore((s) => s.setUserAvatar)
  const loadUserAvatar = useAppStore((s) => s.loadUserAvatar)
  const saveUserAvatar = useAppStore((s) => s.saveUserAvatar)
  const updateNickname = useAppStore((s) => s.updateNickname)
  const setView = useAppStore((s) => s.setView)

  const [cropFile, setCropFile] = useState(null)
  const avatarInputRef = useRef(null)
  const [avatarSaving, setAvatarSaving] = useState(false)
  const [avatarMsg, setAvatarMsg] = useState('')

  const [pwSubmitting, setPwSubmitting] = useState(false)
  const [pwMsg, setPwMsg] = useState('')
  const [pwError, setPwError] = useState(false)
  const [oldPw, setOldPw] = useState('')
  const [newPw, setNewPw] = useState('')
  const [confirmPw, setConfirmPw] = useState('')

  // Nickname
  const [nickname, setNickname] = useState(authUser?.nickname || '')
  const [nicknameSaving, setNicknameSaving] = useState(false)
  const [nicknameMsg, setNicknameMsg] = useState('')
  const [nicknameError, setNicknameError] = useState(false)

  // Sync nickname state when authUser changes (e.g. after save)
  useEffect(() => {
    setNickname(authUser?.nickname || '')
  }, [authUser?.nickname])

  // Email binding
  const [email, setEmail] = useState('')
  const [emailVerified, setEmailVerified] = useState(false)
  const [bindEmail, setBindEmail] = useState('')
  const [bindCode, setBindCode] = useState('')
  const [bindCountdown, setBindCountdown] = useState(0)
  const [bindSent, setBindSent] = useState(false)
  const [bindMsg, setBindMsg] = useState('')
  const [bindError, setBindError] = useState(false)
  const [showBindForm, setShowBindForm] = useState(false)
  const [showPasswordForm, setShowPasswordForm] = useState(false)

  // Privacy + stats
  const [privacy, setPrivacy] = useState({ stats_visible: true, cards_visible: true, books_visible: true, following_visible: true })
  const [stats, setStats] = useState({ followers_count: 0, following_count: 0, cards_count: 0, texts_count: 0 })

  useEffect(() => {
    loadUserAvatar()
  }, [loadUserAvatar])

  // Load email + privacy from /api/auth/me
  useEffect(() => {
    fetchWithTimeout('/api/auth/me')
      .then((r) => r.json())
      .then((data) => {
        setEmail(data.email || '')
        setEmailVerified(data.email_verified || false)
        setPrivacy({
          stats_visible: data.profile_stats_visible !== false,
          cards_visible: data.cards_visible !== false,
          books_visible: data.books_visible !== false,
          following_visible: data.following_visible !== false,
        })
      })
      .catch(() => {})
  }, [])

  // Load stats from author endpoint
  useEffect(() => {
    if (!authUser?.id) return
    fetchWithTimeout(`/api/market/author/${authUser.id}`)
      .then((r) => r.json())
      .then((data) => {
        setStats({
          followers_count: data.followers_count || 0,
          following_count: data.following_count || 0,
          cards_count: (data.cards || []).length,
          texts_count: (data.texts || []).length,
        })
        setPrivacy({
          stats_visible: data.stats_visible !== false,
          cards_visible: data.cards_visible !== false,
          books_visible: data.books_visible !== false,
          following_visible: data.following_visible !== false,
        })
      })
      .catch(() => {})
  }, [authUser?.id])

  const togglePanel = (panel) => {
    const isOpening = (panel === 'password' && !showPasswordForm) ||
                      (panel === 'bind' && !showBindForm)
    setShowPasswordForm(panel === 'password' ? !showPasswordForm : false)
    setShowBindForm(panel === 'bind' ? !showBindForm : false)
    if (isOpening) {
      setTimeout(() => {
        document.querySelector('.profile-card:last-of-type')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
      }, 50)
    }
  }

  const handleAvatarChange = useCallback((e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setCropFile(file)
    e.target.value = ''
  }, [])

  const handleCropConfirm = useCallback(async (base64) => {
    setCropFile(null)
    setAvatarSaving(true)
    setAvatarMsg('')
    try {
      await saveUserAvatar(base64)
      setUserAvatar(base64)
      setAvatarMsg('头像已更新')
    } catch (err) {
      setAvatarMsg(err.message || '保存失败')
    } finally {
      setAvatarSaving(false)
    }
  }, [saveUserAvatar, setUserAvatar])

  const handleCropCancel = useCallback(() => setCropFile(null), [])

  const handlePasswordChange = useCallback(async (e) => {
    e.preventDefault()
    setPwMsg('')
    setPwError(false)

    if (newPw.length < 8) {
      setPwMsg('新密码至少 8 位')
      setPwError(true)
      return
    }
    if (!/[a-zA-Z]/.test(newPw) || !/\d/.test(newPw)) {
      setPwMsg('新密码需包含字母和数字')
      setPwError(true)
      return
    }
    if (newPw !== confirmPw) {
      setPwMsg('两次输入的新密码不一致')
      setPwError(true)
      return
    }

    setPwSubmitting(true)
    try {
      await fetchWithTimeout('/api/auth/password', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ old_password: oldPw, new_password: newPw }),
      })
      setPwMsg('密码修改成功')
      setPwError(false)
      setOldPw('')
      setNewPw('')
      setConfirmPw('')
    } catch (err) {
      setPwMsg(err.message || '修改失败')
      setPwError(true)
    } finally {
      setPwSubmitting(false)
    }
  }, [oldPw, newPw, confirmPw])

  const handleSendBindCode = useCallback(async () => {
    if (!bindEmail || !bindEmail.includes('@')) {
      setBindMsg('请输入有效的邮箱地址')
      setBindError(true)
      return
    }
    if (bindEmail === email) {
      setBindMsg('新邮箱不能与当前邮箱相同')
      setBindError(true)
      return
    }
    setBindSent(true)
    setBindCountdown(60)
    const timer = setInterval(() => {
      setBindCountdown((c) => {
        if (c <= 1) { clearInterval(timer); return 0 }
        return c - 1
      })
    }, 1000)
    try {
      await fetchWithTimeout('/api/auth/send-code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: bindEmail, purpose: 'bind_email' }),
      })
      setBindMsg('验证码已发送')
      setBindError(false)
    } catch (err) {
      setBindMsg(err.message || '发送失败')
      setBindError(true)
    }
  }, [bindEmail])

  const handleBindEmail = useCallback(async () => {
    setBindMsg('')
    setBindError(false)
    try {
      await fetchWithTimeout('/api/auth/email', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: bindEmail, code: bindCode }),
      })
      setEmail(bindEmail)
      setEmailVerified(true)
      setShowBindForm(false)
      setBindMsg('邮箱绑定成功')
      setBindEmail('')
      setBindCode('')
    } catch (err) {
      setBindMsg(err.message || '绑定失败')
      setBindError(true)
    }
  }, [bindEmail, bindCode])

  const handleNicknameSave = useCallback(async () => {
    const trimmed = nickname.trim()
    if (trimmed.length > 30) {
      setNicknameMsg('昵称长度不能超过 30 个字符')
      setNicknameError(true)
      return
    }
    setNicknameSaving(true)
    setNicknameMsg('')
    setNicknameError(false)
    try {
      await updateNickname(trimmed)
      setNicknameMsg('昵称已更新')
    } catch (err) {
      setNicknameMsg(err.message || '保存失败')
      setNicknameError(true)
    } finally {
      setNicknameSaving(false)
    }
  }, [nickname, updateNickname])

  const togglePrivacy = useCallback(async (key) => {
    const next = !privacy[key]
    setPrivacy((p) => ({ ...p, [key]: next }))
    try {
      await fetchWithTimeout('/api/market/author/visibility', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [key]: next }),
      })
    } catch {
      setPrivacy((p) => ({ ...p, [key]: !next }))
    }
  }, [privacy])

  const createdDate = authUser?.created_at
    ? new Date((authUser.created_at.includes('T') && !authUser.created_at.endsWith('Z') && !authUser.created_at.includes('+') ? authUser.created_at + 'Z' : authUser.created_at)).toLocaleDateString('zh-CN')
    : '—'

  const statData = [
    { key: 'stats_visible', icon: <Heart size={14} />, label: '粉丝', count: stats.followers_count },
    { key: 'stats_visible', icon: <Star size={14} />, label: '关注', count: stats.following_count },
    { key: 'cards_visible', icon: <Theater size={14} />, label: '角色', count: stats.cards_count },
    { key: 'books_visible', icon: <Book size={14} />, label: '书籍', count: stats.texts_count },
  ]

  return (
    <div className="profile-page">
      {/* 个人资料卡 */}
      <div className="profile-card">
        <div className="profile-avatar-section">
          <button
            type="button"
            className="profile-avatar-wrap"
            onClick={() => avatarInputRef.current?.click()}
            title="更换头像"
            disabled={avatarSaving}
          >
            <Avatar name={displayName(authUser) || '?'} src={userAvatar} size={96} />
            <span className="profile-avatar-overlay">
              {avatarSaving ? '…' : '\u{1F4F7}'}
            </span>
          </button>
          <input
            ref={avatarInputRef}
            type="file"
            accept="image/*"
            className="sr-only"
            onChange={handleAvatarChange}
          />
          <div className="profile-avatar-info">
            <span className="profile-username">{authUser?.username || '—'}</span>
            <span className="profile-avatar-hint">ID: {authUser?.id || '—'} · 注册于 {createdDate}</span>
            {avatarMsg && (
              <span className={`profile-inline-msg${avatarSaving ? '' : ' success'}`}>
                {avatarMsg}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* 统计栏：粉丝 · 关注 · 角色 · 书籍 */}
      <div className="profile-stats-bar">
        {statData.map(({ key, icon, label, count }) => (
          <button
            key={label}
            type="button"
            className={`profile-stat-item${privacy[key] ? '' : ' dim'}`}
            onClick={() => togglePrivacy(key)}
            title={privacy[key] ? '点击隐藏' : '点击公开'}
          >
            <span className="profile-stat-icon">{icon}</span>
            <span className="profile-stat-count">{count}</span>
            <span className="profile-stat-label">{label}</span>
          </button>
        ))}
      </div>

      {/* 昵称设置 */}
      <div className="profile-card">
        <h2 className="profile-section-title">昵称</h2>
        <div className="profile-field">
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input
              className="profile-input"
              style={{ flex: 1 }}
              value={nickname}
              onChange={(e) => setNickname(e.target.value)}
              placeholder={`留空则显示「${authUser?.username || ''}」`}
              maxLength={30}
            />
            <button
              type="button"
              className="btn-primary profile-save-btn"
              onClick={handleNicknameSave}
              disabled={nicknameSaving}
              style={{ whiteSpace: 'nowrap' }}
            >
              {nicknameSaving ? '保存中…' : '保存'}
            </button>
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-dim)', marginTop: 6 }}>
            昵称可重复，为空时显示用户名。用户名不可修改。
          </div>
          {nicknameMsg && (
            <span className={`profile-inline-msg${nicknameError ? ' error' : ' success'}`}>
              {nicknameMsg}
            </span>
          )}
        </div>
      </div>

      {/* 3项网格：音色/密码/邮箱 */}
      <div className="profile-grid profile-grid-3">
        <button className="profile-grid-item" onClick={() => setView('voice')}>
          <span className="profile-grid-icon"><Mic size={16} /></span>
          <span className="profile-grid-label">音色管理</span>
        </button>
        <button className="profile-grid-item" onClick={() => togglePanel('password')}>
          <span className="profile-grid-icon"><Lock size={16} /></span>
          <span className="profile-grid-label">修改密码</span>
        </button>
        <button className="profile-grid-item" onClick={() => togglePanel('bind')}>
          <span className="profile-grid-icon"><Mail size={16} /></span>
          <span className="profile-grid-label">{email ? '换绑邮箱' : '绑定邮箱'}</span>
          {emailVerified && <span className="profile-grid-badge-ok">✓</span>}
        </button>
      </div>

      {/* 展开区域：修改密码 */}
      {showPasswordForm && (
        <div className="profile-card">
          <h2 className="profile-section-title">修改密码</h2>
          <form className="profile-password-form" onSubmit={handlePasswordChange}>
            <div className="profile-field">
              <label className="profile-field-label">当前密码</label>
              <input
                type="password"
                className="profile-input"
                value={oldPw}
                onChange={(e) => setOldPw(e.target.value)}
                required
                autoComplete="current-password"
              />
            </div>
            <div className="profile-field">
              <label className="profile-field-label">新密码</label>
              <input
                type="password"
                className="profile-input"
                value={newPw}
                onChange={(e) => setNewPw(e.target.value)}
                required
                minLength={8}
                autoComplete="new-password"
              />
            </div>
            <div className="profile-field">
              <label className="profile-field-label">确认新密码</label>
              <input
                type="password"
                className="profile-input"
                value={confirmPw}
                onChange={(e) => setConfirmPw(e.target.value)}
                required
                minLength={8}
                autoComplete="new-password"
              />
            </div>
            {pwMsg && (
              <span className={`profile-inline-msg${pwError ? ' error' : ' success'}`}>
                {pwMsg}
              </span>
            )}
            <button
              type="submit"
              className="btn-primary profile-save-btn"
              disabled={pwSubmitting || !oldPw || !newPw || !confirmPw}
            >
              {pwSubmitting ? '提交中…' : '修改密码'}
            </button>
          </form>
        </div>
      )}

      {/* 展开区域：邮箱绑定 */}
      {showBindForm && (
        <div className="profile-card">
          <h2 className="profile-section-title">{email ? '换绑邮箱' : '绑定邮箱'}</h2>
          <div className="profile-bind-email-form">
            <div className="profile-field">
              <label className="profile-field-label">新邮箱</label>
              <div className="profile-code-field">
                <input
                  type="email"
                  className="profile-input"
                  value={bindEmail}
                  onChange={(e) => setBindEmail(e.target.value)}
                  placeholder="输入邮箱地址"
                  autoComplete="email"
                />
              </div>
            </div>
            <div className="profile-field">
              <label className="profile-field-label">验证码</label>
              <div className="profile-code-field">
                <input
                  type="text"
                  className="profile-input"
                  value={bindCode}
                  onChange={(e) => setBindCode(e.target.value)}
                  placeholder="输入验证码"
                  autoComplete="off"
                />
                <button
                  type="button"
                  className="login-code-btn"
                  disabled={bindCountdown > 0}
                  onClick={handleSendBindCode}
                >
                  {bindCountdown > 0 ? `${bindCountdown}s` : bindSent ? '重新发送' : '获取验证码'}
                </button>
              </div>
            </div>
            {bindMsg && (
              <span className={`profile-inline-msg${bindError ? ' error' : ' success'}`}>
                {bindMsg}
              </span>
            )}
            <button
              type="button"
              className="btn-primary profile-save-btn"
              disabled={!bindEmail || !bindCode}
              onClick={handleBindEmail}
            >
              确认绑定
            </button>
          </div>
        </div>
      )}

      {/* 隐私设置 */}
      <div className="profile-card">
        <h2 className="profile-section-title">隐私设置</h2>
        {[
          { key: 'stats_visible', label: '粉丝/关注数公开' },
          { key: 'cards_visible', label: '角色列表公开' },
          { key: 'books_visible', label: '书籍列表公开' },
          { key: 'following_visible', label: '关注列表公开' },
        ].map(({ key, label }) => (
          <div key={key} className="profile-privacy-row">
            <span>{label}</span>
            <label className="profile-toggle">
              <input type="checkbox" checked={privacy[key]} onChange={() => togglePrivacy(key)} />
              <span className="profile-toggle-slider"></span>
            </label>
          </div>
        ))}
        {/* 在线状态 */}
        <div className="profile-privacy-row" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 8, borderBottom: 'none' }}>
          <span>在线状态</span>
          <span style={{ fontSize: 13, color: 'var(--text-dim)' }}>控制其他人是否能看到你的在线状态。设为"不展示"后，你也无法查看他人的在线状态。</span>
          <PresenceVisibilitySetting />
        </div>
      </div>

      <ImageCropModal
        file={cropFile}
        onConfirm={handleCropConfirm}
        onCancel={handleCropCancel}
      />
    </div>
  )
}

function PresenceVisibilitySetting() {
  const [visibility, setVisibility] = useState('friends')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')

  useEffect(() => {
    let cancelled = false
    fetchWithTimeout('/api/auth/presence-visibility')
      .then(r => r.json())
      .then(data => { if (!cancelled) setVisibility(data.presence_visibility || 'friends') })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  const handleChange = async (value) => {
    setSaving(true)
    setMsg('')
    try {
      const res = await fetchWithTimeout('/api/auth/presence-visibility', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ presence_visibility: value }),
      })
      if (!res.ok) throw new Error((await res.json()).detail || '保存失败')
      setVisibility(value)
      setMsg('已更新')
      setTimeout(() => setMsg(''), 2000)
    } catch (err) {
      setMsg(`保存失败: ${err.message}`)
    } finally {
      setSaving(false)
    }
  }

  const OPTIONS = [
    { value: 'all', label: '所有人可见' },
    { value: 'fans', label: '仅粉丝可见' },
    { value: 'mutual', label: '仅互关好友可见' },
    { value: 'none', label: '不展示' },
  ]

  if (loading) return <span style={{ fontSize: 13, color: 'var(--text-dim)' }}>加载中…</span>

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {OPTIONS.map(opt => (
        <label key={opt.value} style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', padding: '6px 0' }}>
          <input
            type="radio"
            name="presence_visibility"
            value={opt.value}
            checked={visibility === opt.value}
            onChange={() => handleChange(opt.value)}
            disabled={saving}
          />
          <span style={{ fontSize: 14, color: 'var(--text)' }}>{opt.label}</span>
        </label>
      ))}
      {msg && <span style={{ fontSize: 13, color: msg.includes('失败') ? '#ef4444' : '#22c55e' }}>{msg}</span>}
    </div>
  )
}
