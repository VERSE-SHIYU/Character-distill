import { useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'

export default function RoleSetupModal({ isOpen, characterName, onConfirm, onSkip }) {
  const userRole = useAppStore((s) => s.userRole)
  const setUserRole = useAppStore((s) => s.setUserRole)
  const [role, setRole] = useState(userRole || '')
  const inputRef = useRef(null)

  useEffect(() => {
    if (isOpen) {
      setRole(userRole || '')
      setTimeout(() => inputRef.current?.focus(), 100)
    }
  }, [isOpen, userRole])

  if (!isOpen) return null

  const handleConfirm = () => {
    setUserRole(role.trim())
    onConfirm(role.trim())
  }

  const handleSkip = () => {
    setUserRole('')
    onSkip()
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      handleConfirm()
    }
  }

  return (
    <div className="modal-overlay" onClick={handleSkip}>
      <div className="modal-card role-setup-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">你要扮演谁？</div>

        {characterName && (
          <p className="role-setup-hint">
            你即将与 <strong>{characterName}</strong> 对话。设定你在故事中的身份，让对话更加沉浸。
          </p>
        )}

        <div className="modal-field">
          <label className="modal-label" htmlFor="role-setup-input">
            你的角色名
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

        <div className="modal-actions">
          <button
            type="button"
            className="btn-secondary glass"
            onClick={handleSkip}
          >
            跳过
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={handleConfirm}
          >
            确认并开始对话
          </button>
        </div>
      </div>
    </div>
  )
}
