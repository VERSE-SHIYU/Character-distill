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

    const updateVvh = (h) => {
      document.documentElement.style.setProperty('--vvh', `${h}px`)
    }

    const onResize = () => {
      const h = vv.height
      updateVvh(h)
      // Dispatch vvchange only when keyboard opens (height shrinks)
      if (h < prevHeight - 1) {
        window.dispatchEvent(new CustomEvent(SYNC_EVENT, { detail: { height: h } }))
      }
      prevHeight = h
    }

    const onScroll = () => {
      updateVvh(vv.height)
      // Scroll events (toolbar show/hide) never trigger auto-scroll
    }

    updateVvh(prevHeight) // initial
    vv.addEventListener('resize', onResize)
    vv.addEventListener('scroll', onScroll)

    return () => {
      vv.removeEventListener('resize', onResize)
      vv.removeEventListener('scroll', onScroll)
      document.documentElement.style.removeProperty('--vvh')
    }
  }, [])
}
