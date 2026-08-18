import { useState, useMemo, useRef, useCallback } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout } from '../api/client'
import { Check, EyeOff, Eye, Sun, Star, MessageCircle } from './common/Icon'

function PasswordInput({ id, value, onChange, placeholder, autoComplete, name, autoFocus }) {
  const [visible, setVisible] = useState(false)
  return (
    <div className="login-pw-wrap">
      <input
        id={id}
        name={name}
        type={visible ? 'text' : 'password'}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        autoComplete={autoComplete}
        autoFocus={autoFocus}
      />
      <button
        type="button"
        className="login-pw-toggle"
        onClick={() => setVisible((v) => !v)}
        aria-label={visible ? '隐藏密码' : '显示密码'}
        tabIndex={-1}
      >
        {visible ? (
          <EyeOff size={20} />
        ) : (
          <Eye size={20} />
        )}
      </button>
    </div>
  )
}

// 剧光登录 hero：舞台光 + 品牌（角色蒸馏）+ 诚实功能点（桌面双栏才展示功能列表）
function LoginHero({ subtitle }) {
  return (
    <div className="login-hero">
      <span className="stage-glow" />
      <h1 className="login-brand">角色<em>蒸馏</em></h1>
      {subtitle ? (
        <p className="login-subtitle">{subtitle}</p>
      ) : (
        <p className="login-tagline">与角色 · 隔幕对谈</p>
      )}
      <div className="login-features">
        <div className="dl-feature">
          <Sun size={18} />
          六套主题 · 亮暗自由切换
        </div>
        <div className="dl-feature">
          <Star size={18} />
          蒸馏你的专属角色
        </div>
        <div className="dl-feature">
          <MessageCircle size={18} />
          群聊 · 私信 · 沉浸式对话
        </div>
      </div>
    </div>
  )
}

function passwordStrength(pw) {
  if (!pw) return { level: 0, label: '', color: '' }
  const hasLetter = /[a-zA-Z]/.test(pw)
  const hasDigit = /[0-9]/.test(pw)
  const longEnough = pw.length >= 8
  if (longEnough && hasLetter && hasDigit) return { level: 3, label: '强', color: 'var(--success)' }
  if (pw.length >= 6 && hasLetter && hasDigit) return { level: 2, label: '中', color: 'var(--warning)' }
  return { level: 1, label: '弱', color: 'var(--danger)' }
}

export default function LoginPage() {
  const [tab, setTab] = useState('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [inviteCode, setInviteCode] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  // Email verification
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [codeSent, setCodeSent] = useState(false)
  const [countdown, setCountdown] = useState(0)

  // Legal consent
  const legalTab = useAppStore((s) => s.legalTab)
  const setLegalTab = useAppStore((s) => s.setLegalTab)
  const [agreed, setAgreed] = useState(false)

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
  // Returns true only on a confirmed send; the code-sent UI (countdown lock)
  // is applied after the request succeeds, not before.
  const sendCode = useCallback(async (targetEmail, purpose) => {
    setError('')
    if (!targetEmail || !targetEmail.includes('@')) {
      setError('请输入有效的邮箱地址')
      return false
    }
    try {
      await fetchWithTimeout('/api/auth/send-code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: targetEmail, purpose }),
      })
      setCodeSent(true)
      setCountdown(60)
      const timer = setInterval(() => {
        setCountdown((c) => {
          if (c <= 1) { clearInterval(timer); return 0 }
          return c - 1
        })
      }, 1000)
      return true
    } catch (err) {
      setError(err.message || '发送验证码失败')
      return false
    }
  }, [])

  // ---- Forgot password ----
  const handleForgotSendCode = useCallback(async () => {
    const ok = await sendCode(forgotEmail, 'reset_password')
    if (ok) setForgotStep('code')
  }, [forgotEmail, sendCode])

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
    if (tab === 'register' && !/^[a-zA-Z0-9_]{2,20}$/.test(username.trim())) {
      setError('用户名只能含英文、数字、下划线，2–20 位')
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
    if (tab === 'register' && password !== confirmPassword) {
      setError('两次输入的密码不一致')
      return
    }
    setLoading(true)
    try {
      if (tab === 'login') {
        await login(username.trim(), password.trim())
      } else {
        await register(username.trim(), password, inviteCode.trim(), email.trim(), code.trim(), agreed)
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
        <LoginHero subtitle="重置密码" />
        <div className="login-card">

          {forgotStep === 'done' ? (
            <div className="login-form">
              <div style={{ textAlign: 'center', padding: '24px 0', color: 'var(--success)' }}>
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
                    <PasswordInput
                      id="forgot-newpw"
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
      <LoginHero />
      <div className="login-card">

        <div className="login-tabs">
          <button
            className={`login-tab${tab === 'login' ? ' active' : ''}`}
            onClick={() => { setTab('login'); setError(''); setCodeSent(false); setCountdown(0); setConfirmPassword('') }}
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
              placeholder={tab === 'register' ? '英文 / 数字 / 下划线，2–20 位' : '请输入用户名'}
              autoComplete={tab === 'login' ? 'username' : 'off'}
              autoFocus
            />
            {tab === 'register' && (
              <span className="login-field-hint">英文 / 数字 / 下划线，2–20 位，不区分大小写（Tracy 与 tracy 视为同一账号）；昵称可在「我的-昵称」中随时设置</span>
            )}
          </div>
          <div className="login-field">
            <label htmlFor={tab === 'register' ? `reg-pass-${nameSuffix.current}` : 'login-password'}>密码</label>
            <PasswordInput
              id={tab === 'register' ? `reg-pass-${nameSuffix.current}` : 'login-password'}
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

              {/* Confirm password */}
              <div className="login-field">
                <label htmlFor="reg-confirm-pass">确认密码</label>
                <PasswordInput
                  id="reg-confirm-pass"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder="请再次输入密码"
                  autoComplete="new-password"
                />
              </div>
              {confirmPassword !== '' && (
                <div style={{ fontSize: 13, margin: '4px 0 10px', color: password === confirmPassword ? 'var(--success)' : 'var(--danger)' }}>
                  {password === confirmPassword ? <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><Check size={13} />密码一致</span> : '两次密码不一致'}
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
              <div className="login-field">
                <div className="login-code-wrap">
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

              {/* Legal consent checkbox */}
              <div className="legal-consent">
                <label className="legal-consent-label">
                  <input
                    type="checkbox"
                    checked={agreed}
                    onChange={(e) => setAgreed(e.target.checked)}
                  />
                  <span>
                    我已年满 18 周岁，并已阅读并同意
                    <button
                      type="button"
                      className="legal-link-btn"
                      onClick={(e) => { e.preventDefault(); setLegalTab('terms'); useAppStore.getState().navigateTo('legal') }}
                    >《用户协议》</button>
                    <button
                      type="button"
                      className="legal-link-btn"
                      onClick={(e) => { e.preventDefault(); setLegalTab('privacy'); useAppStore.getState().navigateTo('legal') }}
                    >《隐私政策》</button>
                  </span>
                </label>
              </div>
            </>
          )}

          {error && <div className="login-error">{error}</div>}

          <button className="login-submit" type="submit" disabled={loading || (tab === 'register' && (strength.level < 3 || !agreed || (confirmPassword !== '' && password !== confirmPassword)))}>
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
