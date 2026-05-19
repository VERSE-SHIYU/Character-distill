import { useEffect, useRef, useState, useCallback } from 'react'

const SIZE = 300
const RADIUS = SIZE / 2

export default function ImageCropModal({ file, onConfirm, onCancel }) {
  const [scale, setScale] = useState(1.0)
  const [ready, setReady] = useState(false)
  const canvasRef = useRef(null)
  const imageDataRef = useRef(null)
  const offsetRef = useRef({ x: 0, y: 0 })
  const draggingRef = useRef(false)
  const lastPosRef = useRef({ x: 0, y: 0 })

  const clampOffset = useCallback((ox, oy, s) => {
    const maxOff = RADIUS * (s - 1)
    if (maxOff <= 0) return { x: 0, y: 0 }
    return {
      x: Math.max(-maxOff, Math.min(maxOff, ox)),
      y: Math.max(-maxOff, Math.min(maxOff, oy)),
    }
  }, [])

  const drawPreview = useCallback((img, s, offX, offY) => {
    const canvas = canvasRef.current
    if (!canvas || !img) return
    const ctx = canvas.getContext('2d')
    const minDim = Math.min(img.naturalWidth, img.naturalHeight)
    const srcSize = minDim / s

    // Source center, offset applied
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

    // Dashed border ring
    ctx.beginPath()
    ctx.arc(RADIUS, RADIUS, RADIUS - 1, 0, Math.PI * 2)
    ctx.strokeStyle = 'rgba(255,255,255,0.6)'
    ctx.lineWidth = 2
    ctx.setLineDash([6, 4])
    ctx.stroke()
    ctx.setLineDash([])
  }, [])

  const redraw = useCallback(() => {
    const img = imageDataRef.current
    if (!img) return
    const { x, y } = offsetRef.current
    drawPreview(img, scale, x, y)
  }, [scale, drawPreview])

  // Load image
  useEffect(() => {
    if (!file) return
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => {
      imageDataRef.current = img
      offsetRef.current = { x: 0, y: 0 }
      setScale(1.0)
      setReady(true)
      drawPreview(img, 1.0, 0, 0)
    }
    img.src = url
    return () => URL.revokeObjectURL(url)
  }, [file])

  // Scale change
  const handleScaleChange = useCallback((v) => {
    const s = v / 100
    setScale(s)
    const clamped = clampOffset(offsetRef.current.x, offsetRef.current.y, s)
    offsetRef.current = clamped
    if (imageDataRef.current) {
      drawPreview(imageDataRef.current, s, clamped.x, clamped.y)
    }
  }, [clampOffset, drawPreview])

  const handleReset = useCallback(() => {
    setScale(1.0)
    offsetRef.current = { x: 0, y: 0 }
    if (imageDataRef.current) {
      drawPreview(imageDataRef.current, 1.0, 0, 0)
    }
  }, [drawPreview])

  // ---- Drag to pan ----
  const handleMouseDown = useCallback((e) => {
    e.preventDefault()
    draggingRef.current = true
    lastPosRef.current = { x: e.clientX, y: e.clientY }
  }, [])

  const handleMouseMove = useCallback((e) => {
    if (!draggingRef.current || !imageDataRef.current) return
    const dx = e.clientX - lastPosRef.current.x
    const dy = e.clientY - lastPosRef.current.y
    lastPosRef.current = { x: e.clientX, y: e.clientY }

    const newX = offsetRef.current.x + dx
    const newY = offsetRef.current.y + dy
    const clamped = clampOffset(newX, newY, scale)
    offsetRef.current = clamped
    drawPreview(imageDataRef.current, scale, clamped.x, clamped.y)
  }, [scale, clampOffset, drawPreview])

  const handleMouseUp = useCallback(() => {
    draggingRef.current = false
  }, [])

  // Global listeners for drag
  useEffect(() => {
    const onMove = (e) => handleMouseMove(e)
    const onUp = () => handleMouseUp()
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [handleMouseMove, handleMouseUp])

  // Touch support
  const handleTouchStart = useCallback((e) => {
    if (e.touches.length === 1) {
      e.preventDefault()
      draggingRef.current = true
      lastPosRef.current = { x: e.touches[0].clientX, y: e.touches[0].clientY }
    }
  }, [])

  const handleTouchMove = useCallback((e) => {
    if (!draggingRef.current || !imageDataRef.current || e.touches.length !== 1) return
    const dx = e.touches[0].clientX - lastPosRef.current.x
    const dy = e.touches[0].clientY - lastPosRef.current.y
    lastPosRef.current = { x: e.touches[0].clientX, y: e.touches[0].clientY }

    const newX = offsetRef.current.x + dx
    const newY = offsetRef.current.y + dy
    const clamped = clampOffset(newX, newY, scale)
    offsetRef.current = clamped
    drawPreview(imageDataRef.current, scale, clamped.x, clamped.y)
  }, [scale, clampOffset, drawPreview])

  const handleTouchEnd = useCallback(() => {
    draggingRef.current = false
  }, [])

  // Export
  const handleConfirm = useCallback(() => {
    const img = imageDataRef.current
    if (!img) return

    const minDim = Math.min(img.naturalWidth, img.naturalHeight)
    const srcSize = minDim / scale
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
  }, [scale, onConfirm])

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
            onMouseDown={handleMouseDown}
            onTouchStart={handleTouchStart}
            onTouchMove={handleTouchMove}
            onTouchEnd={handleTouchEnd}
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
