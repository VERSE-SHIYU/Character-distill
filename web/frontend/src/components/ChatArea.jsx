import { useCallback, useEffect, useRef, useState } from 'react'
import useAppStore from '../store/useAppStore'
import { Globe, Speaker, SpeakerOff, RefreshCw, User, FontDecrease, FontIncrease, Heart, Smile, Shield, Handshake, MessageSquare, Mic, Book, File } from './common/Icon'
import { saveAvatar, loadCardAvatar } from '../store/db'
import { fetchWithTimeout, getAuthHeaders } from '../api/client'
import Avatar from './common/Avatar'
import Loading from './common/Loading'
import RoleSetupModal from './RoleSetupModal'
import ImageCropModal from './common/ImageCropModal'
import ConfirmModal from './common/ConfirmModal'
import { formatChatTime } from '../utils/time'
import { useMention } from '../utils/useMention'
import { parseCardJson } from '../utils/card'
import MentionDropdown from './common/MentionDropdown'
import ChatHistoryPanel from './common/ChatHistoryPanel'
import EmojiPicker from './common/EmojiPicker'

export default function ChatArea() {
  const currentCard = useAppStore((s) => s.currentCard)
  const sessionId = useAppStore((s) => s.sessionId)
  const resumeLoading = useAppStore((s) => s.resumeLoading)
  const currentView = useAppStore((s) => s.currentView)
  const setView = useAppStore((s) => s.setView)
  const startChat = useAppStore((s) => s.startChat)

  // Auto-recover: only create session when user is actually on the chat view
  useEffect(() => {
    if (currentView === 'chat' && currentCard && !sessionId && !resumeLoading) {
      startChat(currentCard)
    }
  }, [currentView, currentCard?.id, sessionId])

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
          <div className="shell-placeholder-icon"><MessageSquare size={20} /></div>
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
              <User size={16} /> 选择已有角色
            </button>
            <button
              type="button"
              className="home-action-btn"
              onClick={() => setView('text')}
            >
              <File size={16} /> 上传新文本
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
  const webSearchEnabled = useAppStore((s) => s.webSearchEnabled)
  const setWebSearchEnabled = useAppStore((s) => s.setWebSearchEnabled)
  const affinity = useAppStore((s) => s.affinity)
  const affinityOpen = useAppStore((s) => s.affinityOpen)
  const setAffinityOpen = useAppStore((s) => s.setAffinityOpen)
  const affinityEnabled = useAppStore((s) => s.affinityEnabled)
  const setAffinityEnabled = useAppStore((s) => s.setAffinityEnabled)

  const cardData = parseCardJson(currentCard)
  const charName = cardData.name || currentCard.name || '?'
  const charIdentity = cardData.identity || ''

  const [showRoleModal, setShowRoleModal] = useState(false)
  const [cropFile, setCropFile] = useState(null)

  // Font size: 0=small, 1=medium (default), 2=large
  const [fontLevel, setFontLevel] = useState(() => {
    try { return parseInt(localStorage.getItem('charsim-font-level') || '1') }
    catch { return 1 }
  })
  useEffect(() => {
    try { localStorage.setItem('charsim-font-level', String(fontLevel)) }
    catch { /* noop */ }
  }, [fontLevel])

  const [resetConfirm, setResetConfirm] = useState(false)
  const [retractConfirm, setRetractConfirm] = useState(false)
  const [showMemoryPanel, setShowMemoryPanel] = useState(false)
  const [memories, setMemories] = useState([])
  const [memoriesLoading, setMemoriesLoading] = useState(false)
  const [memoryToast, setMemoryToast] = useState(false)

  const resumeSession = useAppStore((s) => s.resumeSession)

  const historyFetchSessions = useCallback(async (keyword) => {
    try {
      const res = await fetchWithTimeout(`/api/history/list?character=${encodeURIComponent(charName)}&keyword=${encodeURIComponent(keyword)}&page=1&page_size=20`)
      const data = await res.json()
      return (data.items || []).map((s) => ({
        id: s.id,
        title: s.character_name || charName,
        preview: s.last_message || '',
        time: s.last_message_at || s.updated_at,
      }))
    } catch { return [] }
  }, [charName])

  const historySelectSession = useCallback((session) => {
    resumeSession(session.id).catch(() => {})
  }, [resumeSession])

  useEffect(() => {
    if (!userRole && !sessionId) setShowRoleModal(true)
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
  const userAvatarInputRef = useRef(null)
  const [userCropFile, setUserCropFile] = useState(null)

  const handleUserAvatarChange = useCallback((e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUserCropFile(file)
    e.target.value = ''
  }, [])

  const handleUserCropConfirm = useCallback(async (base64) => {
    setUserCropFile(null)
    useAppStore.setState({ userAvatar: base64 })
    try {
      await useAppStore.getState().saveUserAvatar(base64)
    } catch { /* non-fatal */ }
  }, [])

  const handleUserCropCancel = useCallback(() => setUserCropFile(null), [])

  useEffect(() => {
    let cancelled = false
    if (!cardId || cardAvatars[cardId]) return
    if (currentCard?.avatar_data) {
      setCardAvatar(cardId, currentCard.avatar_data)
      return
    }
    loadCardAvatar(cardId).then((dataUrl) => {
      if (!cancelled && dataUrl) setCardAvatar(cardId, dataUrl)
    })
    return () => { cancelled = true }
  }, [cardId, cardAvatars, currentCard])

  const handleAvatarChange = useCallback((e) => {
    const file = e.target.files?.[0]
    if (!file || !cardId) return
    setCropFile(file)
    e.target.value = ''
  }, [cardId])

  const handleCropConfirm = useCallback(async (base64) => {
    setCropFile(null)
    if (!cardId) return
    try {
      const res = await fetch(base64)
      const blob = await res.blob()
      await saveAvatar(cardId, blob)
    } catch { /* non-fatal */ }
    try {
      await fetch(`/api/cards/${cardId}/avatar`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ data: base64 }),
      })
    } catch { /* non-fatal */ }
    setCardAvatar(cardId, base64)
  }, [cardId, setCardAvatar])

  const handleCropCancel = useCallback(() => setCropFile(null), [])

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
          ? await fetch(`/api/voice/preview-audio/${selectedVoice}`, { headers: { ...getAuthHeaders() } })
          : await fetch('/api/voice/synthesize', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
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

  const loadMemories = useCallback(async () => {
    if (!cardId) return
    setMemoriesLoading(true)
    try {
      const res = await fetchWithTimeout(`/api/memory/list/${cardId}`)
      const data = await res.json()
      setMemories(data.memories || [])
    } catch { /* ignore */ }
    finally { setMemoriesLoading(false) }
  }, [cardId])

  const handleSend = useCallback(
    (text) => {
      if (!text.trim() || sending) return
      cancelStreamRef.current = sendMessageStream(text)
      if (/记住|别忘了|你要记得|帮我记/.test(text)) {
        setMemoryToast(true)
        setTimeout(() => setMemoryToast(false), 2000)
      }
    },
    [sending, sendMessageStream],
  )

  const handleReset = useCallback(() => {
    setResetConfirm(true)
  }, [])

  const handleRevoke = useCallback(() => {
    setRetractConfirm(true)
  }, [])

  return (
    <div className={`chat-area${fontLevel === 0 ? ' has-text-sm' : fontLevel === 2 ? ' has-text-lg' : ''}`}>
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
              {voiceEnabled ? <Speaker size={16} /> : <SpeakerOff size={16} />}
            </button>
          </div>
          <div className="chat-web-search-ctl">
            <button
              type="button"
              className={`chat-topbar-btn web-search-toggle${webSearchEnabled ? ' active' : ''}`}
              onClick={() => setWebSearchEnabled(!webSearchEnabled)}
              title={webSearchEnabled ? '关闭现实增强' : '开启现实增强'}
            >
              <Globe size={16} />
            </button>
            <span className={`web-search-label${webSearchEnabled ? ' active' : ''}`}>
              {webSearchEnabled ? '现实增强：开' : '现实增强：关'}
            </span>
          </div>
          <div className="chat-font-size-ctl">
            <button
              type="button"
              className="chat-topbar-btn chat-font-btn"
              onClick={() => setFontLevel(Math.max(0, fontLevel - 1))}
              disabled={fontLevel === 0}
              title="缩小字体"
            >
              <FontDecrease size={16} />
            </button>
            <button
              type="button"
              className="chat-topbar-btn chat-font-btn"
              onClick={() => setFontLevel(Math.min(2, fontLevel + 1))}
              disabled={fontLevel === 2}
              title="放大字体"
            >
              <FontIncrease size={16} />
            </button>
          </div>
          <button
            type="button"
            className="chat-topbar-btn"
            onClick={handleReset}
            title="重置对话"
          >
            <RefreshCw size={16} />
          </button>
          <button
            type="button"
            className="chat-topbar-btn"
            onClick={() => setView('character')}
            title="返回角色列表"
          >
            <User size={16} />
          </button>
          <ChatHistoryPanel
            fetchSessions={historyFetchSessions}
            onSelectSession={historySelectSession}
            placeholder="搜索历史对话…"
          />
          <button
            type="button"
            className="chat-topbar-btn"
            title="角色记忆"
            onClick={() => { setShowMemoryPanel(true); loadMemories() }}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2a7 7 0 0 1 7 7c0 3-2 5.5-4 7.5L12 22l-3-5.5C7 14.5 5 12 5 9a7 7 0 0 1 7-7z"/>
              <circle cx="12" cy="9" r="2.5"/>
            </svg>
          </button>
        </div>
      </div>

      {/* Affinity panel */}
      {affinityEnabled && affinityOpen ? (
        <div className="affinity-bar">
          <button
            type="button"
            className="affinity-toggle"
            onClick={() => setAffinityOpen(false)}
            title="收起情感面板"
          >
            <span className="affinity-toggle-arrow">▼</span>
            <span className="affinity-toggle-label">情感状态</span>
            <AffinityInline value={affinity.affinity} icon={<Heart size={14} />} label="好感" />
            <AffinityInline value={affinity.trust} icon={<Handshake size={14} />} label="信任" />
          </button>
          <div className="affinity-detail">
            <AffinityItem value={affinity.affinity} icon={<Heart size={14} />} label="好感" />
            <AffinityItem value={affinity.trust} icon={<Handshake size={14} />} label="信任" />
            <AffinityItem value={affinity.mood} icon={<Smile size={14} />} label="情绪" isMood />
            <AffinityItem value={affinity.guard} icon={<Shield size={14} />} label="防御" />
            {affinity.reason && (
              <span className="affinity-reason" title={affinity.reason}>
                {affinity.reason}
              </span>
            )}
          </div>
        </div>
      ) : affinityEnabled ? (
        <button
          type="button"
          className="affinity-float-toggle"
          onClick={() => setAffinityOpen(true)}
          title="显示情感面板"
        >
          <Heart size={18} />
        </button>
      ) : null}

      {/* User role bar */}
      <div className="user-role-bar">
        <span className="user-role-label">我扮演：</span>
        {messages.length > 1 ? (
          <span className="user-role-locked">{userRole || '未设定'}</span>
        ) : (
          <>
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
          </>
        )}
      </div>

      {/* Source text context */}
      {textLabel && (
        <div className="chat-context-banner">
          <span className="chat-context-source">
            <Book size={14} /> 来自：
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
                <div className="time-divider">{formatChatTime(msg.timestamp)}</div>
              )}
              <MessageBubble
                index={i}
                isUser={isUser}
                isLastUserMsg={i === lastUserMsgIndex}
                content={msg.content}
                retracted={msg.retracted}
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
                timestamp={msg.timestamp}
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
        mentionableItems={[{ id: currentCard.id, name: charName }]}
      />

      <RoleSetupModal
        isOpen={showRoleModal}
        characterName={charName}
        relationships={cardData.relationships || []}
        textType={currentCard?.text_type || 'story'}
        onConfirm={(role) => setShowRoleModal(false)}
        onSkip={() => setShowRoleModal(false)}
      />

      <ImageCropModal
        file={cropFile}
        onConfirm={handleCropConfirm}
        onCancel={handleCropCancel}
      />
      <ImageCropModal
        file={userCropFile}
        onConfirm={handleUserCropConfirm}
        onCancel={handleUserCropCancel}
      />

      <ConfirmModal
        isOpen={resetConfirm}
        title="重置对话"
        message="确定重置对话？历史消息将被清空。"
        confirmText="确定"
        onConfirm={() => {
          setResetConfirm(false)
          if (cancelStreamRef.current) {
            cancelStreamRef.current()
            cancelStreamRef.current = null
          }
          resetChat()
        }}
        onCancel={() => setResetConfirm(false)}
        danger
      />
      <ConfirmModal
        isOpen={retractConfirm}
        title="撤回消息"
        message="撤回最近一条消息？"
        confirmText="确定"
        onConfirm={() => {
          setRetractConfirm(false)
          if (cancelStreamRef.current) {
            cancelStreamRef.current()
            cancelStreamRef.current = null
          }
          revokeMessage()
        }}
        onCancel={() => setRetractConfirm(false)}
        danger
      />

      {/* Memory panel */}
      {showMemoryPanel && (
        <div className="memory-panel-overlay" onClick={() => setShowMemoryPanel(false)}>
          <div className="memory-panel" onClick={e => e.stopPropagation()}>
            <div className="memory-panel-header">
              <h3>角色记忆</h3>
              <button type="button" className="btn-ghost" onClick={() => setShowMemoryPanel(false)}>✕</button>
            </div>
            <div className="memory-panel-body">
              {memoriesLoading ? (
                <p className="memory-empty">加载中…</p>
              ) : memories.length === 0 ? (
                <p className="memory-empty">暂无记忆，聊天中的重要信息会自动记录</p>
              ) : (
                memories.map((m) => (
                  <div key={m.id} className="memory-item">
                    <p className="memory-text">{m.memory}</p>
                    <button
                      type="button"
                      className="memory-delete-btn"
                      onClick={async () => {
                        await fetchWithTimeout(`/api/memory/delete/${m.id}?card_id=${cardId}`, { method: 'DELETE' })
                        setMemories(prev => prev.filter(x => x.id !== m.id))
                      }}
                      title="删除此记忆"
                    >✕</button>
                  </div>
                ))
              )}
            </div>
            {memories.length > 0 && (
              <div className="memory-panel-footer">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={async () => {
                    if (!confirm('确定清空该角色的全部记忆？')) return
                    await fetchWithTimeout(`/api/memory/clear/${cardId}`, { method: 'DELETE' })
                    setMemories([])
                  }}
                >清空全部记忆</button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Memory toast */}
      {memoryToast && (
        <div className="memory-toast">将记录到角色记忆</div>
      )}
    </div>
  )
}

// ---- Message bubble ----

function MessageBubble({ index, isUser, isLastUserMsg, content, retracted, charName, avatarUrl, userRole, isStreaming, onRevoke, revokeCooldown, playTTS, isPlaying, audioUrl, isAudioPlaying, onPlayAudio, userAvatarUrl, onUserAvatarClick, timestamp }) {
  const [hovered, setHovered] = useState(false)
  const [showRetracted, setShowRetracted] = useState(false)

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
        {!isUser && retracted ? (
          <span className="chat-bubble-text">
            <div className="msg-retracted" onClick={() => setShowRetracted(!showRetracted)}>
              <span className="retracted-text">对方撤回了一条消息</span>
              <span className="retracted-peek">{showRetracted ? '收起' : '点击偷看'}</span>
              {showRetracted && (
                <div className="retracted-original">{content}</div>
              )}
            </div>
          </span>
        ) : (
          <span className="chat-bubble-text">
            {content}
            {isStreaming && <span className="chat-cursor" />}
          </span>
        )}
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
        {timestamp && (
          <div className={`msg-time ${isUser ? 'msg-time-user' : ''}`}>{formatChatTime(timestamp)}</div>
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

function ChatInput({ onSend, disabled, voiceStatus, isRecording, recordingDuration, sendVoiceMessage, mentionableItems }) {
  const [text, setText] = useState('')
  const taRef = useRef(null)
  const [showEmoji, setShowEmoji] = useState(false)

  const handleMentionSelect = useCallback((item, atPos) => {
    if (atPos >= 0) {
      setText((prev) => {
        const cursorAfter = taRef.current?.selectionStart ?? prev.length
        return prev.slice(0, atPos) + '@' + item.name + ' ' + prev.slice(cursorAfter)
      })
    }
    setTimeout(() => taRef.current?.focus(), 0)
  }, [])

  const mentionHook = useMention(mentionableItems || [], {
    onSelect: handleMentionSelect,
    maxResults: 6,
  })
  const mediaRecorderRef = useRef(null)
  const chunksRef = useRef([])
  const timerRef = useRef(null)

  const set = useAppStore.setState

  // Emoji picker outside-click
  useEffect(() => {
    if (!showEmoji) return
    const handler = (e) => {
      if (!e.target.closest('.emoji-picker') && !e.target.closest('[data-emoji-btn]')) {
        setShowEmoji(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showEmoji])

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

  const funasrReady = voiceStatus?.funasr

  return (
    <div className="chat-input-bar">
      {funasrReady ? (
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
          <Mic size={16} />
        </button>
      ) : (
        <button
          type="button"
          className="chat-input-voice-btn"
          title="需要配置语音识别服务"
          disabled
        >
          {'\u{1F399}'}
        </button>
      )}

      <button type="button" data-emoji-btn className="record-btn" title="表情"
        onClick={() => setShowEmoji(!showEmoji)}
        style={{ fontSize: 18, lineHeight: 1 }}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>
      </button>

      <div style={{ position: 'relative', flex: 1, minWidth: 0 }}>
        {showEmoji && <EmojiPicker textareaRef={taRef} onEmojiSelect={() => setShowEmoji(false)} />}
        <textarea
          ref={taRef}
          className="chat-textarea"
          rows={1}
          placeholder={disabled ? '等待回复中…' : '输入消息…'}
          value={text}
          onChange={(e) => {
            const val = e.target.value
            setText(val)
            mentionHook.handleMentionInput(val, e.target.selectionStart, e.target)
            // Auto-resize
            const ta = e.target
            ta.style.height = 'auto'
            ta.style.height = Math.min(ta.scrollHeight, 120) + 'px'
          }}
          onKeyDown={(e) => {
            if (mentionHook.handleMentionKeyDown(e)) return
            handleKeyDown(e)
          }}
          disabled={disabled}
        />
        <MentionDropdown
          show={mentionHook.mentionActive}
          items={mentionHook.mentionItems}
          selectedIndex={mentionHook.selectedIndex}
          onSelect={(item) => handleMentionSelect(item, mentionHook.mentionAtPos)}
          position={mentionHook.mentionPosition}
        />
      </div>

      <button
        type="button"
        className="chat-send-btn"
        disabled={disabled || !text.trim()}
        onClick={handleSubmit}
      >
        发送
      </button>
    </div>
  )
}

// ---- Affinity helpers ----

function affinityColor(value) {
  if (value <= 30) return 'var(--affinity-low, #9ca3af)'
  if (value <= 50) return 'var(--affinity-mid, #3b82f6)'
  if (value <= 70) return 'var(--affinity-good, #22c55e)'
  if (value <= 90) return 'var(--affinity-high, #f97316)'
  return 'var(--affinity-max, #ef4444)'
}

function AffinityItem({ value, icon, label, isMood }) {
  const color = isMood ? 'var(--accent)' : affinityColor(value)
  return (
    <span className="affinity-item" style={{ color }} title={`${label}: ${value}`}>
      <span className="affinity-icon">{icon}</span>
      {isMood ? (
        <span className="affinity-mood">{value}</span>
      ) : (
        <span className="affinity-value">{value}</span>
      )}
    </span>
  )
}

function AffinityInline({ value, icon, label }) {
  const color = affinityColor(value)
  return (
    <span className="affinity-inline" style={{ color }} title={`${label}: ${value}`}>
      <span className="affinity-inline-icon">{icon}</span>
      <span className="affinity-inline-value">{value}</span>
    </span>
  )
}
