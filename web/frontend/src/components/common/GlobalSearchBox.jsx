import { useEffect, useState, useRef } from 'react'
import useAppStore from '../../store/useAppStore'
import { globalSearch } from '../../api/client'
import Avatar from './Avatar'
import { Book } from './Icon'
import { displayName } from '../../utils/displayName'

export default function GlobalSearchBox({ className = '' }) {
  const navigateTo = useAppStore((s) => s.navigateTo)

  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState(null)
  const [searchOpen, setSearchOpen] = useState(false)
  const searchRef = useRef(null)
  const debounceRef = useRef(null)

  const handleSearchInput = (val) => {
    setSearchQuery(val)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    if (!val.trim()) { setSearchResults(null); setSearchOpen(false); return }
    debounceRef.current = setTimeout(async () => {
      try {
        const data = await globalSearch(val.trim())
        setSearchResults(data)
        setSearchOpen(true)
      } catch {}
    }, 300)
  }

  useEffect(() => {
    const handler = (e) => {
      if (searchRef.current && !searchRef.current.contains(e.target)) {
        setSearchOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') setSearchOpen(false) }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [])

  const handleResultClick = (type, item) => {
    setSearchOpen(false)
    setSearchQuery('')
    setSearchResults(null)
    if (type === 'card') {
      navigateTo('marketCardDetail', { marketCardId: item.id })
    } else if (type === 'text') {
      navigateTo('textDetail', { textDetailId: item.id })
    } else if (type === 'user') {
      navigateTo('author', { authorUserId: item.id })
    }
  }

  return (
    <div className={`sidebar-search-wrap${className ? ` ${className}` : ''}`} ref={searchRef}>
      <div className="sidebar-search-box">
        <svg className="sidebar-search-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        <input
          type="text"
          className="sidebar-search-input"
          placeholder="搜索角色、文本、用户…"
          value={searchQuery}
          onChange={(e) => handleSearchInput(e.target.value)}
          onFocus={() => { if (searchResults) setSearchOpen(true) }}
        />
      </div>
      {searchOpen && searchResults && (
        <div className="sidebar-search-dropdown">
          {searchResults.cards?.length === 0 && searchResults.texts?.length === 0 && searchResults.users?.length === 0 ? (
            <div className="sidebar-search-empty">未找到相关内容</div>
          ) : (
            <>
              {searchResults.cards?.length > 0 && (
                <div className="sidebar-search-group">
                  <div className="sidebar-search-group-title">角色</div>
                  {searchResults.cards.map((c) => (
                    <button key={c.id} className="sidebar-search-item" onClick={() => handleResultClick('card', c)}>
                      <Avatar name={c.name || '?'} size={28} src={c.avatar_data} />
                      <div className="sidebar-search-item-text">
                        <span className="sidebar-search-item-name">{c.name}</span>
                        <span className="sidebar-search-item-sub">{c.author_name || ''}</span>
                      </div>
                    </button>
                  ))}
                </div>
              )}
              {searchResults.texts?.length > 0 && (
                <div className="sidebar-search-group">
                  <div className="sidebar-search-group-title">文本</div>
                  {searchResults.texts.map((t) => (
                    <button key={t.id} className="sidebar-search-item" onClick={() => handleResultClick('text', t)}>
                      <span style={{ fontSize: 20, width: 28, textAlign: 'center', color: 'var(--text-dim)' }}><Book size={18} /></span>
                      <div className="sidebar-search-item-text">
                        <span className="sidebar-search-item-name">{t.title || t.filename}</span>
                      </div>
                    </button>
                  ))}
                </div>
              )}
              {searchResults.users?.length > 0 && (
                <div className="sidebar-search-group">
                  <div className="sidebar-search-group-title">用户</div>
                  {searchResults.users.map((u) => (
                    <button key={u.id} className="sidebar-search-item" onClick={() => handleResultClick('user', u)}>
                      <Avatar name={displayName(u) || '?'} size={28} src={u.avatar_data} />
                      <div className="sidebar-search-item-text">
                        <span className="sidebar-search-item-name">{displayName(u)}</span>
                        <span className="sidebar-search-item-sub">@{u.username}</span>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
