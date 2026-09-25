import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles/global.css'
import './styles/adm-theme.css'
import { initTheme, initFontDisplay } from './utils/theme'
import App from './App.jsx'
import ErrorBoundary from './components/common/ErrorBoundary'
import { initErrorReporting } from './observability'

initTheme()
initFontDisplay()

// 启动链顺序固定：取配置（同源一次 GET）→ 有 DSN 才 init → 带着报错钩子建根 → 渲染。
// 不能先渲染：钩子要在**第一次渲染之前**就位，否则首屏抛的错没人接。取不到配置或 DSN
// 为空都照常往下走（`initErrorReporting` 不抛，这时回 `{}`，createRoot 保持 React 默认行为）。
initErrorReporting().then((hooks) => {
  createRoot(document.getElementById('root'), hooks).render(
    <StrictMode>
      <ErrorBoundary><App /></ErrorBoundary>
    </StrictMode>,
  )
})

// Register Service Worker for PWA
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {})
  })
}
