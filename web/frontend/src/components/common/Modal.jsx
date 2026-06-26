import { createPortal } from 'react-dom'

export default function Modal({ isOpen, onClose, maxWidth = 420, closeOnOverlay = true, children }) {
  if (!isOpen) return null
  return createPortal(
    <div className="modal-overlay" onClick={closeOnOverlay ? onClose : undefined}>
      <div className="modal-card" style={{ maxWidth }} onClick={(e) => e.stopPropagation()}>
        {children}
      </div>
    </div>,
    document.body,
  )
}
