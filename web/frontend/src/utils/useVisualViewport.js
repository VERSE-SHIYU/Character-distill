import { useEffect } from 'react'

/**
 * Track window.visualViewport height and expose it as --vvh CSS variable.
 * Also dispatch a 'vvchange' custom event so message containers can
 * auto-scroll when the keyboard opens.
 *
 * Only active when visualViewport is available (mobile browsers).
 * Call once at App top level.
 */
export default function useVisualViewport() {
  useEffect(() => {
    const vv = window.visualViewport
    if (!vv) return

    const SYNC_EVENT = 'vvchange'
    let prevHeight = vv.height
    let rafId = null

    const sync = () => {
      rafId = null
      const h = vv.height
      const off = vv.offsetTop
      const root = document.documentElement
      root.style.setProperty('--vvh', `${h}px`)
      // When keyboard opens (offsetTop > 0), lock visual viewport to top
      // so the input field stays at the bottom of the visible area
      if (off > 0) {
        window.scrollTo(0, 0)
      }
      // Dispatch vvchange only when keyboard opens (height shrinks)
      if (h < prevHeight - 1) {
        window.dispatchEvent(new CustomEvent(SYNC_EVENT, { detail: { height: h } }))
      }
      prevHeight = h
    }

    const schedule = () => {
      if (rafId === null) rafId = requestAnimationFrame(sync)
    }

    sync() // initial
    vv.addEventListener('resize', schedule)
    vv.addEventListener('scroll', schedule)

    return () => {
      if (rafId !== null) cancelAnimationFrame(rafId)
      vv.removeEventListener('resize', schedule)
      vv.removeEventListener('scroll', schedule)
      const root = document.documentElement
      root.style.removeProperty('--vvh')
    }
  }, [])
}
