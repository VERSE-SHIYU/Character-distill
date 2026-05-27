import { useState, useEffect, useRef, useCallback } from 'react'
import useAppStore from '../store/useAppStore'
import { fetchWithTimeout } from '../api/client'

const FONT_SIZES = [14, 16, 18, 20, 22]
const LS_FONT_KEY = 'reader_font_size'
const LS_THEME_KEY = 'reader_theme'

function renderMarkdown(text) {
  if (!text) return ''
  let html = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')

  // Code block first
  html = html.replace(/```[\s\S]*?```/g, (m) => {
    const code = m.replace(/```/g, '').trim()
    return `<pre><code>${code}</code></pre>`
  })

  // Inline code
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>')

  // Headers
  html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>')
  html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>')
  html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>')

  // Blockquotes
  html = html.replace(/^&gt; (.+)$/gm, '<blockquote><p>$1</p></blockquote>')

  // Unordered lists
  html = html.replace(/^- (.+)$/gm, '<li>$1</li>')
  html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>')

  // Bold & italic
  html = html.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>')

  // Paragraphs — wrap remaining lines
  const lines = html.split('\n')
  let inBlock = false
  const result = []
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (line.startsWith('<h') || line.startsWith('<pre') || line.startsWith('<blockquote') || line.startsWith('<ul') || line.startsWith('<li') || line.startsWith('</')) {
      result.push(line)
      inBlock = true
    } else if (line.trim() === '') {
      if (inBlock) {
        inBlock = false
      }
      result.push('')
    } else if (line.startsWith('<')) {
      result.push(line)
    } else {
      result.push(`<p>${line}</p>`)
    }
  }
  return result.join('\n')
}

function extractTOC(content) {
  const toc = []
  const re = /^#{1,3}\s+(.+)$/gm
  let match
  while ((match = re.exec(content)) !== null) {
    toc.push({ level: match[0].startsWith('###') ? 3 : match[0].startsWith('##') ? 2 : 1, title: match[1] })
  }
  return toc
}

function stringToHash(str) {
  let hash = 0
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i)
    hash = ((hash << 5) - hash) + char
    hash = hash & hash
  }
  return Math.abs(hash)
}

const coverGradients = [
  ['#667eea', '#764ba2'],
  ['#f093fb', '#f5576c'],
  ['#4facfe', '#00f2fe'],
  ['#43e97b', '#38f9d7'],
  ['#fa709a', '#fee140'],
  ['#a18cd1', '#fbc2eb'],
  ['#fccb90', '#d57eeb'],
  ['#e0c3fc', '#8ec5fc'],
  ['#f5576c', '#ff6f91'],
  ['#667eea', '#764ba2'],
]

function getCoverGradient(id) {
  const idx = stringToHash(id || '') % coverGradients.length
  return coverGradients[idx]
}

export { renderMarkdown, extractTOC, getCoverGradient }

export default function BookReader() {
  const readerTextId = useAppStore((s) => s.readerTextId)
  const setView = useAppStore((s) => s.setView)
  const texts = useAppStore((s) => s.texts)

  const [content, setContent] = useState('')
  const [title, setTitle] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [fontSize, setFontSize] = useState(() => {
    const saved = localStorage.getItem(LS_FONT_KEY)
    return saved ? parseInt(saved, 10) : 18
  })
  const [darkMode, setDarkMode] = useState(() => {
    return localStorage.getItem(LS_THEME_KEY) === 'dark'
  })
  const [progress, setProgress] = useState(0)
  const [showTOC, setShowTOC] = useState(false)
  const [toc, setToc] = useState([])

  const contentRef = useRef(null)
  const saveTimerRef = useRef(null)
  const lastSavedRef = useRef(0)

  useEffect(() => {
    if (!readerTextId) {
      setError('未指定文本')
      setLoading(false)
      return
    }

    const textInfo = texts.find((t) => t.id === readerTextId)
    if (textInfo) setTitle(textInfo.title || textInfo.filename || '')

    setLoading(true)
    setError('')

    Promise.all([
      fetchWithTimeout(`/api/text/${readerTextId}/read`).then((r) => {
        if (!r.ok) throw new Error('加载失败')
        return r.json()
      }),
      fetchWithTimeout(`/api/text/${readerTextId}/progress`).then((r) => r.json()),
    ])
      .then(([textData, progressData]) => {
        const c = textData.text?.content || ''
        setContent(c)
        setTitle(textData.text?.title || title)
        setToc(extractTOC(c))
        setProgress(progressData.progress || 0)
        lastSavedRef.current = progressData.scroll_position || 0

        // Restore scroll after render
        requestAnimationFrame(() => {
          if (contentRef.current && progressData.scroll_position) {
            contentRef.current.scrollTop = progressData.scroll_position
          }
        })
      })
      .catch((err) => {
        setError(err.message)
      })
      .finally(() => setLoading(false))
  }, [readerTextId])

  // Save progress with throttle
  const saveProgress = useCallback((scrollPos) => {
    const now = Date.now()
    if (now - lastSavedRef.current < 4000) return
    lastSavedRef.current = now

    const totalHeight = contentRef.current?.scrollHeight || 1
    const pct = Math.min(1, scrollPos / (totalHeight - window.innerHeight))

    fetchWithTimeout(`/api/text/${readerTextId}/progress`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ progress: pct, scroll_position: scrollPos }),
    }).catch(() => {})
  }, [readerTextId])

  const handleScroll = useCallback(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    saveTimerRef.current = setTimeout(() => {
      if (contentRef.current) {
        const sp = contentRef.current.scrollTop
        setProgress(contentRef.current.scrollTop)
        saveProgress(sp)
      }
    }, 500)
  }, [saveProgress])

  const handleFontSizeChange = (delta) => {
    const idx = FONT_SIZES.indexOf(fontSize)
    const newIdx = Math.max(0, Math.min(FONT_SIZES.length - 1, idx + delta))
    const newSize = FONT_SIZES[newIdx]
    setFontSize(newSize)
    localStorage.setItem(LS_FONT_KEY, String(newSize))
  }

  const toggleTheme = () => {
    setDarkMode((prev) => {
      const next = !prev
      localStorage.setItem(LS_THEME_KEY, next ? 'dark' : 'light')
      return next
    })
  }

  const goBack = () => {
    setView('mine')
  }

  const scrollToHeader = (headerText) => {
    if (!contentRef.current) return
    const html = contentRef.current.innerHTML
    const idx = html.indexOf(`>${headerText}</h`)
    if (idx >= 0) {
      // Find the nearest block-level element
      const before = html.substring(0, idx)
      const lineBreaks = (before.match(/</g) || []).length
      const children = contentRef.current.children
      if (children[lineBreaks]) {
        children[lineBreaks].scrollIntoView({ behavior: 'smooth', block: 'start' })
      }
    }
    setShowTOC(false)
  }

  const totalHeight = contentRef.current?.scrollHeight || 1
  const visibleHeight = contentRef.current?.clientHeight || 1
  const scrollTop = contentRef.current?.scrollTop || 0
  const progressPct = Math.min(100, Math.round((scrollTop / (totalHeight - visibleHeight)) * 100)) || 0

  if (loading) {
    return (
      <div className={`reader-container${darkMode ? ' reader-dark' : ' reader-light'}`}>
        <div className="reader-loading">加载中…</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className={`reader-container${darkMode ? ' reader-dark' : ' reader-light'}`}>
        <div className="reader-error">
          <p>{error}</p>
          <button className="btn-primary" onClick={goBack}>返回</button>
        </div>
      </div>
    )
  }

  return (
    <div className={`reader-container${darkMode ? ' reader-dark' : ' reader-light'}`}>
      {/* Top bar */}
      <div className="reader-top-bar">
        <button className="reader-top-btn" onClick={goBack} title="返回">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
        </button>
        <span className="reader-title">{title || '阅读'}</span>
        {toc.length > 0 && (
          <button className="reader-top-btn" onClick={() => setShowTOC(!showTOC)} title="目录">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
          </button>
        )}
      </div>

      {/* TOC panel */}
      {showTOC && toc.length > 0 && (
        <div className="reader-toc-overlay" onClick={() => setShowTOC(false)}>
          <div className="reader-toc-panel" onClick={(e) => e.stopPropagation()}>
            <div className="reader-toc-title">目录</div>
            {toc.map((item, i) => (
              <div
                key={i}
                className={`reader-toc-item reader-toc-level-${item.level}`}
                onClick={() => scrollToHeader(item.title)}
              >
                {item.title}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Content */}
      <div
        ref={contentRef}
        className="reader-content"
        style={{ fontSize: `${fontSize}px` }}
        onScroll={handleScroll}
        dangerouslySetInnerHTML={{ __html: renderMarkdown(content) }}
      />

      {/* Bottom bar */}
      <div className="reader-bottom-bar">
        <span className="reader-progress-text">{progressPct}%</span>
        <div className="reader-bottom-group">
          <button className="reader-bottom-btn" onClick={() => handleFontSizeChange(-1)} disabled={fontSize <= FONT_SIZES[0]} title="缩小字体">
            A<sup>-</sup>
          </button>
          <span className="reader-font-size-label">{fontSize}</span>
          <button className="reader-bottom-btn" onClick={() => handleFontSizeChange(1)} disabled={fontSize >= FONT_SIZES[FONT_SIZES.length - 1]} title="放大字体">
            A<sup>+</sup>
          </button>
        </div>
        <button className="reader-bottom-btn reader-theme-btn" onClick={toggleTheme} title={darkMode ? '日间模式' : '夜间模式'}>
          {darkMode ? (
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
          ) : (
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
          )}
        </button>
      </div>
    </div>
  )
}
