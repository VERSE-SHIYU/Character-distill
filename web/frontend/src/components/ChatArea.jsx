import { useCallback, useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import Avatar from './common/Avatar'

export default function ChatArea() {
  const currentCard = useAppStore((s) => s.currentCard)
  const sessionId = useAppStore((s) => s.sessionId)

  if (!currentCard || !sessionId) {
    return (
      <div className="shell-placeholder">
        <div className="shell-placeholder-inner">
          <div className="shell-placeholder-icon">{'\u{1F4AC}'}</div>
          <div className="shell-placeholder-title">
            {'\u8bf7\u5148\u9009\u62e9\u89d2\u8272'}
          </div>
          <div className="shell-placeholder-sub">
            {'\u5728\u201c\u89d2\u8272\u7ba1\u7406\u201d\u4e2d\u84b8\u998f\u5e76\u9009\u4e2d\u4e00\u4e2a\u89d2\u8272'}
          </div>
        </div>
      </div>
    )
  }

  return <ChatView />
}

function ChatView() {
  const messages = useAppStore((s) => s.messages)
  const sending = useAppStore((s) => s.sending)
  const currentCard = useAppStore((s) => s.currentCard)
  const userRole = useAppStore((s) => s.userRole)
  const currentTextId = useAppStore((s) => s.currentTextId)
  const texts = useAppStore((s) => s.texts)
  const resetChat = useAppStore((s) => s.resetChat)
  const setView = useAppStore((s) => s.setView)
  const sendMessageStream = useAppStore((s) => s.sendMessageStream)
  const revokeMessage = useAppStore((s) => s.revokeMessage)

  const cardData = typeof currentCard.card_json === 'string'
    ? JSON.parse(currentCard.card_json)
    : currentCard.card_json || currentCard
  const charName = cardData.name || currentCard.name || '?'
  const charIdentity = cardData.identity || ''

  const currentText = texts.find((t) => t.id === currentTextId)
  const textLabel = currentText
    ? `${currentText.filename} (${Number(currentText.char_count || 0).toLocaleString('zh-CN')}\u5b57)`
    : null

  const listRef = useRef(null)
  const bottomRef = useRef(null)
  const cancelStreamRef = useRef(null)

  // ---- TTS global singleton ----
  const audioRef = useRef(null)
  const [playingId, setPlayingId] = useState(null)

  const playTTS = useCallback(
    async (text, msgId) => {
      if (audioRef.current) {
        audioRef.current.pause()
        URL.revokeObjectURL(audioRef.current.src)
        audioRef.current = null
      }
      setPlayingId(msgId)
      try {
        const res = await fetch('/api/tts/synthesize', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text, voice: localStorage.getItem('tts_voice') || 'xiaoxiao' }),
        })
        if (!res.ok) throw new Error(`TTS ${res.status}`)
        const blob = await res.blob()
        const url = URL.createObjectURL(blob)
        audioRef.current = new Audio(url)
        audioRef.current.onended = () => {
          setPlayingId(null)
          URL.revokeObjectURL(url)
          audioRef.current = null
        }
        audioRef.current.onerror = () => {
          setPlayingId(null)
          URL.revokeObjectURL(url)
          audioRef.current = null
        }
        await audioRef.current.play()
      } catch {
        setPlayingId(null)
      }
    },
    [],
  )

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSend = useCallback(
    (text) => {
      if (!text.trim() || sending) return
      cancelStreamRef.current = sendMessageStream(text)
    },
    [sending, sendMessageStream],
  )

  const handleReset = useCallback(() => {
    if (!window.confirm('\u786e\u5b9a\u91cd\u7f6e\u5bf9\u8bdd\uff1f\u5386\u53f2\u6d88\u606f\u5c06\u88ab\u6e05\u7a7a\u3002')) return
    if (cancelStreamRef.current) {
      cancelStreamRef.current()
      cancelStreamRef.current = null
    }
    resetChat()
  }, [resetChat])

  const handleRevoke = useCallback(
    (index) => {
      if (!window.confirm('\u64a4\u56de\u8be5\u6d88\u606f\u53ca\u4e4b\u540e\u7684\u6240\u6709\u6d88\u606f\uff1f')) return
      revokeMessage(index)
    },
    [revokeMessage],
  )

  return (
    <div className="chat-area">
      {/* Top bar */}
      <div className="chat-topbar">
        <div className="chat-topbar-left">
          <Avatar name={charName} size={36} />
          <div className="chat-topbar-info">
            <span className="chat-topbar-name">{charName}</span>
            {charIdentity && (
              <span className="chat-topbar-badge">{charIdentity}</span>
            )}
          </div>
          {userRole && (
            <span className="chat-topbar-user-badge">
              {'\u{1F464} '}{userRole}
            </span>
          )}
        </div>
        <div className="chat-topbar-actions">
          <button
            type="button"
            className="chat-topbar-btn"
            onClick={handleReset}
            title={'\u91cd\u7f6e\u5bf9\u8bdd'}
          >
            {'\u{1F504}'}
          </button>
          <button
            type="button"
            className="chat-topbar-btn"
            onClick={() => setView('character')}
            title={'\u8fd4\u56de\u89d2\u8272\u5217\u8868'}
          >
            {'\u{1F464}'}
          </button>
        </div>
      </div>

      {/* Context banner */}
      {textLabel && (
        <div className="chat-context-banner">
          <span>{'\u{1F4D6} '}{textLabel}</span>
        </div>
      )}

      {/* Messages */}
      <div className="chat-messages" ref={listRef}>
        {messages.map((msg, i) => {
          if (msg.role === 'summary') {
            return <SummaryBubble key={i} content={msg.content} />
          }
          const isUser = msg.role === 'user'
          const isStreaming = sending && !isUser && i === messages.length - 1
          return (
            <MessageBubble
              key={i}
              index={i}
              isUser={isUser}
              content={msg.content}
              charName={charName}
              isStreaming={isStreaming}
              onRevoke={isUser ? handleRevoke : null}
              playTTS={playTTS}
              isPlaying={playingId === i}
            />
          )
        })}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <ChatInput onSend={handleSend} disabled={sending} />
    </div>
  )
}

