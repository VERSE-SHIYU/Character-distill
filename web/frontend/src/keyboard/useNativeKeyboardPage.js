import { useEffect } from 'react'
import useIsMobile from '../hooks/useIsMobile'
import { getKeyboardMode } from './keyboardMode'

/**
 * B2 — 页面声明「键盘交给浏览器」。挂载时给 <html> 打 data-kbd="native" 并清掉旧
 * hook 写的 --vvh；卸载时移除。只在开关 = native 且手机宽度时生效。
 *
 * 返回是否生效，供同页的 useKeyboardFocus 决定是否接管。
 */
export default function useNativeKeyboardPage() {
  const isMobile = useIsMobile()
  const enabled = getKeyboardMode() === 'native' && isMobile

  useEffect(() => {
    if (!enabled) return undefined
    const root = document.documentElement
    root.setAttribute('data-kbd', 'native')
    root.style.removeProperty('--vvh')
    return () => root.removeAttribute('data-kbd')
  }, [enabled])

  return enabled
}
