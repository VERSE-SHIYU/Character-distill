import { createRoot } from 'react-dom/client'
import './styles/global.css'
import { initTheme } from './utils/theme'
import App from './App.jsx'

initTheme()

createRoot(document.getElementById('root')).render(
  <App />
)

// Register Service Worker for PWA
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {})
  })
}