// ---- Message bubble ----

function MessageBubble({ index, isUser, content, charName, isStreaming, onRevoke, playTTS, isPlaying }) {
  const [hovered, setHovered] = useState(false)

  return (
    <div
      className={`chat-msg ${isUser ? 'chat-msg-user' : 'chat-msg-char'}`}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {!isUser && (
        <div className="chat-msg-avatar">
          <Avatar name={charName} size={32} />
        </div>
      )}
      <div className={`chat-bubble ${isUser ? 'chat-bubble-user' : 'chat-bubble-char'}`}>
        <span className="chat-bubble-text">
          {content}
          {isStreaming && <span className="chat-cursor" />}
        </span>
      </div>
      {isUser && hovered && onRevoke && (
        <button
          type="button"
          className="chat-revoke-btn"
          onClick={() => onRevoke(index)}
          title={'\u64a4\u56de'}
        >
          {'\u{21A9}'}
        </button>
      )}
      {!isUser && (
        <button
          type="button"
          className="tts-play-btn"
          disabled={isPlaying}
          onClick={() => playTTS(content, index)}
          title={isPlaying ? '\u64ad\u653e\u4e2d' : '\u64ad\u653e\u8bed\u97f3'}
        >
          {isPlaying ? '\u23f3' : '\u{1F50A}'}
        </button>
      )}
    </div>
  )
}

// ---- Summary fold ----

function SummaryBubble({ content }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="chat-summary">
      <button
        type="button"
        className="chat-summary-toggle"
        onClick={() => setOpen((o) => !o)}
      >
        <span className="chat-summary-icon">{'\u{1F4CB}'}</span>
        <span>{'\u5bf9\u8bdd\u6458\u8981'}</span>
        <span className="chat-summary-arrow">{open ? '\u25B2' : '\u25BC'}</span>
      </button>
      {open && (
        <div className="chat-summary-body">
          {content}
        </div>
      )}
    </div>
  )
}

// ---- Input bar ----

function ChatInput({ onSend, disabled }) {
  const [text, setText] = useState('')
  const taRef = useRef(null)

  const handleSubmit = () => {
    if (!text.trim() || disabled) return
    onSend(text)
    setText('')
    setTimeout(() => taRef.current?.focus(), 0)
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  return (
    <div className="chat-input-bar">
      <textarea
        ref={taRef}
        className="chat-textarea"
        rows={1}
        placeholder={disabled ? '\u7b49\u5f85\u56de\u590d\u4e2d\u2026' : '\u8f93\u5165\u6d88\u606f\uff0cEnter \u53d1\u9001\uff0cShift+Enter \u6362\u884c'}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        disabled={disabled}
      />
      <button
        type="button"
        className="chat-send-btn btn-primary"
        disabled={disabled || !text.trim()}
        onClick={handleSubmit}
      >
        {'\u{27A4}'}
      </button>
    </div>
  )
}
