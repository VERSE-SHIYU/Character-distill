import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles/global.css'
import './styles/adm-theme.css'
import { initTheme, initFontDisplay } from './utils/theme'
import App from './App.jsx'
import ErrorBoundary from './components/common/ErrorBoundary'

initTheme()
initFontDisplay()

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ErrorBoundary><App /></ErrorBoundary>
  </StrictMode>,
)

// Register Service Worker for PWA
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {})
  })
}
