import { useState, useMemo, useRef } from 'react'
import useAppStore from '../store/useAppStore'

function passwordStrength(pw) {
  if (!pw) return { level: 0, label: '', color: '' }
  const hasLetter = /[a-zA-Z]/.test(pw)
  const hasDigit = /[0-9]/.test(pw)
  const longEnough = pw.length >= 8
  if (longEnough && hasLetter && hasDigit) return { level: 3, label: '强', color: '#22c55e' }
  if (pw.length >= 6 && hasLetter && hasDigit) return { level: 2, label: '中', color: '#f59e0b' }
  return { level: 1, label: '弱', color: '#ef4444' }
}

export default function LoginPage() {
  const [tab, setTab] = useState('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [inviteCode, setInviteCode] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const login = useAppStore((s) => s.login)
  const register = useAppStore((s) => s.register)

  const nameSuffix = useRef(Date.now())

  const strength = useMemo(() => passwordStrength(password), [password])

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    if (!username.trim() || !password.trim()) {
      setError('请填写用户名和密码')
      return
    }
    if (tab === 'register' && !inviteCode.trim()) {
      setError('请填写邀请码')
      return
    }
    if (tab === 'register' && strength.level < 3) {
      setError('密码至少 8 位，需包含字母和数字')
      return
    }
    setLoading(true)
    try {
      if (tab === 'login') {
        await login(username.trim(), password)
      } else {
        await register(username.trim(), password, inviteCode.trim())
      }
    } catch (err) {
      setError(err.message || '操作失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <h1 className="login-title">Character Distill</h1>
        <p className="login-subtitle">角色蒸馏 · 智能对话</p>

        <div className="login-tabs">
          <button
            className={`login-tab${tab === 'login' ? ' active' : ''}`}
            onClick={() => { setTab('login'); setError('') }}
          >
            登录
          </button>
          <button
            className={`login-tab${tab === 'register' ? ' active' : ''}`}
            onClick={() => { setTab('register'); setError('') }}
          >
            注册
          </button>
        </div>

        <form className="login-form" onSubmit={handleSubmit}>
          <div className="login-field">
            <label htmlFor={tab === 'register' ? `reg-user-${nameSuffix.current}` : 'login-username'}>用户名</label>
            <input
              id={tab === 'register' ? `reg-user-${nameSuffix.current}` : 'login-username'}
              type="text"
              name={tab === 'register' ? `reg-user-${nameSuffix.current}` : 'username'}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="请输入用户名"
              autoComplete={tab === 'login' ? 'username' : 'off'}
              autoFocus
            />
          </div>
          <div className="login-field">
            <label htmlFor={tab === 'register' ? `reg-pass-${nameSuffix.current}` : 'login-password'}>密码</label>
            <input
              id={tab === 'register' ? `reg-pass-${nameSuffix.current}` : 'login-password'}
              type="password"
              name={tab === 'register' ? `reg-pass-${nameSuffix.current}` : 'password'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="请输入密码"
              autoComplete={tab === 'login' ? 'current-password' : 'new-password'}
            />
          </div>

          {tab === 'register' && (
            <>
              {strength.label && (
                <div className="login-pw-strength" style={{ marginBottom: 12 }}>
                  <span className="login-pw-strength-bar" style={{ width: `${strength.level * 33}%`, backgroundColor: strength.color }} />
                </div>
              )}
              <div className="login-field">
                <label htmlFor="login-invite">邀请码</label>
                <input
                  id="login-invite"
                  type="text"
                  name={`invite-${nameSuffix.current}`}
                  value={inviteCode}
                  onChange={(e) => setInviteCode(e.target.value)}
                  placeholder="请输入邀请码"
                  autoComplete="off"
                />
              </div>
            </>
          )}

          {error && <div className="login-error">{error}</div>}

          <button className="login-submit" type="submit" disabled={loading || (tab === 'register' && strength.level < 3)}>
            {loading ? '请稍候…' : tab === 'login' ? '登录' : '注册'}
          </button>
        </form>
      </div>
    </div>
  )
}
