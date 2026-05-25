import { useCallback, useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout } from '../api/client'
import Avatar from './common/Avatar'
import ImageCropModal from './common/ImageCropModal'

export default function ProfilePage() {
  const authUser = useAppStore((s) => s.authUser)
  const userAvatar = useAppStore((s) => s.userAvatar)
  const setUserAvatar = useAppStore((s) => s.setUserAvatar)
  const loadUserAvatar = useAppStore((s) => s.loadUserAvatar)
  const saveUserAvatar = useAppStore((s) => s.saveUserAvatar)
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

  useEffect(() => {
    loadUserAvatar()
  }, [loadUserAvatar])

  // Load email from /api/auth/me
  useEffect(() => {
    fetchWithTimeout('/api/auth/me')
      .then((r) => r.json())
      .then((data) => {
        setEmail(data.email || '')
        setEmailVerified(data.email_verified || false)
      })
      .catch(() => {})
  }, [])

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

  // ---- Email binding ----
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

  const createdDate = authUser?.created_at
    ? new Date((authUser.created_at.includes('T') && !authUser.created_at.endsWith('Z') && !authUser.created_at.includes('+') ? authUser.created_at + 'Z' : authUser.created_at)).toLocaleDateString('zh-CN')
    : '—'

  return (
    <div className="profile-page">
      {/* 顶部：个人资料卡 */}
      <div className="profile-card">
        <div className="profile-avatar-section">
          <button
            type="button"
            className="profile-avatar-wrap"
            onClick={() => avatarInputRef.current?.click()}
            title="更换头像"
            disabled={avatarSaving}
          >
            <Avatar name={authUser?.username || '?'} src={userAvatar} size={96} />
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

      {/* 3项网格：音色/密码/邮箱 */}
      <div className="profile-grid profile-grid-3">
        <button className="profile-grid-item" onClick={() => setView('voice')}>
          <span className="profile-grid-icon">{'\u{1F399}'}</span>
          <span className="profile-grid-label">音色管理</span>
        </button>
        <button className="profile-grid-item" onClick={() => togglePanel('password')}>
          <span className="profile-grid-icon">{'\u{1F512}'}</span>
          <span className="profile-grid-label">修改密码</span>
        </button>
        <button className="profile-grid-item" onClick={() => togglePanel('bind')}>
          <span className="profile-grid-icon">{'\u{1F4E7}'}</span>
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

      <ImageCropModal
        file={cropFile}
        onConfirm={handleCropConfirm}
        onCancel={handleCropCancel}
      />
    </div>
  )
}
