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

  useEffect(() => {
    loadUserAvatar()
  }, [loadUserAvatar])

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

  const createdDate = authUser?.created_at
    ? new Date(authUser.created_at).toLocaleDateString('zh-CN')
    : '—'

  return (
    <div className="profile-page">
      <div className="profile-card">
        <h2 className="profile-section-title">个人资料</h2>

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
            <span className="profile-avatar-hint">点击头像更换，支持裁剪和压缩</span>
            {avatarMsg && (
              <span className={`profile-inline-msg${avatarSaving ? '' : ' success'}`}>
                {avatarMsg}
              </span>
            )}
          </div>
        </div>

        <div className="profile-details">
          <div className="profile-detail-row">
            <span className="profile-detail-label">用户 ID</span>
            <span className="profile-detail-value mono">{authUser?.id || '—'}</span>
          </div>
          <div className="profile-detail-row">
            <span className="profile-detail-label">注册时间</span>
            <span className="profile-detail-value">{createdDate}</span>
          </div>
          <div className="profile-detail-row">
            <span className="profile-detail-label">角色</span>
            <span className="profile-detail-value">
              {authUser?.is_admin ? '管理员' : '普通用户'}
            </span>
          </div>
        </div>
      </div>

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

      <ImageCropModal
        file={cropFile}
        onConfirm={handleCropConfirm}
        onCancel={handleCropCancel}
      />
    </div>
  )
}
