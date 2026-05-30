import { createPortal } from 'react-dom'

export default function ConfirmModal({ isOpen, title, message, confirmText = '确认', cancelText = '取消', onConfirm, onCancel, danger = false, disabled = false }) {
  if (!isOpen) return null

  return createPortal(
    <div className="modal-overlay" onClick={disabled ? undefined : onCancel}>
      <div className="modal-card" style={{ maxWidth: 380 }} onClick={(e) => e.stopPropagation()}>
        <h3 className="modal-title">{title}</h3>
        <div className="modal-body">
          <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6 }}>{message}</p>
        </div>
        <div className="modal-actions">
          <button className="btn-ghost" onClick={onCancel} disabled={disabled}>{cancelText}</button>
          <button className={danger ? 'btn-danger' : 'btn-primary'} onClick={onConfirm} disabled={disabled}>
            {confirmText}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
