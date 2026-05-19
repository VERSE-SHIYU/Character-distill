import { useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'

export default function RoleSetupModal({ isOpen, characterName, relationships, onConfirm, onSkip }) {
  const userRole = useAppStore((s) => s.userRole)
  const setUserRole = useAppStore((s) => s.setUserRole)
  const [role, setRole] = useState(userRole || '')
  const [step, setStep] = useState('input') // 'input' | 'confirm'
  const inputRef = useRef(null)

  useEffect(() => {
    if (isOpen) {
      setRole(userRole || '')
      setStep('input')
      setTimeout(() => inputRef.current?.focus(), 100)
    }
  }, [isOpen, userRole])

  if (!isOpen) return null

  const targets = (relationships || [])
    .map((r) => r.target)
    .filter(Boolean)

  const trimmed = role.trim()

  const handleFirstConfirm = () => {
    if (!trimmed) return
    setUserRole(trimmed)
    setStep('confirm')
  }

  const handleEnterChat = () => {
    onConfirm(trimmed)
  }

  const handleBack = () => {
    setStep('input')
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && trimmed) {
      e.preventDefault()
      handleFirstConfirm()
    }
  }

  // Step 2: confirmation
  if (step === 'confirm') {
    return (
      <div className="modal-overlay" onClick={handleBack}>
        <div className="modal-card role-setup-card" onClick={(e) => e.stopPropagation()}>
          <div className="modal-title">确认身份</div>
          <p className="role-confirm-text">
            你将以 <strong>「{trimmed}」</strong> 的身份与 <strong>{characterName}</strong> 对话
          </p>
          <div className="modal-actions">
            <button type="button" className="btn-secondary glass" onClick={handleBack}>
              ← 重新选择
            </button>
            <button type="button" className="btn-primary" onClick={handleEnterChat}>
              进入对话
            </button>
          </div>
        </div>
      </div>
    )
  }

  // Step 1: input
  return (
    <div className="modal-overlay" onClick={() => onSkip ? onSkip() : null}>
      <div className="modal-card role-setup-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">你要扮演谁？</div>

        {characterName && (
          <p className="role-setup-hint">
            你即将与 <strong>{characterName}</strong> 对话。设定你在故事中的身份，让对话更加沉浸。
          </p>
        )}

        <div className="modal-field">
          <label className="modal-label" htmlFor="role-setup-input">
            你的角色名 <span className="modal-label-required">（必填）</span>
          </label>
          <input
            ref={inputRef}
            id="role-setup-input"
            type="text"
            className="modal-input glass-input"
            placeholder="输入你的角色名，如：魏无羡"
            value={role}
            onChange={(e) => setRole(e.target.value)}
            onKeyDown={handleKeyDown}
          />
        </div>

        {targets.length > 0 && (
          <div className="modal-field">
            <label className="modal-label">{characterName} 认识的人（点击选择）</label>
            <div className="user-role-presets">
              {targets.map((t) => (
                <button
                  key={t}
                  type="button"
                  className={`user-role-preset-btn${role === t ? ' active' : ''}`}
                  onClick={() => setRole(t)}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="modal-actions">
          <button
            type="button"
            className="btn-primary"
            onClick={handleFirstConfirm}
            disabled={!trimmed}
          >
            确认并开始对话
          </button>
        </div>
      </div>
    </div>
  )
}
