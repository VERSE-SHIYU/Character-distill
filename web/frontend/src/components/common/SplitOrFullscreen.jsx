import { useCallback, useRef } from 'react'
import useIsMobile from '../../hooks/useIsMobile'

/**
 * Desktop: flex row with draggable splitter (matching current behavior).
 * Mobile: fullscreen toggle — main when closed, panel overlay when open.
 *
 * Props:
 *   open               boolean — show the panel?
 *   main               ReactNode — primary content
 *   panel              ReactNode — sidebar / history panel content
 *   splitRatio         number (0.4–0.8), default 0.65 — desktop main fraction
 *   onSplitRatioChange fn(ratio) — called when user drags splitter
 */
export default function SplitOrFullscreen({
  open,
  main,
  panel,
  splitRatio = 0.65,
  onSplitRatioChange,
  panelClassName = 'history-sidebar',
}) {
  const isMobile = useIsMobile()
  const containerRef = useRef(null)

  const onSplitterMouseDown = useCallback((e) => {
    e.preventDefault()
    const container = containerRef.current
    if (!container) return
    const rect = container.getBoundingClientRect()
    const onMove = (moveE) => {
      const ratio = (moveE.clientX - rect.left) / rect.width
      onSplitRatioChange?.(Math.min(0.8, Math.max(0.4, ratio)))
    }
    const onUp = () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [onSplitRatioChange])

  if (isMobile) {
    if (!open) return <>{main}</>
    return (
      <div style={{ position: 'absolute', inset: 0, zIndex: 10, background: 'var(--bg-page)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {panel}
      </div>
    )
  }

  return (
    <div ref={containerRef} style={{ display: 'flex', flex: 1, minHeight: 0, minWidth: 0 }}>
      <div style={open ? { flex: splitRatio, minWidth: 0, display: 'flex', flexDirection: 'column', minHeight: 0 } : { flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: 0 }}>
        {main}
      </div>
      {open && (
        <>
          <div className="chat-splitter" onMouseDown={onSplitterMouseDown} />
          <div className={panelClassName} style={{ flex: 1 - splitRatio, minWidth: 280, maxWidth: '50vw' }}>
            {panel}
          </div>
        </>
      )}
    </div>
  )
}
