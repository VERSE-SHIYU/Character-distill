import { useState, useMemo, useRef, useCallback } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout } from '../api/client'

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

  // Email verification
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [codeSent, setCodeSent] = useState(false)
  const [countdown, setCountdown] = useState(0)

  // Forgot password
  const [forgotStep, setForgotStep] = useState('email')  // email → code → done
  const [forgotEmail, setForgotEmail] = useState('')
  const [forgotCode, setForgotCode] = useState('')
  const [forgotNewPw, setForgotNewPw] = useState('')

  const login = useAppStore((s) => s.login)
  const register = useAppStore((s) => s.register)

  const nameSuffix = useRef(Date.now())

  const strength = useMemo(() => passwordStrength(password), [password])

  // ---- Send verification code ----
  const sendCode = useCallback(async (targetEmail, purpose) => {
    setError('')
    if (!targetEmail || !targetEmail.includes('@')) {
      setError('请输入有效的邮箱地址')
      return
    }
    setCodeSent(true)
    setCountdown(60)
    const timer = setInterval(() => {
      setCountdown((c) => {
        if (c <= 1) { clearInterval(timer); return 0 }
        return c - 1
      })
    }, 1000)
    try {
      await fetchWithTimeout('/api/auth/send-code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: targetEmail, purpose }),
      })
    } catch (err) {
      setError(err.message || '发送验证码失败')
    }
  }, [])

  // ---- Forgot password ----
  const handleForgotSendCode = useCallback(() => {
    sendCode(forgotEmail, 'reset_password')
    if (!error) setForgotStep('code')
  }, [forgotEmail, sendCode, error])

  const handleForgotReset = useCallback(async () => {
    setError('')
    if (forgotNewPw.length < 8 || !/[a-zA-Z]/.test(forgotNewPw) || !/\d/.test(forgotNewPw)) {
      setError('新密码至少 8 位，需包含字母和数字')
      return
    }
    setLoading(true)
    try {
      await fetchWithTimeout('/api/auth/reset-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: forgotEmail,
          code: forgotCode,
          new_password: forgotNewPw,
        }),
      })
      setForgotStep('done')
    } catch (err) {
      setError(err.message || '重置密码失败')
    } finally {
      setLoading(false)
    }
  }, [forgotEmail, forgotCode, forgotNewPw])

  // ---- Submit ----
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
        await register(username.trim(), password, inviteCode.trim(), email.trim(), code.trim())
      }
    } catch (err) {
      setError(err.message || '操作失败')
    } finally {
      setLoading(false)
    }
  }

  // ---- Forgot password flow ----
  if (tab === 'forgot') {
    return (
      <div className="login-page">
        <div className="login-card">
          <h1 className="login-title">Character Distill</h1>
          <p className="login-subtitle">重置密码</p>

          {forgotStep === 'done' ? (
            <div className="login-form">
              <div style={{ textAlign: 'center', padding: '24px 0', color: '#22c55e' }}>
                <p style={{ fontSize: 18, marginBottom: 12 }}>密码已重置</p>
                <p style={{ fontSize: 14, color: 'var(--text-secondary)' }}>请使用新密码重新登录</p>
              </div>
              <button className="login-submit" type="button" onClick={() => { setTab('login'); setForgotStep('email'); setError('') }}>
                返回登录
              </button>
            </div>
          ) : (
            <form className="login-form" onSubmit={(e) => { e.preventDefault(); handleForgotReset() }}>
              {forgotStep === 'email' && (
                <>
                  <div className="login-field">
                    <label htmlFor="forgot-email">注册时的邮箱</label>
                    <input
                      id="forgot-email"
                      type="email"
                      value={forgotEmail}
                      onChange={(e) => setForgotEmail(e.target.value)}
                      placeholder="请输入邮箱"
                      autoComplete="email"
                      autoFocus
                    />
                  </div>
                  {error && <div className="login-error">{error}</div>}
                  <button type="button" className="login-submit" onClick={handleForgotSendCode} disabled={loading}>
                    {loading ? '请稍候…' : '获取验证码'}
                  </button>
                </>
              )}

              {forgotStep === 'code' && (
                <>
                  <div className="login-field">
                    <label htmlFor="forgot-code">验证码</label>
                    <input
                      id="forgot-code"
                      type="text"
                      value={forgotCode}
                      onChange={(e) => setForgotCode(e.target.value)}
                      placeholder="请输入邮箱中的验证码"
                      autoComplete="off"
                      autoFocus
                    />
                  </div>
                  <div className="login-field">
                    <label htmlFor="forgot-newpw">新密码</label>
                    <input
                      id="forgot-newpw"
                      type="password"
                      value={forgotNewPw}
                      onChange={(e) => setForgotNewPw(e.target.value)}
                      placeholder="至少 8 位，含字母和数字"
                      autoComplete="new-password"
                    />
                  </div>
                  {error && <div className="login-error">{error}</div>}
                  <button type="submit" className="login-submit" disabled={loading || !forgotCode || !forgotNewPw}>
                    {loading ? '请稍候…' : '重置密码'}
                  </button>
                </>
              )}

              <div className="login-back-link">
                <button type="button" className="login-link-btn" onClick={() => { setTab('login'); setForgotStep('email'); setError('') }}>
                  返回登录
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <h1 className="login-title">Character Distill</h1>
        <p className="login-subtitle">角色蒸馏与沉浸式对话</p>

        <div className="login-tabs">
          <button
            className={`login-tab${tab === 'login' ? ' active' : ''}`}
            onClick={() => { setTab('login'); setError(''); setCodeSent(false); setCountdown(0) }}
          >
            登录
          </button>
          <button
            className={`login-tab${tab === 'register' ? ' active' : ''}`}
            onClick={() => { setTab('register'); setError(''); setCodeSent(false); setCountdown(0) }}
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

              {/* Email + verification code */}
              <div className="login-field">
                <label htmlFor="reg-email">邮箱</label>
                <input
                  id="reg-email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="用于找回密码"
                  autoComplete="email"
                />
              </div>
              <div className="login-field login-code-field">
                <input
                  type="text"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  placeholder="邮箱验证码"
                  autoComplete="off"
                />
                <button
                  type="button"
                  className="login-code-btn"
                  disabled={codeSent && countdown > 0}
                  onClick={() => sendCode(email, 'register')}
                >
                  {countdown > 0 ? `${countdown}s` : codeSent ? '重新发送' : '获取验证码'}
                </button>
              </div>

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

          {tab === 'login' && (
            <div className="login-back-link">
              <button type="button" className="login-link-btn" onClick={() => { setTab('forgot'); setError(''); setForgotStep('email') }}>
                忘记密码？
              </button>
            </div>
          )}
        </form>
      </div>
    </div>
  )
}
