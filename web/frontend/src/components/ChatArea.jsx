import { useCallback, useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { saveAvatar, getAvatar } from '../store/db'
import { compressImage } from '../utils/image'
import Avatar from './common/Avatar'
import Loading from './common/Loading'
import RoleSetupModal from './RoleSetupModal'

export default function ChatArea() {
  const currentCard = useAppStore((s) => s.currentCard)
  const sessionId = useAppStore((s) => s.sessionId)
  const resumeLoading = useAppStore((s) => s.resumeLoading)
  const setView = useAppStore((s) => s.setView)
  const startChat = useAppStore((s) => s.startChat)

  // Auto-recover: if we have a card but no session, try to create one
  useEffect(() => {
    if (currentCard && !sessionId && !resumeLoading) {
      startChat(currentCard)
    }
  }, [currentCard?.id, sessionId])

  if (!currentCard || !sessionId) {
    if (resumeLoading) {
      return (
        <div className="shell-placeholder">
          <Loading text="正在加载会话…" />
        </div>
      )
    }
    // Show loading while recovery effect runs
    if (currentCard && !sessionId) {
      return (
        <div className="shell-placeholder">
          <Loading text="正在创建会话…" />
        </div>
      )
    }
    return (
      <div className="shell-placeholder">
        <div className="shell-placeholder-inner">
          <div className="shell-placeholder-icon">{'\u{1F4AC}'}</div>
          <div className="shell-placeholder-title">
            请先选择一个角色开始对话
          </div>
          <div className="shell-placeholder-sub">
            在"角色管理"中蒸馏并选中一个角色后即可开始聊天
          </div>
          <div className="home-actions" style={{ marginTop: 16 }}>
            <button
              type="button"
              className="home-action-btn"
              onClick={() => setView('character')}
            >
              {'\u{1F464}'} 选择已有角色
            </button>
            <button
              type="button"
              className="home-action-btn"
              onClick={() => setView('text')}
            >
              {'\u{1F4C4}'} 上传新文本
            </button>
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
  const voiceStatus = useAppStore((s) => s.voiceStatus)
  const isRecording = useAppStore((s) => s.isRecording)
  const recordingDuration = useAppStore((s) => s.recordingDuration)
  const resetChat = useAppStore((s) => s.resetChat)
  const setView = useAppStore((s) => s.setView)
  const setUserRole = useAppStore((s) => s.setUserRole)
  const sendMessageStream = useAppStore((s) => s.sendMessageStream)
  const sessionId = useAppStore((s) => s.sessionId)
  const revokeMessage = useAppStore((s) => s.revokeMessage)
  const revokeCooldown = useAppStore((s) => s.revokeCooldown)
  const sendVoiceMessage = useAppStore((s) => s.sendVoiceMessage)

  const cardData = typeof currentCard.card_json === 'string'
    ? JSON.parse(currentCard.card_json)
    : currentCard.card_json || currentCard
  const charName = cardData.name || currentCard.name || '?'
  const charIdentity = cardData.identity || ''

  const [showRoleModal, setShowRoleModal] = useState(false)

  useEffect(() => {
    if (!userRole) setShowRoleModal(true)
  }, [])

  const currentText = texts.find((t) => t.id === currentTextId)
  const textLabel = currentText
    ? `${currentText.filename} (${Number(currentText.char_count || 0).toLocaleString('zh-CN')}字)`
    : null

  const listRef = useRef(null)
  const bottomRef = useRef(null)
  const cancelStreamRef = useRef(null)
  const userScrolledUp = useRef(false)

  // ---- Avatar ----
  const setCardAvatar = useAppStore((s) => s.setCardAvatar)
  const cardAvatars = useAppStore((s) => s.cardAvatars)

  const cardId = currentCard?.id || currentCard?.card_id
  const avatarUrl = cardAvatars[cardId] || null
  const avatarInputRef = useRef(null)

  // ---- User avatar ----
  const userAvatarUrl = useAppStore((s) => s.userAvatar)
  const setUserAvatar = useAppStore((s) => s.setUserAvatar)
  const userAvatarInputRef = useRef(null)

  useEffect(() => {
    if (sessionId) {
      const saved = localStorage.getItem(`user_avatar_${sessionId}`)
      if (saved) setUserAvatar(saved)
    }
  }, [sessionId])

  const handleUserAvatarChange = useCallback((e) => {
    const file = e.target.files?.[0]
    if (!file) return
    const url = URL.createObjectURL(file)
    setUserAvatar(url)
    const reader = new FileReader()
    reader.onload = () => {
      if (sessionId) localStorage.setItem(`user_avatar_${sessionId}`, reader.result)
      setUserAvatar(reader.result)
      URL.revokeObjectURL(url)
    }
    reader.readAsDataURL(file)
  }, [sessionId, setUserAvatar])

  useEffect(() => {
    let cancelled = false
    if (!cardId || cardAvatars[cardId]) return
    getAvatar(cardId).then((blob) => {
      if (!cancelled && blob) {
        const reader = new FileReader()
        reader.onload = () => setCardAvatar(cardId, reader.result)
        reader.readAsDataURL(blob)
      }
    })
    return () => { cancelled = true }
  }, [cardId, cardAvatars])

  const handleAvatarChange = async (e) => {
    const file = e.target.files?.[0]
    if (!file || !cardId) return
    await saveAvatar(cardId, file)
    const dataUrl = await compressImage(file, 200)
    setCardAvatar(cardId, dataUrl)
  }

  // ---- Global audio player for voice bubbles ----
  const audioRef = useRef(null)
  const [playingMsgId, setPlayingMsgId] = useState(null)

  const playAudio = useCallback((msgId, url) => {
    if (playingMsgId === msgId) {
      if (audioRef.current) { audioRef.current.pause() }
      setPlayingMsgId(null)
      return
    }
    if (audioRef.current) { audioRef.current.pause() }
    const a = new Audio(url)
    a.onended = () => setPlayingMsgId(null)
    a.onerror = () => setPlayingMsgId(null)
    a.play()
    audioRef.current = a
    setPlayingMsgId(msgId)
  }, [playingMsgId])

  // Cleanup audio on unmount
  useEffect(() => {
    return () => { if (audioRef.current) audioRef.current.pause() }
  }, [])

  // ---- TTS global singleton ----
  const ttsAudioRef = useRef(null)
  const [ttsPlayingId, setTtsPlayingId] = useState(null)

  const voiceList = useAppStore((s) => s.voiceList)
  const loadVoices = useAppStore((s) => s.loadVoices)
  const voiceEnabled = useAppStore((s) => s.voiceEnabled)
  const setVoiceEnabled = useAppStore((s) => s.setVoiceEnabled)

  useEffect(() => { loadVoices() }, [loadVoices])

  const playTTS = useCallback(
    async (text, msgId) => {
      if (ttsAudioRef.current) {
        ttsAudioRef.current.pause()
        URL.revokeObjectURL(ttsAudioRef.current.src)
        ttsAudioRef.current = null
      }
      setTtsPlayingId(msgId)
      const selectedVoice = localStorage.getItem('tts_voice') || 'xiaoxiao'
      const isCustom = voiceList.some((v) => v.voice_id === selectedVoice)
      try {
        const res = isCustom
          ? await fetch(`/api/voice/preview-audio/${selectedVoice}`)
          : await fetch('/api/voice/synthesize', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ text, voice: selectedVoice, card_id: currentCard?.id || '' }),
            })
        if (!res.ok) throw new Error(`TTS ${res.status}`)
        const blob = await res.blob()
        const url = URL.createObjectURL(blob)
        ttsAudioRef.current = new Audio(url)
        ttsAudioRef.current.onended = () => {
          setTtsPlayingId(null)
          URL.revokeObjectURL(url)
          ttsAudioRef.current = null
        }
        ttsAudioRef.current.onerror = () => {
          setTtsPlayingId(null)
          URL.revokeObjectURL(url)
          ttsAudioRef.current = null
        }
        await ttsAudioRef.current.play()
      } catch {
        setTtsPlayingId(null)
      }
    },
    [],
  )

  // Smart auto-scroll: only scroll down if user is already near bottom
  useEffect(() => {
    const el = listRef.current
    if (!el) return
    const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    if (!userScrolledUp.current || isNearBottom) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages])

  const handleScroll = useCallback(() => {
    const el = listRef.current
    if (!el) return
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight
    userScrolledUp.current = dist > 80
  }, [])

  const handleSend = useCallback(
    (text) => {
      if (!text.trim() || sending) return
      cancelStreamRef.current = sendMessageStream(text)
    },
    [sending, sendMessageStream],
  )

  const handleReset = useCallback(() => {
    if (!window.confirm('确定重置对话？历史消息将被清空。')) return
    if (cancelStreamRef.current) {
      cancelStreamRef.current()
      cancelStreamRef.current = null
    }
    resetChat()
  }, [resetChat])

  const handleRevoke = useCallback(
    () => {
      if (!window.confirm('撤回最近一条消息？')) return
      if (cancelStreamRef.current) {
        cancelStreamRef.current()
        cancelStreamRef.current = null
      }
      revokeMessage()
    },
    [revokeMessage],
  )

  return (
    <div className="chat-area">
      {/* Top bar */}
      <div className="chat-topbar">
        <div className="chat-topbar-left">
          <button
            type="button"
            className="chat-avatar-edit-wrap"
            onClick={() => avatarInputRef.current?.click()}
            title="更换头像"
          >
            <Avatar name={charName} src={avatarUrl} size={75} />
            <span className="chat-avatar-edit-icon">{'\u{1F4F7}'}</span>
          </button>
          <input
            ref={avatarInputRef}
            type="file"
            accept="image/*"
            className="sr-only"
            onChange={handleAvatarChange}
          />
          <input
            ref={userAvatarInputRef}
            type="file"
            accept="image/*"
            className="sr-only"
            onChange={handleUserAvatarChange}
          />
          <div className="chat-topbar-info">
            <span className="chat-topbar-name">{charName}</span>
            {charIdentity && (
              <span className="chat-topbar-badge">{charIdentity}</span>
            )}
          </div>
        </div>
        <div className="chat-topbar-actions">
          <div className="chat-voice-indicator">
            <span className={`voice-dot ${voiceEnabled ? 'on' : 'off'}`} />
            <button
              type="button"
              className="voice-toggle-mini"
              onClick={() => setVoiceEnabled(!voiceEnabled)}
              title={voiceEnabled ? '关闭语音' : '开启语音'}
            >
              {voiceEnabled ? '\u{1F50A}' : '\u{1F507}'}
            </button>
          </div>
          <button
            type="button"
            className="chat-topbar-btn"
            onClick={handleReset}
            title="重置对话"
          >
            {'\u{1F504}'}
          </button>
          <button
            type="button"
            className="chat-topbar-btn"
            onClick={() => setView('character')}
            title="返回角色列表"
          >
            {'\u{1F464}'}
          </button>
        </div>
      </div>

      {/* User role bar */}
      <div className="user-role-bar">
        <span className="user-role-label">我扮演：</span>
        <input
          type="text"
          className="user-role-input"
          placeholder="输入你的角色名，如：江澄"
          value={userRole}
          onChange={(e) => setUserRole(e.target.value)}
          onBlur={() => setUserRole(userRole)}
        />
        {cardData?.identity?.relationships && (
          <div className="user-role-presets">
            {Object.keys(cardData.identity.relationships).slice(0, 4).map((name) => (
              <button
                key={name}
                type="button"
                className={`user-role-preset-btn${userRole === name ? ' active' : ''}`}
                onClick={() => setUserRole(name)}
              >
                {name}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Source text context */}
      {textLabel && (
        <div className="chat-context-banner">
          <span className="chat-context-source">
            {'\u{1F4D6}'} 来自：
            <button
              type="button"
              className="chat-context-link"
              onClick={() => setView('character')}
            >
              {textLabel}
            </button>
          </span>
        </div>
      )}

      {/* Messages */}
      <div className="chat-messages" ref={listRef} onScroll={handleScroll}>
        {messages.map((msg, i) => {
          if (msg.role === 'summary') {
            return <SummaryBubble key={i} content={msg.content} />
          }

          // Time divider: show if gap from previous message > 5 min
          const prevMsg = i > 0 ? messages[i - 1] : null
          const showTime = prevMsg && msg.timestamp && prevMsg.timestamp
            ? (new Date(msg.timestamp) - new Date(prevMsg.timestamp)) > 5 * 60 * 1000
            : false

          const isUser = msg.role === 'user'
          const isStreaming = sending && !isUser && i === messages.length - 1
          const lastUserIdx = [...messages].reverse().findIndex((m) => m.role === 'user')
          const lastUserMsgIndex = lastUserIdx >= 0 ? messages.length - 1 - lastUserIdx : -1
          return (
            <div key={i}>
              {showTime && (
                <div className="time-divider">{formatTime(msg.timestamp)}</div>
              )}
              <MessageBubble
                index={i}
                isUser={isUser}
                isLastUserMsg={i === lastUserMsgIndex}
                content={msg.content}
                charName={charName}
                avatarUrl={avatarUrl}
                userRole={userRole}
                isStreaming={isStreaming}
                onRevoke={isUser ? handleRevoke : null}
                revokeCooldown={revokeCooldown}
                playTTS={playTTS}
                isPlaying={ttsPlayingId === i}
                audioUrl={msg.audio_url}
                isAudioPlaying={playingMsgId === msg.id || playingMsgId === i}
                onPlayAudio={playAudio}
                userAvatarUrl={userAvatarUrl}
                onUserAvatarClick={() => userAvatarInputRef.current?.click()}
              />
            </div>
          )
        })}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <ChatInput
        onSend={handleSend}
        disabled={sending}
        voiceStatus={voiceStatus}
        isRecording={isRecording}
        recordingDuration={recordingDuration}
        sendVoiceMessage={sendVoiceMessage}
      />

      <RoleSetupModal
        isOpen={showRoleModal}
        characterName={charName}
        relationships={cardData.relationships || []}
        onConfirm={(role) => setShowRoleModal(false)}
        onSkip={() => setShowRoleModal(false)}
      />
    </div>
  )
}

// ---- Message bubble ----

function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  const h = d.getHours()
  const m = d.getMinutes().toString().padStart(2, '0')
  const ap = h < 12 ? '上午' : '下午'
  const h12 = h % 12 || 12
  return `${ap} ${h12}:${m}`
}

function MessageBubble({ index, isUser, isLastUserMsg, content, charName, avatarUrl, userRole, isStreaming, onRevoke, revokeCooldown, playTTS, isPlaying, audioUrl, isAudioPlaying, onPlayAudio, userAvatarUrl, onUserAvatarClick }) {
  const [hovered, setHovered] = useState(false)

  const userInitial = (userRole || '我').charAt(0)

  return (
    <div
      className={`chat-msg ${isUser ? 'chat-msg-user' : 'chat-msg-char'}`}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {!isUser ? (
        <div className="chat-msg-avatar">
          <Avatar name={charName} src={avatarUrl} size={70} />
        </div>
      ) : (
        <div className="user-avatar-circle" style={userAvatarUrl ? { backgroundImage: `url(${userAvatarUrl})`, backgroundSize: 'cover', backgroundPosition: 'center' } : {}} onClick={onUserAvatarClick}>
          {!userAvatarUrl && userInitial}
        </div>
      )}
      <div className={`chat-bubble ${isUser ? 'chat-bubble-user' : 'chat-bubble-char'}`}>
        <span className="chat-bubble-text">
          {content}
          {isStreaming && <span className="chat-cursor" />}
        </span>
        {/* Voice bubble */}
        {!isUser && audioUrl && (
          <div
            className={`voice-bubble${isAudioPlaying ? ' playing' : ''}`}
            onClick={(e) => { e.stopPropagation(); onPlayAudio(index, audioUrl) }}
          >
            <span className="voice-waves"><i /><i /><i /></span>
            <span className="voice-duration">{''}</span>
          </div>
        )}
      </div>
      {isUser && onRevoke && isLastUserMsg && (
        <button
          type="button"
          className={`chat-revoke-btn${revokeCooldown ? ' revoke-cooldown' : ''}`}
          disabled={revokeCooldown}
          onClick={() => onRevoke()}
          title={revokeCooldown ? '冷却中…' : '撤回'}
        >
          {revokeCooldown ? '⏳' : '✕'}
        </button>
      )}
      {!isUser && (
        <button
          type="button"
          className="tts-play-btn"
          disabled={isPlaying}
          onClick={() => playTTS(content, index)}
          title={isPlaying ? '合成中' : '播放语音'}
        >
          {isPlaying ? '\u{23F3} 合成中' : '\u{1F50A} 听'}
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
        <span>对话摘要</span>
        <span className="chat-summary-arrow">{open ? '▲' : '▼'}</span>
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

function ChatInput({ onSend, disabled, voiceStatus, isRecording, recordingDuration, sendVoiceMessage }) {
  const [text, setText] = useState('')
  const taRef = useRef(null)
  const mediaRecorderRef = useRef(null)
  const chunksRef = useRef([])
  const timerRef = useRef(null)

  const set = useAppStore.setState

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

  // ---- Recording ----
  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream, { mimeType: 'audio/webm' })
      mediaRecorderRef.current = mr
      chunksRef.current = []

      mr.ondataavailable = (e) => chunksRef.current.push(e.data)
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop())
        if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
        const dur = useAppStore.getState().recordingDuration
        set({ isRecording: false, recordingDuration: 0 })
        if (dur < 1) return // too short, cancel
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' })
        const asrText = await sendVoiceMessage(blob)
        if (asrText && taRef.current) {
          setText(asrText)
          setTimeout(() => {
            if (taRef.current) {
              taRef.current.style.height = 'auto'
              taRef.current.style.height = Math.min(taRef.current.scrollHeight, 120) + 'px'
            }
          }, 0)
        }
      }

      mr.start()
      set({ isRecording: true, recordingDuration: 0 })
      timerRef.current = setInterval(() => {
        set((s) => ({ recordingDuration: s.recordingDuration + 1 }))
      }, 1000)
    } catch {
      // Permission denied or no mic
    }
  }

  const stopRecording = () => {
    if (mediaRecorderRef.current?.state === 'recording') {
      if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
      mediaRecorderRef.current.stop()
    }
  }

  // Recording cancel
  const cancelRecording = useCallback(() => {
    const mr = mediaRecorderRef.current
    if (mr && mr.state !== 'inactive') {
      mr.ondataavailable = null
      mr.onstop = null
      mr.stop()
      mr.stream?.getTracks().forEach((t) => t.stop())
    }
    chunksRef.current = []
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
    set({ isRecording: false, recordingDuration: 0 })
  }, [set])

  useEffect(() => {
    if (!isRecording) return
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') cancelRecording()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isRecording, cancelRecording])

  // Recording UI
  if (isRecording) {
    return (
      <div className="chat-input-bar recording-bar">
        <span className="recording-dot" />
        <span className="recording-text">{`录音中 ${recordingDuration}s`}</span>
        <button type="button" className="recording-cancel-btn" onClick={cancelRecording}>
          取消
        </button>
        <span className="recording-hint">按 Esc 取消</span>
      </div>
    )
  }

  const showMic = voiceStatus?.funasr

  return (
    <div className="chat-input-bar">
      {/* Voice placeholder */}
      <button
        type="button"
        className="chat-input-voice-btn"
        title="语音输入（暂未实装）"
        onClick={() => {}}
      >
        {'\u{1F399}'}
      </button>

      <textarea
        ref={taRef}
        className="chat-textarea"
        rows={1}
        placeholder={disabled ? '等待回复中…' : '输入消息…'}
        value={text}
        onChange={(e) => {
          setText(e.target.value)
          // Auto-resize
          const ta = e.target
          ta.style.height = 'auto'
          ta.style.height = Math.min(ta.scrollHeight, 120) + 'px'
        }}
        onKeyDown={handleKeyDown}
        disabled={disabled}
      />

      <button
        type="button"
        className="chat-send-btn"
        disabled={disabled || !text.trim()}
        onClick={handleSubmit}
      >
        发送
      </button>

      {showMic && (
        <button
          type="button"
          className="record-btn"
          onMouseDown={startRecording}
          onMouseUp={stopRecording}
          onMouseLeave={stopRecording}
          onTouchStart={startRecording}
          onTouchEnd={stopRecording}
          title="按住录音"
        >
          {'\u{1F3A4}'}
        </button>
      )}
    </div>
  )
}
