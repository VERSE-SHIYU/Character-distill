import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useAutoScroll } from '../hooks/useAutoScroll'
import useTypewriter from '../hooks/useTypewriter'
import useAppStore from '../store/useAppStore'
import { Globe, Speaker, SpeakerOff, RefreshCw, User, FontDecrease, FontIncrease, MessageSquare, Book, File, Heart } from './common/Icon'
import { saveAvatar, loadCardAvatar } from '../store/db'
import { fetchWithTimeout, getAuthHeaders } from '../api/client'
import Avatar from './common/Avatar'
import Loading from './common/Loading'
import ImageCropModal from './common/ImageCropModal'
import ConfirmModal from './common/ConfirmModal'
import { formatChatTime } from '../utils/time'
import { parseCardJson } from '../utils/card'
import ChatInputBar from './common/ChatInputBar'
import ChatBubble from './common/ChatBubble'
import MessageReactions from './common/MessageReactions'
import ReplyQuote from './common/ReplyQuote'
import { Calendar } from './common/ChatHistoryPanel'

export default function ChatArea() {
  const currentCard = useAppStore((s) => s.currentCard)
  const sessionId = useAppStore((s) => s.sessionId)
  const resumeLoading = useAppStore((s) => s.resumeLoading)
  const currentView = useAppStore((s) => s.currentView)
  const chatSnapshot = useAppStore((s) => s.chatSnapshot)
  const archiveModalOpen = useAppStore((s) => s.archiveModalOpen)
  const setView = useAppStore((s) => s.setView)
  const selectText = useAppStore((s) => s.selectText)
  const startChat = useAppStore((s) => s.startChat)

  // Auto-recover: only create session when user is on chat view and no snapshot/archive modal is blocking
  useEffect(() => {
    if (currentView === 'chat' && currentCard && !sessionId && !resumeLoading && !chatSnapshot && !archiveModalOpen) {
      startChat(currentCard)
    }
  }, [currentView, currentCard?.id, sessionId, resumeLoading, chatSnapshot, archiveModalOpen])

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
              onClick={() => {
                const tid = currentTextId || currentCard?.text_id
                if (tid) selectText(tid)
                else setView('character')
              }}
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
  const affinityEnabled = useAppStore((s) => s.affinityEnabled)
  const setAffinityEnabled = useAppStore((s) => s.setAffinityEnabled)
  const authUser = useAppStore((s) => s.authUser)
  const selectText = useAppStore((s) => s.selectText)
  const openCharacterList = useAppStore((s) => s.openCharacterList)

  const cardData = parseCardJson(currentCard)
  const charName = cardData.name || currentCard.name || '?'
  const charIdentity = cardData.identity || ''

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
  const [memoryClearConfirm, setMemoryClearConfirm] = useState(false)
  const [showMemoryPanel, setShowMemoryPanel] = useState(false)
  const [memories, setMemories] = useState([])
  const [memoriesLoading, setMemoriesLoading] = useState(false)
  const [editingMemId, setEditingMemId] = useState(null)
  const [editMemText, setEditMemText] = useState('')
  const [addingMem, setAddingMem] = useState(false)
  const [addMemText, setAddMemText] = useState('')
  const [memoryToast, setMemoryToast] = useState(false)
  const [stageToast, setStageToast] = useState(null) // { stage, stage_emoji }

  // Stage upgrade toast detection
  const prevStageRef = useRef(affinity.stage)
  useEffect(() => {
    const current = affinity.stage
    const prev = prevStageRef.current
    if (current && prev && current !== prev && affinity.affinity > 0) {
      setStageToast({ stage: current, stage_emoji: affinity.stage_emoji || '' })
      const t = setTimeout(() => setStageToast(null), 2500)
      return () => clearTimeout(t)
    }
    prevStageRef.current = current
  }, [affinity.stage, affinity.affinity, affinity.stage_emoji])

  // Reply-to state
  const [replyTo, setReplyTo] = useState(null) // { id, preview }
  // Reactions map: msgIndex -> [{ emoji, count, users }]
  const [reactions, setReactions] = useState({})

  // Load reactions when messages change
  useEffect(() => {
    if (!sessionId || messages.length === 0) { setReactions({}); return }
    fetchWithTimeout(`/api/chat/session/${sessionId}/reactions`)
      .then(r => r.json())
      .then(data => setReactions(data.reactions || {}))
      .catch(() => {})
  }, [sessionId, messages.length])

  const resumeSession = useAppStore((s) => s.resumeSession)

  const handleExport = useCallback(() => {
    const header = `角色名: ${charName}\n导出时间: ${new Date().toLocaleString('zh-CN')}\n---\n`
    const body = messages.map(m => {
      const time = m.timestamp ? new Date(m.timestamp).toLocaleString('zh-CN') : ''
      const speaker = m.role === 'user' ? (userRole || '我') : charName
      return `[${time}] ${speaker}: ${m.content || ''}`
    }).join('\n')
    const blob = new Blob([header + body], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = `${charName}.txt`
    document.body.appendChild(a); a.click()
    document.body.removeChild(a); URL.revokeObjectURL(url)
  }, [charName, messages, userRole])

  const currentText = texts.find((t) => t.id === currentTextId)
  const textLabel = currentText
    ? `${currentText.filename} (${Number(currentText.char_count || 0).toLocaleString('zh-CN')}字)`
    : null

  const listRef = useRef(null)
  const bottomRef = useRef(null)
  const cancelStreamRef = useRef(null)

  // ---- Avatar ----
  const setCardAvatar = useAppStore((s) => s.setCardAvatar)
  const cardAvatars = useAppStore((s) => s.cardAvatars)

  const cardId = currentCard?.id || currentCard?.card_id
  const avatarUrl = cardAvatars[cardId] || null
  const avatarInputRef = useRef(null)

  // ---- User avatar (session-level with global fallback) ----
  const globalUserAvatar = useAppStore((s) => s.userAvatar)
  const currentSessionAvatar = useAppStore((s) => s.currentSessionAvatar)
  const userAvatarUrl = currentSessionAvatar || globalUserAvatar
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
    if (!sessionId) {
      useAppStore.setState({ userAvatar: base64 })
      try {
        await useAppStore.getState().saveUserAvatar(base64)
      } catch { /* non-fatal */ }
      return
    }
    const prev = useAppStore.getState().currentSessionAvatar
    useAppStore.getState().setCurrentSessionAvatar(base64)
    try {
      await fetchWithTimeout(`/api/history/${sessionId}/avatar`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ avatar_data: base64 }),
      })
    } catch (err) {
      useAppStore.getState().setCurrentSessionAvatar(prev)
      useAppStore.setState({ error: '头像保存失败，请重试' })
    }
  }, [sessionId])

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

  // Smart auto-scroll
  const { handleScroll } = useAutoScroll(listRef, bottomRef, [messages])

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
      const rt = replyTo
      cancelStreamRef.current = sendMessageStream(text, rt?.id || null, rt?.preview || '')
      setReplyTo(null)
      if (/记住|别忘了|你要记得|帮我记/.test(text)) {
        setMemoryToast(true)
        setTimeout(() => setMemoryToast(false), 2000)
      }
    },
    [sending, sendMessageStream, replyTo],
  )

  const handleReset = useCallback(() => {
    setResetConfirm(true)
  }, [])

  const handleRevoke = useCallback(() => {
    setRetractConfirm(true)
  }, [])

  // Affinity popup state
  const [showInnerVoice, setShowInnerVoice] = useState(false)
  const innerVoicePopupRef = useRef(null)
  // More menu state
  const [showMore, setShowMore] = useState(false)
  const moreMenuRef = useRef(null)

  // Close popup/menu on outside click
  useEffect(() => {
    if (!showInnerVoice && !showMore) return
    const handler = (e) => {
      if (showInnerVoice && !e.target.closest('[data-affinity-trigger]') && !e.target.closest('.inner-voice-popup')) {
        setShowInnerVoice(false)
      }
      if (showMore && !e.target.closest('[data-more-trigger]') && !e.target.closest('.chat-more-menu')) {
        setShowMore(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showInnerVoice, showMore])

  const scrollToMessage = useCallback((msgId) => {
    const el = document.querySelector(`[data-msg-id="${msgId}"]`)
    el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [])

  // ── Sidebar history splitter ──
  const [historyOpen, setHistoryOpen] = useState(false)
  const [splitRatio, setSplitRatio] = useState(0.65)
  const splitContainerRef = useRef(null)

  const onSplitterMouseDown = useCallback((e) => {
    e.preventDefault()
    const container = splitContainerRef.current
    if (!container) return
    const rect = container.getBoundingClientRect()
    const onMove = (moveE) => {
      const ratio = (moveE.clientX - rect.left) / rect.width
      setSplitRatio(Math.min(0.8, Math.max(0.4, ratio)))
    }
    const onUp = () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [])

  // ── History panel state ──
  const [historyFilterDate, setHistoryFilterDate] = useState('')
  const [historySearchKeyword, setHistorySearchKeyword] = useState('')
  const [historyTab, setHistoryTab] = useState('history')
  const [historyFilterSpeaker, setHistoryFilterSpeaker] = useState('all')

  const filteredHistoryMessages = useMemo(() => {
    let result = messages.filter(m => m.role !== 'summary')
    if (historyFilterDate) {
      result = result.filter(m => {
        const ts = m.timestamp || m.created_at
        const d = ts ? new Date(ts).toISOString().slice(0, 10) : ''
        return d === historyFilterDate
      })
    }
    if (historySearchKeyword) {
      const q = historySearchKeyword.toLowerCase()
      result = result.filter(m => (m.content || '').toLowerCase().includes(q))
    }
    if (historyFilterSpeaker === 'other') {
      result = result.filter(m => m.role !== 'user')
    } else if (historyFilterSpeaker === 'me') {
      result = result.filter(m => m.role === 'user')
    }
    return result
  }, [messages, historyFilterDate, historySearchKeyword, historyFilterSpeaker])

  const historyDateGroups = useMemo(() => {
    const dates = new Set()
    for (const m of messages) {
      const ts = m.timestamp || m.created_at
      if (ts) {
        try { dates.add(new Date(ts).toISOString().slice(0, 10)) } catch {}
      }
    }
    return [...dates].sort().reverse()
  }, [messages])

  return (
    <div className={`chat-area${fontLevel === 0 ? ' has-text-sm' : fontLevel === 2 ? ' has-text-lg' : ''}`}>
      <div className="chat-with-history" ref={splitContainerRef} style={{ flex: 1, minHeight: 0 }}>
        <div className="chat-main-content" style={historyOpen ? { flex: splitRatio, minWidth: 0, display: 'flex', flexDirection: 'column' } : { flex: 1, display: 'flex', flexDirection: 'column' }}>
          <div style={{ position: 'relative', flexShrink: 0 }}>
          <div className="chat-topbar-compact">
        <div className="chat-topbar-compact-left">
          <button type="button" className="chat-topbar-back" onClick={() => {
            const tid = currentCard?.text_id || currentTextId
            if (tid) selectText(tid)
            else setView('character')
          }} title="返回">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
          </button>
          <button
            type="button"
            className="chat-topbar-avatar-btn avatar-shape"
            onClick={() => avatarInputRef.current?.click()}
            title="更换头像"
          >
            <Avatar name={charName} src={avatarUrl} size={48} />
          </button>
          <input ref={avatarInputRef} type="file" accept="image/*" className="sr-only" onChange={handleAvatarChange} />
          <input ref={userAvatarInputRef} type="file" accept="image/*" className="sr-only" onChange={handleUserAvatarChange} />
          <div className="chat-topbar-compact-name-row">
            <span className="chat-topbar-compact-name">{charName}</span>
            {charIdentity && <span className="chat-topbar-badge-compact">{charIdentity}</span>}
            {affinityEnabled && (
              <button
                type="button"
                data-affinity-trigger
                className="chat-topbar-mood-btn"
                onClick={() => setShowInnerVoice(v => !v)}
                title={affinity.mood || '情感状态'}
              >
                {affinity.mood_emoji || '😊'}
              </button>
            )}
          </div>
        </div>
        <div className="chat-topbar-compact-right">
          <button
            type="button"
            className={`chat-history-toggle${historyOpen ? ' active' : ''}`}
            onClick={() => setHistoryOpen(v => !v)}
            title="历史记录"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" />
              <polyline points="12 6 12 12 16 14" />
            </svg>
            历史
          </button>
          <button
            type="button"
            data-more-trigger
            className="chat-topbar-more-btn"
            onClick={() => setShowMore(v => !v)}
            title="更多"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/></svg>
          </button>
        </div>
      </div>

      {/* ── Inner voice popup (replaces old affinity bar) ── */}
      {showInnerVoice && (
        <div className="inner-voice-popup" ref={innerVoicePopupRef}>
          <div className="inner-voice-header">
            {affinity.mood_emoji || '😊'} {charName}此刻的想法
          </div>
          <div className="inner-voice-text">"{affinity.inner_voice || '…'}"</div>
          <div className="inner-voice-mood"><Heart size={14} /> {affinity.mood}</div>
          <div className="inner-voice-footer">
            <span className="stage-pill">{affinity.stage_emoji} {affinity.stage}</span>
            <span className="inner-voice-stats">♡{affinity.affinity} 🤝{affinity.trust} 🛡{affinity.guard}</span>
          </div>
        </div>
      )}

      {/* ── More menu ── */}
      {showMore && (
        <div className="chat-more-menu" ref={moreMenuRef}>
          <button type="button" className="chat-more-item" onClick={() => { setVoiceEnabled(!voiceEnabled); setShowMore(false) }}>
            {voiceEnabled ? <Speaker size={16} /> : <SpeakerOff size={16} />}
            <span>{voiceEnabled ? '关闭语音' : '开启语音'}</span>
          </button>
          <button type="button" className={`chat-more-item${webSearchEnabled ? ' active' : ''}`} onClick={() => { setWebSearchEnabled(!webSearchEnabled); setShowMore(false) }}>
            <Globe size={16} />
            <span>现实增强</span>
          </button>
          <button type="button" className="chat-more-item" onClick={() => { setFontLevel(Math.max(0, fontLevel - 1)); setShowMore(false) }} disabled={fontLevel === 0}>
            <FontDecrease size={16} />
            <span>缩小字号</span>
          </button>
          <button type="button" className="chat-more-item" onClick={() => { setFontLevel(Math.min(2, fontLevel + 1)); setShowMore(false) }} disabled={fontLevel === 2}>
            <FontIncrease size={16} />
            <span>放大字号</span>
          </button>
          <button type="button" className="chat-more-item" onClick={() => {
            const tid = currentCard?.text_id || currentTextId
            if (tid) { openCharacterList(tid) }
            else { setView('character') }
            setShowMore(false)
          }}>
            <User size={16} />
            <span>角色列表</span>
          </button>
          <button type="button" className="chat-more-item" onClick={() => { setShowMemoryPanel(true); loadMemories(); setShowMore(false) }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2a7 7 0 0 1 7 7c0 3-2 5.5-4 7.5L12 22l-3-5.5C7 14.5 5 12 5 9a7 7 0 0 1 7-7z"/><circle cx="12" cy="9" r="2.5"/></svg>
            <span>角色记忆</span>
          </button>
          <button type="button" className="chat-more-item chat-more-item-danger" onClick={() => { handleReset(); setShowMore(false) }}>
            <RefreshCw size={16} />
            <span>重置对话</span>
          </button>
        </div>
      )}
      </div>

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
              onClick={() => {
                const tid = currentCard?.text_id || currentTextId
                if (tid) selectText(tid)
                else setView('character')
              }}
            >
              {textLabel}
            </button>
          </span>
        </div>
      )}

          <div className="chat-messages" ref={listRef} onScroll={handleScroll}>
            {messages.map((msg, i) => {
              if (msg.role === 'summary') {
                return <SummaryBubble key={msg.id ?? msg._cid} content={msg.content} />
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
                <div key={msg.id ?? msg._cid} data-msg-id={msg.id}>
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
                    reactions={reactions[msg.id] || []}
                    replyToPreview={msg.reply_to_preview}
                    replyToId={msg.reply_to_id}
                    onReact={async (emoji) => {
                      if (!msg.id) return
                      try {
                        await fetchWithTimeout(`/api/chat/message/${msg.id}/react`, {
                          method: 'POST',
                          headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
                          body: JSON.stringify({ emoji }),
                        })
                        const res = await fetchWithTimeout(`/api/chat/session/${sessionId}/reactions`)
                        const data = await res.json()
                        setReactions(data.reactions || {})
                      } catch {}
                    }}
                    onReply={() => {
                      const preview = isUser
                        ? `我: ${(msg.content || '').slice(0, 60)}`
                        : `${charName}: ${(msg.content || '').slice(0, 60)}`
                      setReplyTo({ id: msg.id, preview })
                    }}
                    msgId={msg.id}
                    authUser={authUser}
                    onScrollToMessage={scrollToMessage}
                  />
                </div>
              )
            })}
            <div ref={bottomRef} />
          </div>

          <ChatInputBar
            onSend={handleSend}
            disabled={sending}
            disabledPlaceholder="等待回复中…"
            voice={{ status: voiceStatus, isRecording, recordingDuration, sendVoiceMessage }}
            mentionableItems={[{ id: currentCard.id, name: charName }]}
            replyTo={replyTo}
            onCancelReply={() => setReplyTo(null)}
          />
        </div>

        {historyOpen && (
          <>
            <div className="chat-splitter" onMouseDown={onSplitterMouseDown} />
            <div className="history-sidebar" style={{ flex: 1 - splitRatio, minWidth: 280, maxWidth: '50vw' }}>
              <div className="history-sidebar-content">
                <div className="history-sidebar-header">
                  <div className="chat-history-search-bar" style={{ flex: 1 }}>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
                      <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
                    </svg>
                    <input type="text" className="chat-history-search-input" placeholder="搜索消息…"
                      value={historySearchKeyword}
                      onChange={(e) => setHistorySearchKeyword(e.target.value)} />
                  </div>
                  <button type="button" className="chat-history-export-btn" onClick={handleExport} title="导出对话">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
                    </svg>
                  </button>
                  <button type="button" className="history-sidebar-close" onClick={() => setHistoryOpen(false)}>
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                  </button>
                </div>

                <div className="history-date-tabs">
                  <button type="button" className={`history-date-tab${historyTab === 'history' ? ' active' : ''}`}
                    onClick={() => setHistoryTab('history')}>历史</button>
                  <button type="button" className={`history-date-tab${historyTab === 'date' ? ' active' : ''}`}
                    onClick={() => setHistoryTab('date')}>日期</button>
                </div>

                <div className="history-speaker-tabs">
                  <button type="button" className={`history-speaker-tab${historyFilterSpeaker === 'all' ? ' active' : ''}`}
                    onClick={() => setHistoryFilterSpeaker('all')}>全部</button>
                  <button type="button" className={`history-speaker-tab${historyFilterSpeaker === 'other' ? ' active' : ''}`}
                    onClick={() => setHistoryFilterSpeaker('other')}>{charName}</button>
                  <button type="button" className={`history-speaker-tab${historyFilterSpeaker === 'me' ? ' active' : ''}`}
                    onClick={() => setHistoryFilterSpeaker('me')}>我</button>
                </div>

                {historyTab === 'date' ? (
                  <div className="history-sidebar-body">
                    <Calendar dateGroups={historyDateGroups} selectedDate={historyFilterDate}
                      onSelectDate={(iso) => { setHistoryFilterDate(iso || ''); if (iso) setHistoryTab('history') }} />
                  </div>
                ) : (
                  <div className="history-sidebar-body">
                    {(historyFilterDate || historyFilterSpeaker !== 'all') && (
                      <div className="group-history-filter-bar">
                        <span className="group-history-filter-label">筛选：</span>
                        {historyFilterDate && (
                          <span className="group-history-filter-chip">
                            {historyFilterDate}
                            <button type="button" className="group-history-filter-chip-x" onClick={() => setHistoryFilterDate('')}>
                              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                            </button>
                          </span>
                        )}
                        {historyFilterSpeaker !== 'all' && (
                          <span className="group-history-filter-chip">
                            {historyFilterSpeaker === 'other' ? charName : '我'}
                            <button type="button" className="group-history-filter-chip-x" onClick={() => setHistoryFilterSpeaker('all')}>
                              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                            </button>
                          </span>
                        )}
                      </div>
                    )}
                    {filteredHistoryMessages.length === 0 ? (
                      <div className="group-history-empty">暂无消息</div>
                    ) : (
                      <div className="group-history-list">
                        {filteredHistoryMessages.map((m, i) => {
                          const isUser = m.role === 'user'
                          const speakerName = isUser ? (userRole || '我') : charName
                          return (
                            <div key={m.id || i} className="group-history-item">
                              <Avatar name={speakerName} size={28}
                                src={isUser ? userAvatarUrl : avatarUrl} />
                              <div className="group-history-item-body">
                                <div className="group-history-item-head">
                                  <span className="group-history-item-speaker">{speakerName}</span>
                                  <span className="group-history-item-time">{m.timestamp ? formatChatTime(m.timestamp) : ''}</span>
                                </div>
                                <p className="group-history-item-text">{m.content}</p>
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </div>

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
      <ConfirmModal
        isOpen={memoryClearConfirm}
        title="清空记忆"
        message="确定清空该角色的全部记忆？此操作不可恢复。"
        confirmText="清空"
        onConfirm={async () => {
          setMemoryClearConfirm(false)
          await fetchWithTimeout(`/api/memory/clear/${cardId}`, { method: 'DELETE' })
          setMemories([])
        }}
        onCancel={() => setMemoryClearConfirm(false)}
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
              {/* Add memory row */}
              {addingMem ? (
                <div className="memory-add-row">
                  <textarea
                    className="memory-add-input"
                    placeholder="输入新记忆…"
                    value={addMemText}
                    onChange={e => setAddMemText(e.target.value)}
                    rows={2}
                  />
                  <div className="memory-add-actions">
                    <button type="button" className="btn-primary btn-sm" onClick={async () => {
                      if (!addMemText.trim()) return
                      try {
                        const res = await fetchWithTimeout(`/api/memory/add/${cardId}`, {
                          method: 'POST',
                          headers: { 'Content-Type': 'application/json' },
                          body: JSON.stringify({ text: addMemText.trim() }),
                        })
                        if (!res.ok) { alert('添加失败'); return }
                        setAddMemText('')
                        setAddingMem(false)
                        loadMemories()
                      } catch { alert('添加失败') }
                    }}>保存</button>
                    <button type="button" className="btn-ghost btn-sm" onClick={() => { setAddingMem(false); setAddMemText('') }}>取消</button>
                  </div>
                </div>
              ) : (
                <button type="button" className="memory-add-trigger" onClick={() => setAddingMem(true)}>
                  + 添加记忆
                </button>
              )}

              {memoriesLoading ? (
                <p className="memory-empty">加载中…</p>
              ) : memories.length === 0 ? (
                <p className="memory-empty">暂无记忆，聊天中的重要信息会自动记录</p>
              ) : (
                memories.map((m) => (
                  <div key={m.id} className="memory-item">
                    {editingMemId === m.id ? (
                      <div className="memory-edit-row">
                        <textarea
                          className="memory-edit-input"
                          value={editMemText}
                          onChange={e => setEditMemText(e.target.value)}
                          rows={2}
                        />
                        <div className="memory-edit-actions">
                          <button type="button" className="btn-primary btn-sm" onClick={async () => {
                            if (!editMemText.trim()) return
                            try {
                              const res = await fetchWithTimeout(`/api/memory/update/${m.id}?card_id=${cardId}`, {
                                method: 'PUT',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ text: editMemText.trim() }),
                              })
                              if (!res.ok) { alert('更新失败'); return }
                              setEditingMemId(null)
                              loadMemories()
                            } catch { alert('更新失败') }
                          }}>保存</button>
                          <button type="button" className="btn-ghost btn-sm" onClick={() => setEditingMemId(null)}>取消</button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <p className="memory-text">{m.memory}</p>
                        <div className="memory-item-actions">
                          <button type="button" className="memory-action-btn" onClick={() => {
                            setEditingMemId(m.id)
                            setEditMemText(m.memory)
                          }} title="编辑">✎</button>
                          <button type="button" className="memory-action-btn" onClick={async () => {
                            await fetchWithTimeout(`/api/memory/delete/${m.id}?card_id=${cardId}`, { method: 'DELETE' })
                            setMemories(prev => prev.filter(x => x.id !== m.id))
                          }} title="删除">✕</button>
                        </div>
                      </>
                    )}
                  </div>
                ))
              )}
            </div>
            {memories.length > 0 && (
              <div className="memory-panel-footer">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setMemoryClearConfirm(true)}
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

      {/* Stage upgrade toast */}
      {stageToast && (
        <div className="stage-toast">🎉 你和{charName}的关系变为「{stageToast.stage_emoji} {stageToast.stage}」</div>
      )}
    </div>
  )
}

// ---- Message bubble ----

function MessageBubble({ index, isUser, isLastUserMsg, content, retracted, charName, avatarUrl, userRole, isStreaming, onRevoke, revokeCooldown, playTTS, isPlaying, audioUrl, isAudioPlaying, onPlayAudio, userAvatarUrl, onUserAvatarClick, timestamp, reactions = [], replyToPreview, replyToId, onReact, onReply, msgId, authUser, onScrollToMessage }) {
  const [showRetracted, setShowRetracted] = useState(false)

  // Typewriter: smooth code-point-by-code-point reveal for streaming messages.
  const tw = useTypewriter()
  const twRef = useRef(tw); twRef.current = tw
  const lastCpCountRef = useRef(0)
  const prevStreamingRef = useRef(false)

  // Rising edge of isStreaming: reset typewriter on streaming start.
  useEffect(() => {
    if (isStreaming && !prevStreamingRef.current) {
      tw.reset()
      lastCpCountRef.current = 0
    }
    prevStreamingRef.current = isStreaming
  }, [isStreaming, tw])

  // Push code-point diffs into the typewriter queue (emoji-safe).
  useEffect(() => {
    if (!isStreaming) return
    const cps = [...content]
    if (cps.length > lastCpCountRef.current) {
      tw.push(cps.slice(lastCpCountRef.current).join(''))
      lastCpCountRef.current = cps.length
    }
  }, [content, isStreaming, tw])

  // Interrupt scenarios (revoke / cancel / unmount): finish immediately, no lost chars.
  useEffect(() => {
    return () => { twRef.current.flush(); twRef.current.reset() }
  }, [])

  const displayContent = tw.displayedText || content
  const userAvatarNode = (
    <Avatar
      name={userRole || '我'}
      src={userAvatarUrl}
      size={68}
      onClick={onUserAvatarClick}
    />
  )

  return (
    <div
      className={`chat-msg ${isUser ? 'chat-msg-user' : 'chat-msg-char'}`}
    >
      <ChatBubble
        side={isUser ? 'right' : 'left'}
        avatar={
          !isUser
            ? <Avatar name={charName} src={avatarUrl} size={68} />
            : userAvatarNode
        }
        time={timestamp ? formatChatTime(timestamp) : undefined}
      >
        {/* Reply quote */}
        <ReplyQuote preview={replyToPreview} messageId={replyToId} onScrollTo={onScrollToMessage} />

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
            {displayContent}
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

        <MessageReactions
          side={isUser ? 'right' : 'left'}
          reactions={reactions}
          showQuickBar={true}
          onReact={onReact}
          onReply={onReply}
          authUserId={authUser?.id}
        />
      </ChatBubble>
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



