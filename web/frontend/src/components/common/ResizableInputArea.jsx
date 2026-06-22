import { createContext, useCallback, useEffect, useRef, useState } from 'react'

export const MIN_H = 38
export const AUTO_MAX = 160
export const HARD_MAX = 400
export const InputHeightContext = createContext(null)

export default function ResizableInputArea({ children }) {
  const [manualH, setManualH] = useState(null)
  const containerRef = useRef(null)
  const dragRef = useRef({ active: false, startY: 0, startH: 0 })

  const getTextarea = useCallback(() => {
    return containerRef.current?.querySelector('textarea') || null
  }, [])

  const applyHeight = useCallback((h) => {
    const ta = getTextarea()
    if (!ta) return
    const clamped = Math.max(MIN_H, Math.min(HARD_MAX, h))
    ta.style.height = clamped + 'px'
    ta.style.overflowY = ta.scrollHeight > clamped ? 'auto' : 'hidden'
  }, [getTextarea])

  const onMouseDown = useCallback((e) => {
    const ta = getTextarea()
    if (!ta) return
    dragRef.current = { active: true, startY: e.clientY, startH: ta.offsetHeight }
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'ns-resize'
    e.preventDefault()
  }, [getTextarea])

  useEffect(() => {
    const onMove = (e) => {
      const d = dragRef.current
      if (!d.active) return
      const next = d.startH + (d.startY - e.clientY)
      const clamped = Math.max(MIN_H, Math.min(HARD_MAX, next))
      setManualH(clamped)
      applyHeight(clamped)
    }
    const onUp = () => {
      if (!dragRef.current.active) return
      dragRef.current.active = false
      document.body.style.userSelect = ''
      document.body.style.cursor = ''
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    return () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
  }, [applyHeight])

  return (
    <InputHeightContext.Provider value={manualH}>
      <div ref={containerRef} className="resize-input-area">
        <div className="resize-handle" onMouseDown={onMouseDown} />
        {children}
      </div>
    </InputHeightContext.Provider>
  )
}
