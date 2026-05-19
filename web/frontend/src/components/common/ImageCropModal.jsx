import { useEffect, useRef, useState, useCallback } from 'react'

export default function ImageCropModal({ file, onConfirm, onCancel }) {
  const [scale, setScale] = useState(1.0)
  const [ready, setReady] = useState(false)
  const imgRef = useRef(null)
  const canvasRef = useRef(null)
  const imageDataRef = useRef(null)
  const SIZE = 300
  const RADIUS = SIZE / 2

  // Load image from file
  useEffect(() => {
    if (!file) return
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => {
      imageDataRef.current = img
      setReady(true)
      drawPreview(img, 1.0)
    }
    img.src = url
    return () => URL.revokeObjectURL(url)
  }, [file])

  const drawPreview = useCallback((img, s) => {
    const canvas = canvasRef.current
    if (!canvas || !img) return
    const ctx = canvas.getContext('2d')
    const minDim = Math.min(img.naturalWidth, img.naturalHeight)
    const srcSize = minDim / s
    const sx = (img.naturalWidth - srcSize) / 2
    const sy = (img.naturalHeight - srcSize) / 2

    ctx.clearRect(0, 0, SIZE, SIZE)

    // Circular clip
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

  const handleScaleChange = useCallback((v) => {
    const s = v / 100
    setScale(s)
    if (imageDataRef.current) {
      drawPreview(imageDataRef.current, s)
    }
  }, [drawPreview])

  const handleReset = useCallback(() => {
    setScale(1.0)
    if (imageDataRef.current) {
      drawPreview(imageDataRef.current, 1.0)
    }
  }, [drawPreview])

  const handleConfirm = useCallback(() => {
    const img = imageDataRef.current
    if (!img) return

    // Export via offscreen canvas for clean crop
    const exportCanvas = document.createElement('canvas')
    exportCanvas.width = SIZE
    exportCanvas.height = SIZE
    const ctx = exportCanvas.getContext('2d')

    const minDim = Math.min(img.naturalWidth, img.naturalHeight)
    const srcSize = minDim / scale
    const sx = (img.naturalWidth - srcSize) / 2
    const sy = (img.naturalHeight - srcSize) / 2

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
            style={{ display: ready ? 'block' : 'none' }}
          />
        </div>

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
