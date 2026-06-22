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
    // 基准取 textarea 当前实际高度，而非容器高度（容器含 handle+padding，会错位）
    const ta = el.querySelector('textarea')
    const startH = ta ? ta.offsetHeight : el.offsetHeight
    dragRef.current = { active: true, startY: e.clientY, startH }
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'ns-resize'
    e.preventDefault()
  }, [])

  useEffect(() => {
    const onMove = (e) => {
      const d = dragRef.current
      if (!d.active) return
      // 向上拖（startY - clientY 为正）增高
      const next = Math.max(MIN_H, Math.min(HARD_MAX, d.startH + (d.startY - e.clientY)))
      setManualH(next)
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
      <div ref={containerRef} className="resize-input-area">
        <div className="resize-handle" onMouseDown={onMouseDown} />
        {children}
      </div>
    </InputHeightContext.Provider>
  )
}
