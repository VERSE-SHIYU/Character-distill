import { create } from 'zustand'
import { postJSON, streamSSE, fetchWithTimeout } from '../api/client'

const useAppStore = create((set, get) => ({
  currentView: 'home',
  setView: (view) => {
    const updates = { currentView: view }
    if (view === 'home' || view === 'text') updates.currentTextTitle = ''
    set(updates)
  },

  goBack: () => {
    const { currentView } = get()
    const backMap = { chat: 'character', character: 'text', text: 'home' }
    set({ currentView: backMap[currentView] || 'home' })
  },

  texts: [],
  currentTextId: null,
  currentTextTitle: '',

  cards: [],
  currentCard: null,
  sessionId: null,
  identifiedChars: [],
  distilling: false,
  distillTokenCount: 0,
  distillStatus: '',
  distillIncrementalActive: false,
  identifying: false,

  messages: [],
  loading: false,
  resumeLoading: false,
  sending: false,
  userRole: localStorage.getItem('user_role') || '',
  setUserRole: (role) => {
    localStorage.setItem('user_role', role)
    set({ userRole: role })
  },

  error: null,
  setError: (err) => set({ error: err }),

  apiConfigured: false,

  checkApiConfig: async () => {
    try {
      const res = await fetchWithTimeout('/api/settings/config')
      const data = await res.json()
      const configured = !!(data.base_url && data.model && data.api_key)
      set({ apiConfigured: configured })
      return configured
    } catch (err) {
      console.error('[store] checkApiConfig failed:', err)
      set({ apiConfigured: false })
      return false
    }
  },

  // Avatar sync
  cardAvatars: {},
  setCardAvatar: (cardId, dataUrl) => {
    set((state) => ({
      cardAvatars: { ...state.cardAvatars, [cardId]: dataUrl }
    }))
    localStorage.setItem(`card_avatar_${cardId}`, dataUrl)
  },
  loadCardAvatar: (cardId) => {
    const saved = localStorage.getItem(`card_avatar_${cardId}`)
    if (saved) {
      set((state) => ({
        cardAvatars: { ...state.cardAvatars, [cardId]: saved }
      }))
    }
  },

  // Voice cloning
  voiceStatus: { gptsovits: false, funasr: false },
  voiceEnabled: false,
  voiceSpeed: 1.0,
  voiceRefInfo: null,

  checkVoiceStatus: async () => {
    try {
      const res = await fetchWithTimeout('/api/voice/status')
      const data = await res.json()
      set({ voiceStatus: data })
    } catch {
      // Silent fail — service detection should never bother the user
    }
  },

  uploadRefAudio: async (file, cardId, promptText) => {
    const form = new FormData()
    form.append('file', file)
    form.append('card_id', cardId)
    form.append('prompt_text', promptText)
    const res = await fetchWithTimeout('/api/voice/ref-audio/upload', {
      method: 'POST',
      body: form,
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: '上传失败' }))
      throw new Error(err.detail || '上传失败')
    }
    await get().loadVoiceRef(cardId)
    return res.json()
  },

  loadVoiceRef: async (cardId) => {
    if (!cardId) { set({ voiceRefInfo: null }); return }
    try {
      const res = await fetchWithTimeout(`/api/voice/ref-audio/${cardId}`)
      const data = await res.json()
      set({ voiceRefInfo: data })
    } catch {
      set({ voiceRefInfo: null })
    }
  },

  deleteVoiceRef: async (cardId) => {
    await fetchWithTimeout(`/api/voice/ref-audio/${cardId}`, { method: 'DELETE' })
    set({ voiceRefInfo: null })
  },

  voiceList: [],
  loadVoices: async () => {
    try {
      const res = await fetchWithTimeout('/api/voice/list')
      const data = await res.json()
      set({ voiceList: Array.isArray(data) ? data : [] })
    } catch {
      // Silent fail — voice library is non-critical
    }
  },

  uploadCustomVoice: async (file, name) => {
    const form = new FormData()
    form.append('file', file)
    form.append('name', name)
    const res = await fetchWithTimeout('/api/voice/upload', {
      method: 'POST',
      body: form,
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: '上传失败' }))
      throw new Error(err.detail || '上传失败')
    }
    await get().loadVoices()
    return res.json()
  },

  deleteCustomVoice: async (voiceId) => {
    await fetchWithTimeout(`/api/voice/${voiceId}`, { method: 'DELETE' })
    await get().loadVoices()
  },

  setVoiceEnabled: (bool) => set({ voiceEnabled: bool }),
  setVoiceSpeed: (speed) => set({ voiceSpeed: speed }),

  // Recording
  isRecording: false,
  recordingDuration: 0,

  _synthesizeVoiceReply: async (reply, charIdx) => {
    const { sessionId } = get()
    if (!reply || !sessionId) return
    // Strip action/narration in parentheses before TTS
    const ttsText = reply
      .replace(/（[^）]*）/g, '')
      .replace(/\([^)]*\)/g, '')
      .replace(/\s+/g, ' ')
      .trim()
    if (!ttsText) return
    try {
      const selectedVoice = localStorage.getItem('tts_voice') || 'xiaoxiao'
      const res = await fetchWithTimeout('/api/voice/synthesize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: ttsText, voice: selectedVoice, card_id: get().currentCard?.id || '' }),
      })
      if (res.ok) {
        const blob = await res.blob()
        const audio_url = URL.createObjectURL(blob)
        set((s) => {
          const msgs = [...s.messages]
          if (msgs[charIdx]?.role === 'char') {
            msgs[charIdx] = { ...msgs[charIdx], audio_url }
          }
          return { messages: msgs }
        })
      }
    } catch {
      console.warn('[store] Voice synthesis failed, falling back to text-only')
    }
  },

  sendVoiceMessage: async (audioBlob) => {
    const form = new FormData()
    form.append('file', audioBlob, 'recording.webm')
    try {
      const res = await fetchWithTimeout('/api/voice/asr', { method: 'POST', body: form })
      if (!res.ok) throw new Error('语音识别失败')
      const data = await res.json()
      return data.text || ''
    } catch (err) {
      console.warn('[store] Voice message failed:', err)
      set({ error: '语音识别失败，请使用文字输入' })
      return ''
    }
  },

  loadTexts: async () => {
    set({ loading: true })
    try {
      const res = await fetchWithTimeout('/api/text/list')
      const data = await res.json()
      set({ texts: data, loading: false })
    } catch (err) {
      console.error('[store] loadTexts failed:', err)
      set({ error: err.message, loading: false })
    }
  },

  uploadProgress: null,
  setUploadProgress: (val) => set({ uploadProgress: val }),

  uploadText: async (file, title, description) => {
    const MAX_SIZE = 100 * 1024 * 1024 // 100MB
    if (file.size > MAX_SIZE) {
      set({ error: `文件过大（${(file.size / 1024 / 1024).toFixed(1)}MB），最大支持 100MB` })
      return
    }
    set({ uploadProgress: 0 })
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      const formData = new FormData()
      formData.append('file', file)
      formData.append('title', title || '')
      formData.append('description', description || '')

      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          set({ uploadProgress: Math.round((e.loaded / e.total) * 100) })
        }
      }

      xhr.onload = () => {
        set({ uploadProgress: null })
        if (xhr.status === 200) {
          const data = JSON.parse(xhr.responseText)
          get().loadTexts()
          resolve(data)
        } else {
          try {
            const errData = JSON.parse(xhr.responseText)
            reject(new Error(errData.detail || '上传失败'))
          } catch {
            reject(new Error(`上传失败 (${xhr.status})`))
          }
        }
      }

      xhr.onerror = () => {
        set({ uploadProgress: null })
        reject(new Error('网络错误'))
      }

      xhr.open('POST', '/api/text/upload')
      xhr.send(formData)
    })
  },

  deleteText: async (textId) => {
    try {
      await fetchWithTimeout(`/api/text/${textId}`, { method: 'DELETE' })
      set((s) => ({
        texts: s.texts.filter((t) => t.id !== textId),
        currentTextId: s.currentTextId === textId ? null : s.currentTextId,
      }))
    } catch (err) {
      console.error('[store] deleteText failed:', err)
      set({ error: err.message })
      throw err
    }
  },

  selectText: (textId) => {
    const text = get().texts.find((t) => t.id === textId)
    set({
      currentTextId: textId,
      currentView: 'character',
      currentTextTitle: text?.title || text?.filename || '',
      identifiedChars: [],
      currentCard: null,
      sessionId: null,
      messages: [],
    })
    get().loadCards(textId)
  },

  loadCards: async (textId) => {
    if (!textId) return
    try {
      const res = await fetchWithTimeout(`/api/distill/cards/by-text/${textId}`)
      const data = await res.json()
      set({ cards: data })
    } catch (err) {
      console.error('[store] loadCards failed:', err)
      set({ error: err.message })
    }
  },

  identifyCharacters: async (textId) => {
    set({ identifying: true, identifiedChars: [], error: null })
    try {
      const data = await postJSON('/api/distill/identify', { text_id: textId })
      const chars = data.characters || []
      set({ identifiedChars: chars, identifying: false })
      return chars
    } catch (err) {
      console.error('[store] identifyCharacters failed:', err)
      set({ error: err.message, identifying: false })
      throw err
    }
  },

  distillCharacter: (textId, characterName, force = false) => {
    set({ distilling: true, distillTokenCount: 0, distillStatus: '', distillIncrementalActive: false, error: null })

    const cancel = streamSSE(
      '/api/distill/run_stream',
      { text_id: textId, character_name: characterName, force },
      (token) => {
        set((s) => {
          return { distillIncrementalActive: false, distillTokenCount: s.distillTokenCount + token.length, distillStatus: '正在蒸馏…' }
        })
      },
      (payload) => {
        set((s) => {
          const card = { ...payload, id: payload.card_id }
          const exists = s.cards.some((c) => c.id === card.id)
          return {
            cards: exists ? s.cards : [card, ...s.cards],
            currentCard: card,
            sessionId: card.session_id,
            messages: card.first_message
              ? [{ role: 'char', content: card.first_message }]
              : [],
            distilling: false,
            distillTokenCount: 0,
            distillStatus: '',
            distillIncrementalActive: false,
          }
        })
      },
      (err) => {
        console.error('[store] distillCharacter stream failed:', err)
        set({ error: err.message, distilling: false, distillTokenCount: 0, distillStatus: '', distillIncrementalActive: false })
      },
      (payload) => {
        if (payload.status === 'compressing' && payload.current) {
          set({ distillStatus: `正在压缩第 ${payload.current}/${payload.total} 段…`, distillIncrementalActive: true })
          return
        }
        if (payload.status === 'analyzing' && payload.current) {
          set({ distillStatus: `正在分析第 ${payload.current}/${payload.total} 段…`, distillIncrementalActive: true })
          return
        }
        const statusMap = {
          identifying: '正在识别角色…',
        }
        set({ distillStatus: statusMap[payload.status] || payload.status })
      },
    )

    return cancel
  },

  selectCard: async (card) => {
    // TODO: 如果 card.session_id 存在，应从 /api/history/{session_id}/resume 加载历史消息，
    // 而非每次新建会话（当前行为：仅在 card 无 session_id 时创建新会话）。
    set({
      currentCard: card,
      messages: card.first_message ? [{ role: 'char', content: card.first_message }] : [],
      currentView: 'chat',
      resumeLoading: true,
    })

    let sessionId = card.session_id || null
    if (!sessionId && card.text_id) {
      try {
        const result = await postJSON('/api/distill/start_session', {
          text_id: card.text_id,
          card_id: card.id || card.card_id,
        }, 120000)
        sessionId = result.session_id
      } catch (err) {
        set({ error: err.message, resumeLoading: false })
        return
      }
    }
    set({ sessionId, resumeLoading: false })
    get().loadVoiceRef(card?.id || card?.card_id || null)
  },

  startChat: async (card) => {
    if (!card) {
      set({ currentView: 'chat' })
      return
    }

    const data = typeof card.card_json === 'string'
      ? JSON.parse(card.card_json)
      : card.card_json || card
    const name = data.name || card.name

    let sessionId = card.session_id || null

    if (!sessionId) {
      set({ sending: true })
      try {
        const cardId = card.id || card.card_id
        const result = await postJSON('/api/distill/start_session', {
          text_id: card.text_id,
          card_id: cardId,
        })
        sessionId = result.session_id
        set({ sending: false })
      } catch (err) {
        console.error('[store] startChat create session failed:', err)
        set({ error: err.message, sending: false })
        return
      }
    }

    const textTitle = card.text_id
      ? (get().texts.find((t) => t.id === card.text_id)?.title || get().currentTextTitle)
      : get().currentTextTitle

    set({
      currentCard: { ...card, session_id: sessionId },
      sessionId,
      messages: data.first_message
        ? [{ role: 'char', content: data.first_message }]
        : [],
      currentView: 'chat',
      currentTextTitle: textTitle || get().currentTextTitle,
    })
  },

  sendMessage: async (message) => {
    const { sessionId, messages, voiceEnabled, voiceRefInfo } = get()
    if (!sessionId || !message.trim()) return

    const userMsg = { role: 'user', content: message }
    set({ messages: [...messages, userMsg], sending: true, error: null })

    try {
      const data = await postJSON('/api/chat/send', {
        session_id: sessionId,
        message,
        user_role: get().userRole,
      })
      set((s) => {
        const msgs = [...s.messages]; msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], id: data.user_msg_id }; msgs.push({ role: 'char', content: data.reply, id: data.char_msg_id })
        if (data.summary) {
          msgs.splice(msgs.length - 2, 0, { role: 'summary', content: data.summary })
        }
        return { messages: msgs, sending: false }
      })

      if (voiceEnabled) {
        const { messages: currentMsgs } = get()
        get()._synthesizeVoiceReply(data.reply, currentMsgs.length - 1)
      }
    } catch (err) {
      console.error('[store] sendMessage failed:', err)
      set((s) => ({
        messages: [
          ...s.messages,
          { role: 'char', content: `[Error] ${err.message}` },
        ],
        sending: false,
        error: err.message,
      }))
    }
  },

  sendMessageStream: (message) => {
    const { sessionId, messages, voiceEnabled, voiceRefInfo } = get()
    if (!sessionId || !message.trim()) return () => {}

    const userMsg = { role: 'user', content: message }
    const charMsg = { role: 'char', content: '' }
    set({ messages: [...messages, userMsg, charMsg], sending: true, error: null })

    let fullReply = ''

    return streamSSE(
      '/api/chat/send',
      { session_id: sessionId, message, stream: true, user_role: get().userRole },
      (token) => {
        fullReply += token
        set((s) => {
          const msgs = [...s.messages]
          const last = msgs[msgs.length - 1]
          msgs[msgs.length - 1] = { ...last, content: last.content + token }
          return { messages: msgs }
        })
      },
      async (payload) => {
        set((s) => {
          const msgs = [...s.messages]
          if (payload.user_msg_id && msgs.length >= 2) {
            msgs[msgs.length - 2] = { ...msgs[msgs.length - 2], id: payload.user_msg_id }
          }
          if (payload.char_msg_id) {
            msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], id: payload.char_msg_id }
          }
          if (payload.summary) {
            const userIdx = msgs.length - 2
            msgs.splice(userIdx, 0, { role: 'summary', content: payload.summary })
          }
          return { messages: msgs, sending: false }
        })

        if (voiceEnabled && fullReply) {
          const { messages: currentMsgs } = get()
          get()._synthesizeVoiceReply(fullReply, currentMsgs.length - 1)
        }
      },
      (err) => {
        console.error('[store] stream failed:', err)
        set({ sending: false, error: err.message })
      },
    )
  },

  revokeMessage: async (index) => {
    const { sessionId, messages } = get()
    const msgId = messages[index]?.id
    if (!sessionId || msgId == null) return
    set({ messages: messages.slice(0, index), sending: false })
    try {
      await postJSON('/api/chat/revoke', {
        session_id: sessionId,
        message_id: msgId,
      })
    } catch (err) {
      console.error('[store] revokeMessage failed:', err)
      set({ error: err.message })
    }
  },

  resetChat: async () => {
    const { sessionId, currentCard } = get()
    if (!sessionId) return
    try {
      await postJSON('/api/chat/reset', { session_id: sessionId })
      set({
        messages: currentCard?.first_message
          ? [{ role: 'char', content: currentCard.first_message }]
          : [],
      })
    } catch (err) {
      console.error('[store] resetChat failed:', err)
      set({ error: err.message })
    }
  },

  resumeSession: async (sessionId) => {
    set({ resumeLoading: true })
    try {
      const data = await postJSON(`/api/history/${sessionId}/resume`, {})
      const session = data.session || {}
      const messages = (data.messages || []).map((m) => ({
        role: m.role,
        content: m.content,
        id: m.id,
      }))
      set({
        sessionId: session.id || sessionId,
        userRole: session.user_role || get().userRole,
        messages,
        currentCard: {
          id: session.card_id,
          name: session.character_name,
          session_id: session.id || sessionId,
        },
        currentView: 'chat',
        error: null,
        resumeLoading: false,
      })
    } catch (err) {
      console.error('[store] resumeSession failed:', err)
      set({ error: err.message, resumeLoading: false })
      throw err
    }
  },
}))

export default useAppStore
