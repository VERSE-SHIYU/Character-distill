import { useCallback } from 'react'

/**
 * 拖动分隔条 → 按指针在容器里的位置算出比例（0–1），夹在 [min, max] 内交给 onChange。
 *
 * 全站分隔条的拖动只此一处：左右分栏（SplitOrFullscreen，axis 'x'）与上下分栏
 * （StackSplit，axis 'y'）共用。用 Pointer Events，鼠标与触屏同一套。
 *
 * 触发链：分隔条 pointerdown → 文档上监听 pointermove → 每次移动算比例 → onChange(ratio)
 *        → 调用方更新自己的状态、重排两栏；pointerup / pointercancel 时撤掉监听。
 */
export default function useSplitDrag(containerRef, { axis = 'x', min = 0, max = 1, onChange }) {
  return useCallback((e) => {
    e.preventDefault()
    const container = containerRef.current
    if (!container) return
    const rect = container.getBoundingClientRect()
    const onMove = (moveE) => {
      const ratio = axis === 'y'
        ? (moveE.clientY - rect.top) / rect.height
        : (moveE.clientX - rect.left) / rect.width
      onChange?.(Math.min(max, Math.max(min, ratio)))
    }
    const onUp = () => {
      document.removeEventListener('pointermove', onMove)
      document.removeEventListener('pointerup', onUp)
      document.removeEventListener('pointercancel', onUp)
    }
    document.addEventListener('pointermove', onMove)
    document.addEventListener('pointerup', onUp)
    document.addEventListener('pointercancel', onUp)
  }, [containerRef, axis, min, max, onChange])
}
