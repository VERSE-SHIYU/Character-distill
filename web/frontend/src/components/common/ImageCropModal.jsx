import { useCallback, useEffect, useRef, useState } from 'react'

const DISPLAY_SIZE = 260
const RADIUS = DISPLAY_SIZE / 2

export default function ImageCropModal({ file, onConfirm, onCancel }) {
  const [ready, setReady] = useState(false)
  const [scale, setScale] = useState(1.0)
  const [dragging, setDragging] = useState(false)
  const canvasRef = useRef(null)
  const imgRef = useRef(null)
  const offsetRef = useRef({ x: 0, y: 0 })
  const scaleRef = useRef(1.0)
  const draggingRef = useRef(false)
  const lastPosRef = useRef({ x: 0, y: 0 })
  const containerRef = useRef(null)

  // Sync refs for event handlers
  useEffect(() => { scaleRef.current = scale }, [scale])

  /** Clamp offset so the crop circle never sees empty canvas edge.
   *  At any scale >= 1, the visible region of the image is DISPLAY_SIZE / scale
   *  in source-image pixels. The offset (in display-px) maps to
   *  source-px displacement of (offset * (imgSize / DISPLAY_SIZE)).
   *  The circle has radius RADIUS in display px = imgSize/2 in source px.
   *  Constraint: -RADIUS*(s-1)/s <= offset <= RADIUS*(s-1)/s
   *  Simplified for s=1: 0; for s>1: max displacement so circle stays inside.
   */
  const clampOffset = (ox, oy, s) => {
    const limit = RADIUS * (s - 1) / s
    if (limit <= 0) return { x: 0, y: 0 }
    return {
      x: Math.max(-limit, Math.min(limit, ox)),
      y: Math.max(-limit, Math.min(limit, oy)),
    }
  }

  const draw = useCallback((img, s, offX, offY) => {
    const canvas = canvasRef.current
    if (!canvas || !img) return
    const ctx = canvas.getContext('2d')
    const iw = img.naturalWidth
    const ih = img.naturalHeight
    const minDim = Math.min(iw, ih)

    // Source square region to display
    const srcSize = minDim / s
    // The center of the crop in source coords
    const srcCx = iw / 2 + offX * (minDim / DISPLAY_SIZE) / s
    const srcCy = ih / 2 + offY * (minDim / DISPLAY_SIZE) / s

    ctx.clearRect(0, 0, DISPLAY_SIZE, DISPLAY_SIZE)

    // Clip to circle
    ctx.save()
    ctx.beginPath()
    ctx.arc(RADIUS, RADIUS, RADIUS, 0, Math.PI * 2)
    ctx.closePath()
    ctx.clip()

    // Draw the source rectangle into the full canvas
    ctx.drawImage(
      img,
      srcCx - srcSize / 2, srcCy - srcSize / 2,
      srcSize, srcSize,
      0, 0,
      DISPLAY_SIZE, DISPLAY_SIZE,
    )
    ctx.restore()

    // Circle border
    ctx.beginPath()
    ctx.arc(RADIUS, RADIUS, RADIUS - 1, 0, Math.PI * 2)
    ctx.strokeStyle = 'rgba(255,255,255,0.7)'
    ctx.lineWidth = 2.5
    ctx.setLineDash([6, 5])
    ctx.stroke()
    ctx.setLineDash([])
  }, [])

  // Load image
  useEffect(() => {
    if (!file) return
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => {
      imgRef.current = img
      offsetRef.current = { x: 0, y: 0 }
      scaleRef.current = 1.0
      setScale(1.0)
      setReady(true)
      draw(img, 1.0, 0, 0)
    }
    img.src = url
    return () => URL.revokeObjectURL(url)
  }, [file, draw])

  // --- Mouse wheel zoom ---
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const onWheel = (e) => {
      e.preventDefault()
      const img = imgRef.current
      if (!img) return
      const delta = e.deltaY > 0 ? -0.1 : 0.1
      const newScale = Math.max(1.0, Math.min(3.0, scaleRef.current + delta))
      scaleRef.current = newScale
      setScale(newScale)
      const clamped = clampOffset(offsetRef.current.x, offsetRef.current.y, newScale)
      offsetRef.current = clamped
      draw(img, newScale, clamped.x, clamped.y)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [draw])

  // --- Mouse drag ---
  useEffect(() => {
    const onMouseDown = (e) => {
      if (e.target !== canvasRef.current) return
      e.preventDefault()
      draggingRef.current = true
      setDragging(true)
      lastPosRef.current = { x: e.clientX, y: e.clientY }
    }
    const onMouseMove = (e) => {
      if (!draggingRef.current) return
      const img = imgRef.current
      if (!img) return
      const dx = e.clientX - lastPosRef.current.x
      const dy = e.clientY - lastPosRef.current.y
      lastPosRef.current = { x: e.clientX, y: e.clientY }
      const s = scaleRef.current
      const clamped = clampOffset(offsetRef.current.x + dx, offsetRef.current.y + dy, s)
      offsetRef.current = clamped
      draw(img, s, clamped.x, clamped.y)
    }
    const onMouseUp = () => { draggingRef.current = false; setDragging(false) }

    window.addEventListener('mousedown', onMouseDown)
    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('mouseup', onMouseUp)
    return () => {
      window.removeEventListener('mousedown', onMouseDown)
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('mouseup', onMouseUp)
    }
  }, [draw])

  // --- Touch drag ---
  useEffect(() => {
    const onTouchStart = (e) => {
      if (e.target !== canvasRef.current || e.touches.length !== 1) return
      e.preventDefault()
      draggingRef.current = true
      lastPosRef.current = { x: e.touches[0].clientX, y: e.touches[0].clientY }
    }
    const onTouchMove = (e) => {
      if (!draggingRef.current || e.touches.length !== 1) return
      const img = imgRef.current
      if (!img) return
      const dx = e.touches[0].clientX - lastPosRef.current.x
      const dy = e.touches[0].clientY - lastPosRef.current.y
      lastPosRef.current = { x: e.touches[0].clientX, y: e.touches[0].clientY }
      const s = scaleRef.current
      const clamped = clampOffset(offsetRef.current.x + dx, offsetRef.current.y + dy, s)
      offsetRef.current = clamped
      draw(img, s, clamped.x, clamped.y)
    }
    const onTouchEnd = () => { draggingRef.current = false; setDragging(false) }

    window.addEventListener('touchstart', onTouchStart, { passive: false })
    window.addEventListener('touchmove', onTouchMove, { passive: false })
    window.addEventListener('touchend', onTouchEnd)
    return () => {
      window.removeEventListener('touchstart', onTouchStart)
      window.removeEventListener('touchmove', onTouchMove)
      window.removeEventListener('touchend', onTouchEnd)
    }
  }, [draw])

  // Slider zoom
  const handleScaleChange = useCallback((v) => {
    const s = v / 100
    scaleRef.current = s
    setScale(s)
    const clamped = clampOffset(offsetRef.current.x, offsetRef.current.y, s)
    offsetRef.current = clamped
    if (imgRef.current) draw(imgRef.current, s, clamped.x, clamped.y)
  }, [draw])

  const handleReset = useCallback(() => {
    scaleRef.current = 1.0
    setScale(1.0)
    offsetRef.current = { x: 0, y: 0 }
    if (imgRef.current) draw(imgRef.current, 1.0, 0, 0)
  }, [draw])

  // Export square base64 (200×200, full image — CSS handles circular clip)
  const handleConfirm = useCallback(() => {
    const img = imgRef.current
    if (!img) return
    const s = scaleRef.current
    const minDim = Math.min(img.naturalWidth, img.naturalHeight)
    const srcSize = minDim / s
    const { x: ox, y: oy } = offsetRef.current
    const srcCx = img.naturalWidth / 2 + ox * (minDim / DISPLAY_SIZE) / s
    const srcCy = img.naturalHeight / 2 + oy * (minDim / DISPLAY_SIZE) / s

    const EXPORT_SIZE = 200
    const ec = document.createElement('canvas')
    ec.width = EXPORT_SIZE
    ec.height = EXPORT_SIZE
    const ctx = ec.getContext('2d')
    ctx.fillStyle = '#fff'
    ctx.fillRect(0, 0, EXPORT_SIZE, EXPORT_SIZE)
    ctx.drawImage(img, srcCx - srcSize / 2, srcCy - srcSize / 2, srcSize, srcSize, 0, 0, EXPORT_SIZE, EXPORT_SIZE)

    onConfirm(ec.toDataURL('image/jpeg', 0.85))
  }, [onConfirm])

  if (!file) return null

  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal-card crop-modal-card" onClick={(e) => e.stopPropagation()}>
        <h2 className="modal-title">调整头像</h2>

        <div className="crop-preview-wrap" ref={containerRef}>
          {!ready && <div className="crop-loading">加载中…</div>}
          <canvas
            ref={canvasRef}
            width={DISPLAY_SIZE}
            height={DISPLAY_SIZE}
            className="crop-canvas"
            style={{
              display: ready ? 'block' : 'none',
              cursor: dragging ? 'grabbing' : scale > 1 ? 'grab' : 'default',
            }}
          />
        </div>

        <p className="crop-drag-hint">
          滚轮缩放 · 拖拽调整位置
        </p>

        <div className="crop-controls">
          <label className="crop-slider-label">
            缩放
            <input
              type="range"
              className="crop-slider"
              min={100}
              max={300}
              value={Math.round(scale * 100)}
              onChange={(e) => handleScaleChange(Number(e.target.value))}
            />
            <span className="crop-scale-value">{Math.round(scale * 100)}%</span>
          </label>
          <button type="button" className="crop-reset-btn" onClick={handleReset}>
            重置
          </button>
        </div>

        <div className="crop-actions">
          <button type="button" className="btn-ghost" onClick={onCancel}>
            取消
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={handleConfirm}
            disabled={!ready}
          >
            确认
          </button>
        </div>
      </div>
    </div>
  )
}
