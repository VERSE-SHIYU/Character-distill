import { useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'

export default function RoleSetupModal({ isOpen, characterName, characterId, relationships, textType, arcPhases, onConfirm, onSkip }) {
  const getUserRole = useAppStore((s) => s.getUserRole)
  const setUserRole = useAppStore((s) => s.setUserRole)
  const setSessionUserRole = useAppStore((s) => s.setSessionUserRole)
  const getArcPhase = useAppStore((s) => s.getArcPhase)
  const setArcPhase = useAppStore((s) => s.setArcPhase)
  const [role, setRole] = useState(() => characterId ? getUserRole(characterId) : '')
  const [phase, setPhase] = useState(() => (characterId ? getArcPhase(characterId) : null))
  const [step, setStep] = useState('input') // 'input' | 'confirm'
  const inputRef = useRef(null)

  useEffect(() => {
    if (isOpen) {
      setRole(characterId ? getUserRole(characterId) : '')
      setPhase(characterId ? getArcPhase(characterId) : null)
      setStep('input')
      setTimeout(() => inputRef.current?.focus(), 100)
    }
  }, [isOpen, characterId, getUserRole, getArcPhase])

  if (!isOpen) return null

  const targets = (relationships || [])
    .map((r) => r.target)
    .filter(Boolean)

  const trimmed = role.trim()
  // 阶段单选：每张卡记住上次选择（getArcPhase），没选过 → 最后阶段。
  const phaseCount = (arcPhases || []).length
  const selectedPhase = phase ?? phaseCount
  const choosePhase = (k) => {
    setPhase(k)
    if (characterId) setArcPhase(characterId, k)
  }

  const isSelfIdentity = trimmed && characterName && trimmed === characterName
    && !targets.includes(trimmed)

  const handleFirstConfirm = () => {
    if (!trimmed || isSelfIdentity) return
    setUserRole(characterId, trimmed)
    setSessionUserRole(trimmed)  // 同步更新会话身份（startChat 会再覆盖一次，但前置确保不遗漏）
    setStep('confirm')
  }

  const handleEnterChat = () => {
    onConfirm(trimmed)
  }

  const handleBack = () => {
    setStep('input')
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && trimmed && !isSelfIdentity) {
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
            {phaseCount > 0 && `（阶段 ${selectedPhase}/${phaseCount}${arcPhases[selectedPhase - 1]?.label ? ` · ${arcPhases[selectedPhase - 1].label}` : ''}）`}
          </p>
          <div className="modal-actions">
            <button type="button" className="btn-secondary" onClick={handleBack}>
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

  const isChatType = textType === 'chat'

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
          {isChatType ? (
            <p className="role-setup-hint">聊天记录模式：请从下方选择你在对话中的身份</p>
          ) : (
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
          )}
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

        {phaseCount > 0 && (
          <fieldset className="modal-field role-phase-select">
            <legend className="modal-label">这次聊的是哪个时期的他？</legend>
            {arcPhases.map((p, i) => {
              const k = i + 1
              return (
                <label key={k} className="role-phase-option">
                  <input
                    type="radio"
                    name="role-phase"
                    value={k}
                    checked={selectedPhase === k}
                    onChange={() => choosePhase(k)}
                  />
                  <span className="role-phase-text">
                    阶段 {k}{p.label ? ` · ${p.label}` : ''}
                    {p.state && <span className="role-phase-state">{p.state}</span>}
                  </span>
                </label>
              )
            })}
          </fieldset>
        )}

        <div className="modal-actions">
          {isSelfIdentity && (
            <p className="role-setup-warning">不能以角色自己的身份对话</p>
          )}
          <button
            type="button"
            className="btn-primary"
            onClick={handleFirstConfirm}
            disabled={!trimmed || isSelfIdentity}
          >
            确认并开始对话
          </button>
        </div>
      </div>
    </div>
  )
}
