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

    const sync = () => {
      const h = vv.height
      document.documentElement.style.setProperty('--vvh', `${h}px`)
      window.dispatchEvent(new CustomEvent(SYNC_EVENT, { detail: { height: h } }))
    }

    const onChange = () => sync()

    vv.addEventListener('resize', onChange)
    vv.addEventListener('scroll', onChange)
    sync() // initial

    return () => {
      vv.removeEventListener('resize', onChange)
      vv.removeEventListener('scroll', onChange)
      document.documentElement.style.removeProperty('--vvh')
    }
  }, [])
}
