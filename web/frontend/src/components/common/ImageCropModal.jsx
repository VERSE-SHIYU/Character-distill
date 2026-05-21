import { useCallback, useEffect, useRef, useState } from 'react'

const SIZE = 200
const RADIUS = SIZE / 2

export default function ImageCropModal({ file, onConfirm, onCancel }) {
  const [scale, setScale] = useState(1.0)
  const [ready, setReady] = useState(false)
  const canvasRef = useRef(null)
  const imageDataRef = useRef(null)
  const offsetRef = useRef({ x: 0, y: 0 })
  const scaleRef = useRef(1.0)
  const draggingRef = useRef(false)
  const lastPosRef = useRef({ x: 0, y: 0 })

  // Keep scaleRef in sync so mouse handlers always read the latest scale
  useEffect(() => { scaleRef.current = scale }, [scale])

  const clampOffset = (ox, oy, s) => {
    const maxOff = RADIUS * (s - 1)
    if (maxOff <= 0) return { x: 0, y: 0 }
    return {
      x: Math.max(-maxOff, Math.min(maxOff, ox)),
      y: Math.max(-maxOff, Math.min(maxOff, oy)),
    }
  }

  const drawPreview = useCallback((img, s, offX, offY) => {
    const canvas = canvasRef.current
    if (!canvas || !img) return
    const ctx = canvas.getContext('2d')
    const minDim = Math.min(img.naturalWidth, img.naturalHeight)
    const srcSize = minDim / s

    const cx = img.naturalWidth / 2 + offX * (srcSize / SIZE)
    const cy = img.naturalHeight / 2 + offY * (srcSize / SIZE)
    const sx = cx - srcSize / 2
    const sy = cy - srcSize / 2

    ctx.clearRect(0, 0, SIZE, SIZE)

    ctx.save()
    ctx.beginPath()
    ctx.arc(RADIUS, RADIUS, RADIUS, 0, Math.PI * 2)
    ctx.closePath()
    ctx.clip()

    ctx.drawImage(img, sx, sy, srcSize, srcSize, 0, 0, SIZE, SIZE)
    ctx.restore()

    ctx.beginPath()
    ctx.arc(RADIUS, RADIUS, RADIUS - 1, 0, Math.PI * 2)
    ctx.strokeStyle = 'rgba(255,255,255,0.6)'
    ctx.lineWidth = 2
    ctx.setLineDash([6, 4])
    ctx.stroke()
    ctx.setLineDash([])
  }, [])

  // Load image
  useEffect(() => {
    if (!file) return
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => {
      imageDataRef.current = img
      offsetRef.current = { x: 0, y: 0 }
      scaleRef.current = 1.0
      setScale(1.0)
      setReady(true)
      drawPreview(img, 1.0, 0, 0)
    }
    img.src = url
    return () => URL.revokeObjectURL(url)
  }, [file, drawPreview])

  // ---- Drag: attach window listeners once, read latest values from refs ----
  useEffect(() => {
    const onMouseDown = (e) => {
      // Only handle mousedown on the canvas
      if (e.target !== canvasRef.current) return
      e.preventDefault()
      draggingRef.current = true
      lastPosRef.current = { x: e.clientX, y: e.clientY }
    }

    const onMouseMove = (e) => {
      if (!draggingRef.current) return
      const img = imageDataRef.current
      if (!img) return

      const dx = e.clientX - lastPosRef.current.x
      const dy = e.clientY - lastPosRef.current.y
      lastPosRef.current = { x: e.clientX, y: e.clientY }

      const s = scaleRef.current
      const newX = offsetRef.current.x + dx
      const newY = offsetRef.current.y + dy
      const clamped = clampOffset(newX, newY, s)
      offsetRef.current = clamped
      drawPreview(img, s, clamped.x, clamped.y)
    }

    const onMouseUp = () => {
      draggingRef.current = false
    }

    window.addEventListener('mousedown', onMouseDown)
    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('mouseup', onMouseUp)
    return () => {
      window.removeEventListener('mousedown', onMouseDown)
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('mouseup', onMouseUp)
    }
  }, [drawPreview]) // drawPreview is stable ([])

  // Touch support — same pattern
  useEffect(() => {
    const onTouchStart = (e) => {
      if (e.target !== canvasRef.current || e.touches.length !== 1) return
      e.preventDefault()
      draggingRef.current = true
      lastPosRef.current = { x: e.touches[0].clientX, y: e.touches[0].clientY }
    }

    const onTouchMove = (e) => {
      if (!draggingRef.current || e.touches.length !== 1) return
      const img = imageDataRef.current
      if (!img) return

      const dx = e.touches[0].clientX - lastPosRef.current.x
      const dy = e.touches[0].clientY - lastPosRef.current.y
      lastPosRef.current = { x: e.touches[0].clientX, y: e.touches[0].clientY }

      const s = scaleRef.current
      const newX = offsetRef.current.x + dx
      const newY = offsetRef.current.y + dy
      const clamped = clampOffset(newX, newY, s)
      offsetRef.current = clamped
      drawPreview(img, s, clamped.x, clamped.y)
    }

    const onTouchEnd = () => {
      draggingRef.current = false
    }

    window.addEventListener('touchstart', onTouchStart, { passive: false })
    window.addEventListener('touchmove', onTouchMove, { passive: false })
    window.addEventListener('touchend', onTouchEnd)
    return () => {
      window.removeEventListener('touchstart', onTouchStart)
      window.removeEventListener('touchmove', onTouchMove)
      window.removeEventListener('touchend', onTouchEnd)
    }
  }, [drawPreview])

  // Scale change
  const handleScaleChange = useCallback((v) => {
    const s = v / 100
    scaleRef.current = s
    setScale(s)
    const clamped = clampOffset(offsetRef.current.x, offsetRef.current.y, s)
    offsetRef.current = clamped
    if (imageDataRef.current) {
      drawPreview(imageDataRef.current, s, clamped.x, clamped.y)
    }
  }, [drawPreview])

  const handleReset = useCallback(() => {
    scaleRef.current = 1.0
    setScale(1.0)
    offsetRef.current = { x: 0, y: 0 }
    if (imageDataRef.current) {
      drawPreview(imageDataRef.current, 1.0, 0, 0)
    }
  }, [drawPreview])

  // Export
  const handleConfirm = useCallback(() => {
    const img = imageDataRef.current
    if (!img) return

    const s = scaleRef.current
    const minDim = Math.min(img.naturalWidth, img.naturalHeight)
    const srcSize = minDim / s
    const { x: ox, y: oy } = offsetRef.current

    const cx = img.naturalWidth / 2 + ox * (srcSize / SIZE)
    const cy = img.naturalHeight / 2 + oy * (srcSize / SIZE)
    const sx = cx - srcSize / 2
    const sy = cy - srcSize / 2

    const exportCanvas = document.createElement('canvas')
    exportCanvas.width = SIZE
    exportCanvas.height = SIZE
    const ctx = exportCanvas.getContext('2d')

    ctx.beginPath()
    ctx.arc(RADIUS, RADIUS, RADIUS, 0, Math.PI * 2)
    ctx.closePath()
    ctx.clip()
    ctx.drawImage(img, sx, sy, srcSize, srcSize, 0, 0, SIZE, SIZE)

    const base64 = exportCanvas.toDataURL('image/jpeg', 0.85)
    onConfirm(base64)
  }, [onConfirm])

  if (!file) return null

  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal-card crop-modal-card" onClick={(e) => e.stopPropagation()}>
        <h2 className="modal-title">调整头像</h2>

        <div className="crop-preview-wrap">
          {!ready && <div className="crop-loading">加载中…</div>}
          <canvas
            ref={canvasRef}
            width={SIZE}
            height={SIZE}
            className="crop-canvas"
            style={{
              display: ready ? 'block' : 'none',
              cursor: scale > 1 ? 'grab' : 'default',
            }}
          />
        </div>

        <p className="crop-drag-hint">
          {scale > 1 ? '拖拽图片调整位置 · 滑块控制缩放' : '拖动下方滑块调整缩放'}
        </p>

        <div className="crop-controls">
          <label className="crop-slider-label">
            缩放
            <input
              type="range"
              className="crop-slider"
              min={50}
              max={150}
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
