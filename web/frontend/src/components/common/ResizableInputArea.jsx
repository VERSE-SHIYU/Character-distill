import { createContext, useCallback, useEffect, useRef, useState } from 'react'

export const MIN_H = 38
export const HARD_MAX = 400
export const InputHeightContext = createContext(null)

export default function ResizableInputArea({ children }) {
  const [manualH, setManualH] = useState(null)
  const containerRef = useRef(null)
  const dragRef = useRef({ active: false, startY: 0, startH: 0 })

  const onMouseDown = useCallback((e) => {
    const el = containerRef.current
    if (!el) return
    dragRef.current = { active: true, startY: e.clientY, startH: el.offsetHeight }
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'ns-resize'
    e.preventDefault()
  }, [])

  useEffect(() => {
    const onMove = (e) => {
      const d = dragRef.current
      if (!d.active) return
      setManualH(Math.max(MIN_H, Math.min(HARD_MAX, d.startH + (d.startY - e.clientY))))
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
  }, [])

  return (
    <InputHeightContext.Provider value={manualH}>
      <div
        ref={containerRef}
        className="resize-input-area"
        style={manualH ? { height: manualH } : undefined}
      >
        <div className="resize-handle" onMouseDown={onMouseDown} />
        {children}
      </div>
    </InputHeightContext.Provider>
  )
}
