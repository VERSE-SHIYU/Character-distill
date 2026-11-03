import { useRef } from 'react'
import useIsMobile from '../../hooks/useIsMobile'
import useSplitDrag from '../../hooks/useSplitDrag'
import useStoredRatio from '../../hooks/useStoredRatio'

/**
 * Desktop: flex row with draggable splitter (matching current behavior).
 * Mobile: fullscreen toggle — main when closed, panel overlay when open.
 *
 * Props:
 *   open               boolean — show the panel?
 *   main               ReactNode — primary content
 *   panel              ReactNode — sidebar / history panel content
 *   defaultRatio       number (0.4–0.8), default 0.65 — desktop main fraction before any drag
 *   storageKey         string — where the dragged ratio is remembered (localStorage)
 *
 * The ratio is owned here (useStoredRatio), not by callers: all three callers used to pass
 * `onSplitRatioChange={() => {}}`, so the splitter could never move.
 * Trigger chain: drag splitter → useSplitDrag → setSplitRatio (persisted) → main / panel flex re-layout.
 */
export default function SplitOrFullscreen({
  open,
  main,
  panel,
  defaultRatio = 0.65,
  storageKey,
  panelClassName = 'history-sidebar',
}) {
  const isMobile = useIsMobile()
  const containerRef = useRef(null)
  const [splitRatio, setSplitRatio] = useStoredRatio(storageKey, defaultRatio, 0.4, 0.8)

  const onSplitterPointerDown = useSplitDrag(containerRef, {
    axis: 'x', min: 0.4, max: 0.8, onChange: setSplitRatio,
  })

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
          <div className="chat-splitter" onPointerDown={onSplitterPointerDown} />
          <div className={panelClassName} style={{ flex: 1 - splitRatio, minWidth: 280, maxWidth: '50vw' }}>
            {panel}
          </div>
        </>
      )}
    </div>
  )
}
