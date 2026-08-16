import { useEffect, useMemo, useState } from 'react'
import useAppStore from '../../store/useAppStore'
import Avatar from './Avatar'
import { formatChatTime } from '../../utils/time'

function previewText(text, max = 72) {
  if (!text) return '暂无消息'
  const one = text.replace(/\s+/g, ' ').trim()
  return one.length > max ? `${one.slice(0, max)}…` : one
}

export default function ChatSessionList() {
  const sessionList = useAppStore((s) => s.sessionList)
  const sessionListLoading = useAppStore((s) => s.sessionListLoading)
  const sessionId = useAppStore((s) => s.sessionId)
  const cardAvatars = useAppStore((s) => s.cardAvatars)
  const loadHistory = useAppStore((s) => s.loadHistory)
  const loadCardAvatar = useAppStore((s) => s.loadCardAvatar)
  const resumeSession = useAppStore((s) => s.resumeSession)
  const pushView = useAppStore((s) => s.pushView)
  const [q, setQ] = useState('')

  useEffect(() => {
    loadHistory()
  }, [loadHistory])

  // 懒加载未缓存的角色头像
  useEffect(() => {
    if (!sessionList.length) return
    const seen = new Set()
    sessionList.forEach((it) => {
      const id = it.card_id
      if (!id || seen.has(id) || cardAvatars[id]) return
      seen.add(id)
      loadCardAvatar(id)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionList])

  const filtered = useMemo(() => {
    const kw = q.trim().toLowerCase()
    if (!kw) return sessionList
    return sessionList.filter((s) =>
      (s.character_name || '').toLowerCase().includes(kw) ||
      (s.last_message || '').toLowerCase().includes(kw)
    )
  }, [sessionList, q])

  return (
    <aside className="tl-panel">
      <div className="tl-head">
        <div className="tl-title-row">
          <h3 className="tl-title">消息</h3>
          <button type="button" className="tl-new" onClick={() => pushView('character')} title="新对话">
            + 新对话
          </button>
        </div>
        <div className="tl-search">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></svg>
          <input placeholder="搜索会话" value={q} onChange={(e) => setQ(e.target.value)} aria-label="搜索会话" />
        </div>
      </div>
      <div className="tl-list">
        {sessionListLoading && !sessionList.length ? (
          <div className="tl-empty">加载中…</div>
        ) : filtered.length === 0 ? (
          <div className="tl-empty">暂无会话</div>
        ) : (
          filtered.map((it) => {
            const active = it.id === sessionId
            return (
              <button
                key={it.id}
                type="button"
                className={`tl-item${active ? ' is-active' : ''}`}
                onClick={() => { if (!active) resumeSession(it.id) }}
              >
                <Avatar name={it.character_name || '?'} size={40} src={cardAvatars[it.card_id]} />
                <div className="tl-item-body">
                  <div className="tl-item-head">
                    <span className="tl-item-name">{it.character_name}</span>
                    <span className="tl-item-time">
                      {it.last_message_at || it.updated_at ? formatChatTime(it.last_message_at || it.updated_at) : '—'}
                    </span>
                  </div>
                  <p className="tl-item-sub">{previewText(it.last_message)}</p>
                </div>
              </button>
            )
          })
        )}
      </div>
    </aside>
  )
}
