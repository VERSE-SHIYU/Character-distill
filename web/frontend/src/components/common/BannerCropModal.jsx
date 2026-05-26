import { useCallback, useEffect, useRef, useState } from 'react'

const ASPECT = 3 / 1
const EXPORT_W = 1200
const EXPORT_H = Math.round(EXPORT_W / ASPECT)
const DISPLAY_W = 480
const DISPLAY_H = Math.round(DISPLAY_W / ASPECT)
const MIN_SCALE = 1.2
const MAX_SCALE = 3.0

function getEffectiveSize(img, rotation) {
  const sw = img.naturalWidth, sh = img.naturalHeight
  return rotation % 180 !== 0 ? { w: sh, h: sw } : { w: sw, h: sh }
}

/** Render the raw image at the given rotation into an off-screen canvas. */
function renderRotated(img, rotation) {
  const { w, h } = getEffectiveSize(img, rotation)
  const c = document.createElement('canvas')
  c.width = w
  c.height = h
  const ctx = c.getContext('2d')
  ctx.translate(w / 2, h / 2)
  ctx.rotate(rotation * Math.PI / 180)
  ctx.drawImage(img, -img.naturalWidth / 2, -img.naturalHeight / 2)
  return c
}

export default function BannerCropModal({ file, onConfirm, onCancel }) {
  const [ready, setReady] = useState(false)
  const [scale, setScale] = useState(MIN_SCALE)
  const [dragging, setDragging] = useState(false)
  const [rotation, setRotation] = useState(0)

  const canvasRef = useRef(null)
  const imgRef = useRef(null)            // original source Image
  const rotCanvasRef = useRef(null)      // pre-rendered rotated canvas
  const offsetRef = useRef({ x: 0, y: 0 })
  const scaleRef = useRef(MIN_SCALE)
  const rotationRef = useRef(0)
  const draggingRef = useRef(false)
  const lastPosRef = useRef({ x: 0, y: 0 })
  const containerRef = useRef(null)

  useEffect(() => { scaleRef.current = scale }, [scale])
  useEffect(() => { rotationRef.current = rotation }, [rotation])

  // --- helpers ---
  const getSrc = () => rotCanvasRef.current || imgRef.current

  const clampOffset = (ox, oy, s) => {
    const src = getSrc()
    if (!src) return { x: 0, y: 0 }
    const sw = src.naturalWidth || src.width, sh = src.naturalHeight || src.height
    const fit = Math.max(DISPLAY_W / sw, DISPLAY_H / sh)
    const baseW = sw * fit, baseH = sh * fit
    const limitX = Math.max(0, (baseW * s - DISPLAY_W) / 2)
    const limitY = Math.max(0, (baseH * s - DISPLAY_H) / 2)
    return {
      x: Math.max(-limitX, Math.min(limitX, ox)),
      y: Math.max(-limitY, Math.min(limitY, oy)),
    }
  }

  const draw = useCallback((src, s, offX, offY) => {
    const canvas = canvasRef.current
    if (!canvas || !src) return
    const ctx = canvas.getContext('2d')
    const sw = src.naturalWidth || src.width, sh = src.naturalHeight || src.height
    const fit = Math.max(DISPLAY_W / sw, DISPLAY_H / sh)
    const baseW = sw * fit, baseH = sh * fit
    const zW = baseW * s, zH = baseH * s
    const px = (DISPLAY_W - zW) / 2 + offX
    const py = (DISPLAY_H - zH) / 2 + offY

    ctx.clearRect(0, 0, DISPLAY_W, DISPLAY_H)
    ctx.fillStyle = 'rgba(0,0,0,0.45)'
    ctx.fillRect(0, 0, DISPLAY_W, DISPLAY_H)

    ctx.save()
    ctx.beginPath()
    ctx.rect(0, 0, DISPLAY_W, DISPLAY_H)
    ctx.clip()
    ctx.drawImage(src, px, py, zW, zH)
    ctx.restore()

    // Crop frame
    ctx.strokeStyle = 'rgba(255,255,255,0.6)'
    ctx.lineWidth = 2
    ctx.strokeRect(1, 1, DISPLAY_W - 2, DISPLAY_H - 2)

    // Corner brackets
    const cl = 16, inset = 4
    ctx.strokeStyle = '#fff'
    ctx.lineWidth = 3
    ctx.beginPath()
    ;[
      [inset, inset, 1, 0, 0, 1],
      [DISPLAY_W - inset, inset, -1, 0, 0, 1],
      [inset, DISPLAY_H - inset, 1, 0, 0, -1],
      [DISPLAY_W - inset, DISPLAY_H - inset, -1, 0, 0, -1],
    ].forEach(([x, y, dx, dy]) => {
      ctx.moveTo(x, y + cl * dy * -1)
      ctx.lineTo(x, y)
      ctx.lineTo(x + cl * dx, y)
    })
    ctx.stroke()
  }, [])

  // --- rotation ---
  const applyRotation = useCallback((rot) => {
    const img = imgRef.current
    if (!img) return
    rotCanvasRef.current = renderRotated(img, rot)
  }, [])

  const handleRotate = useCallback(() => {
    const img = imgRef.current
    if (!img) return
    const newRot = (rotationRef.current + 90) % 360
    rotationRef.current = newRot
    setRotation(newRot)
    applyRotation(newRot)
    offsetRef.current = { x: 0, y: 0 }
    scaleRef.current = MIN_SCALE
    setScale(MIN_SCALE)
    draw(rotCanvasRef.current, MIN_SCALE, 0, 0)
  }, [draw, applyRotation])

  // --- load image ---
  useEffect(() => {
    if (!file) return
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => {
      imgRef.current = img
      rotCanvasRef.current = null
      offsetRef.current = { x: 0, y: 0 }
      scaleRef.current = MIN_SCALE
      setScale(MIN_SCALE)
      rotationRef.current = 0
      setRotation(0)
      setReady(true)
      draw(img, MIN_SCALE, 0, 0)
    }
    img.src = url
    return () => URL.revokeObjectURL(url)
  }, [file, draw])

  // --- wheel zoom ---
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const onWheel = (e) => {
      e.preventDefault()
      const src = getSrc()
      if (!src) return
      const delta = e.deltaY > 0 ? -0.1 : 0.1
      const ns = Math.max(MIN_SCALE, Math.min(MAX_SCALE, scaleRef.current + delta))
      scaleRef.current = ns
      setScale(ns)
      const clamped = clampOffset(offsetRef.current.x, offsetRef.current.y, ns)
      offsetRef.current = clamped
      draw(src, ns, clamped.x, clamped.y)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [draw])

  // --- mouse drag ---
  useEffect(() => {
    const onDown = (e) => {
      if (e.target !== canvasRef.current) return
      e.preventDefault()
      draggingRef.current = true
      setDragging(true)
      lastPosRef.current = { x: e.clientX, y: e.clientY }
    }
    const onMove = (e) => {
      if (!draggingRef.current) return
      const src = getSrc()
      if (!src) return
      const dx = e.clientX - lastPosRef.current.x
      const dy = e.clientY - lastPosRef.current.y
      lastPosRef.current = { x: e.clientX, y: e.clientY }
      const s = scaleRef.current
      const clamped = clampOffset(offsetRef.current.x + dx, offsetRef.current.y + dy, s)
      offsetRef.current = clamped
      draw(src, s, clamped.x, clamped.y)
    }
    const onUp = () => { draggingRef.current = false; setDragging(false) }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [draw])

  // --- touch drag ---
  useEffect(() => {
    const onStart = (e) => {
      if (e.target !== canvasRef.current || e.touches.length !== 1) return
      e.preventDefault()
      draggingRef.current = true
      lastPosRef.current = { x: e.touches[0].clientX, y: e.touches[0].clientY }
    }
    const onMove = (e) => {
      if (!draggingRef.current || e.touches.length !== 1) return
      const src = getSrc()
      if (!src) return
      const dx = e.touches[0].clientX - lastPosRef.current.x
      const dy = e.touches[0].clientY - lastPosRef.current.y
      lastPosRef.current = { x: e.touches[0].clientX, y: e.touches[0].clientY }
      const s = scaleRef.current
      const clamped = clampOffset(offsetRef.current.x + dx, offsetRef.current.y + dy, s)
      offsetRef.current = clamped
      draw(src, s, clamped.x, clamped.y)
    }
    const onEnd = () => { draggingRef.current = false; setDragging(false) }
    window.addEventListener('touchstart', onStart, { passive: false })
    window.addEventListener('touchmove', onMove, { passive: false })
    window.addEventListener('touchend', onEnd)
    return () => {
      window.removeEventListener('touchstart', onStart)
      window.removeEventListener('touchmove', onMove)
      window.removeEventListener('touchend', onEnd)
    }
  }, [draw])

  // --- controls ---
  const handleScaleChange = useCallback((v) => {
    const s = v / 100
    const src = getSrc()
    if (!src) return
    scaleRef.current = s
    setScale(s)
    const clamped = clampOffset(offsetRef.current.x, offsetRef.current.y, s)
    offsetRef.current = clamped
    draw(src, s, clamped.x, clamped.y)
  }, [draw])

  const handleReset = useCallback(() => {
    const img = imgRef.current
    if (!img) return
    rotCanvasRef.current = null
    rotationRef.current = 0
    setRotation(0)
    scaleRef.current = MIN_SCALE
    setScale(MIN_SCALE)
    offsetRef.current = { x: 0, y: 0 }
    draw(img, MIN_SCALE, 0, 0)
  }, [draw])

  // --- export ---
  const handleConfirm = useCallback(() => {
    const img = imgRef.current
    if (!img) return
    const s = scaleRef.current
    const rot = rotationRef.current
    const { x: ox, y: oy } = offsetRef.current
    const { w: eW, h: eH } = getEffectiveSize(img, rot)

    // Get or render the rotated source
    const src = rotCanvasRef.current || renderRotated(img, rot)

    const fit = Math.max(DISPLAY_W / eW, DISPLAY_H / eH)
    const baseW = eW * fit, baseH = eH * fit
    const zW = baseW * s, zH = baseH * s
    const px = (DISPLAY_W - zW) / 2 + ox
    const py = (DISPLAY_H - zH) / 2 + oy

    // Visible region in rotated-source coords
    const srcX = Math.max(0, (-px / zW) * eW)
    const srcY = Math.max(0, (-py / zH) * eH)
    const srcW = Math.min(eW, (DISPLAY_W / zW) * eW)
    const srcH = Math.min(eH, (DISPLAY_H / zH) * eH)

    const ec = document.createElement('canvas')
    ec.width = EXPORT_W
    ec.height = EXPORT_H
    const ectx = ec.getContext('2d')
    ectx.drawImage(src, srcX, srcY, srcW, srcH, 0, 0, EXPORT_W, EXPORT_H)
    onConfirm(ec.toDataURL('image/jpeg', 0.9))
  }, [onConfirm])

  if (!file) return null

  const pct = Math.round(scale * 100)

  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal-card banner-crop-card" onClick={(e) => e.stopPropagation()}>
        <h2 className="modal-title">调整封面位置</h2>
        <p className="banner-crop-dim-hint">建议尺寸 1200×400 · 拖拽调整位置，滚轮缩放</p>

        <div className="banner-crop-preview-wrap" ref={containerRef}>
          {!ready && <div className="crop-loading">加载中…</div>}
          <canvas
            ref={canvasRef}
            width={DISPLAY_W}
            height={DISPLAY_H}
            style={{
              display: ready ? 'block' : 'none',
              cursor: dragging ? 'grabbing' : 'grab',
              width: '100%',
              aspectRatio: ASPECT,
              borderRadius: 8,
            }}
          />
        </div>

        <div className="crop-controls">
          <label className="crop-slider-label">
            缩放
            <input
              type="range"
              className="crop-slider"
              min={Math.round(MIN_SCALE * 100)}
              max={300}
              value={pct}
              onChange={(e) => handleScaleChange(Number(e.target.value))}
            />
            <span className="crop-scale-value">{pct}%</span>
          </label>
          <button type="button" className="crop-reset-btn" onClick={handleReset}>
            重置
          </button>
        </div>

        <div className="crop-controls" style={{ justifyContent: 'center', marginTop: 4 }}>
          <button
            type="button"
            className="btn-ghost"
            onClick={handleRotate}
            disabled={!ready}
            style={{ fontSize: 13, gap: 4 }}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="1 4 1 10 7 10" />
              <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
            </svg>
            旋转
          </button>
        </div>

        <div className="crop-actions">
          <button type="button" className="btn-ghost" onClick={onCancel}>取消</button>
          <button type="button" className="btn-primary" onClick={handleConfirm} disabled={!ready}>确认</button>
        </div>
      </div>
    </div>
  )
}
