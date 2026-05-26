import { create } from 'zustand'
import { postJSON, streamSSE, fetchWithTimeout, getToken, setToken, removeToken, setRefreshToken, removeAuth } from '../api/client'

const useAppStore = create((set, get) => ({
  // ---- Auth ----

  authUser: null,
  isLoggedIn: false,

  login: async (username, password) => {
    const data = await postJSON('/api/auth/login', { username, password })
    setToken(data.access_token)
    if (data.refresh_token) setRefreshToken(data.refresh_token)
    set({ authUser: data.user, isLoggedIn: true, currentView: 'home' })
  },

  register: async (username, password, inviteCode = '', email = '', code = '') => {
    const data = await postJSON('/api/auth/register', { username, password, invite_code: inviteCode, email, code })
    setToken(data.access_token)
    if (data.refresh_token) setRefreshToken(data.refresh_token)
    set({ authUser: data.user, isLoggedIn: true, currentView: 'home' })
  },

  logout: () => {
    // Best-effort server-side logout
    fetchWithTimeout('/api/auth/logout', { method: 'POST' }).catch(() => {})
    removeAuth()
    if (get()._chatAbort) get()._chatAbort.abort()
    set({
      authUser: null,
      isLoggedIn: false,
      currentView: 'home',
      texts: [],
      cards: [],
      standaloneCards: [],
      currentCard: null,
      sessionId: null,
      messages: [],
      currentTextId: null,
      currentTextTitle: '',
      identifiedChars: [],
    })
  },

  // ---- Navigation ----

  currentView: 'home',
  authorUserId: null,
  setAuthorUserId: (userId) => set({ authorUserId: userId }),
  currentTextDetailId: null,
  setCurrentTextDetailId: (id) => set({ currentTextDetailId: id }),
  currentMarketCardId: null,
  setCurrentMarketCardId: (id) => set({ currentMarketCardId: id }),
  messageTargetUserId: null,
  setMessageTargetUserId: (id) => set({ messageTargetUserId: id }),
  messageTargetUsername: null,
  setMessageTargetUsername: (name) => set({ messageTargetUsername: name }),
  setView: (view) => {
    const updates = { currentView: view, error: null }
    if (view === 'home' || view === 'text') updates.currentTextTitle = ''
    set(updates)
  },

  goBack: () => {
    const { currentView } = get()
    const backMap = { chat: 'character', character: 'text', text: 'home' }
    set({ currentView: backMap[currentView] || 'home' })
  },

  setResumeGroupId: (groupId) => set({ resumeGroupId: groupId }),

  texts: [],
  currentTextId: null,
  currentTextTitle: '',

  cards: [],
  standaloneCards: [],
  currentCard: null,
  sessionId: null,
  resumeGroupId: null,
  identifiedChars: [],
  distilling: false,
  distillTokenCount: 0,
  distillStatus: '',
  distillIncrementalActive: false,
  distillTasks: [],
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

  // Avatar sync — keyed by sessionId for per-conversation isolation
  cardAvatars: {},
  setCardAvatar: (key, dataUrl) => {
    set((state) => ({
      cardAvatars: { ...state.cardAvatars, [key]: dataUrl }
    }))
    localStorage.setItem(`avatar_${key}`, dataUrl)
  },
  loadCardAvatar: async (key) => {
    const saved = localStorage.getItem(`avatar_${key}`)
    if (saved) {
      set((state) => ({
        cardAvatars: { ...state.cardAvatars, [key]: saved }
      }))
      return saved
    }
    // Check if card data already has avatar_data
    const existing = get().cards.find(c => c.id === key || c.card_id === key)
    if (existing?.avatar_data) {
      set((state) => ({
        cardAvatars: { ...state.cardAvatars, [key]: existing.avatar_data }
      }))
      localStorage.setItem(`avatar_${key}`, existing.avatar_data)
      return existing.avatar_data
    }
    return null
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

  uploadRefAudio: (file, cardId, promptText, onProgress) => {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      const form = new FormData()
      form.append('file', file)
      form.append('card_id', cardId)
      form.append('prompt_text', promptText)

      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) {
          onProgress(Math.round((e.loaded / e.total) * 100))
        }
      }

      xhr.onload = async () => {
        if (xhr.status === 200) {
          await get().loadVoiceRef(cardId)
          resolve(JSON.parse(xhr.responseText))
        } else {
          try {
            const err = JSON.parse(xhr.responseText)
            reject(new Error(err.detail || '上传失败'))
          } catch {
            reject(new Error(`上传失败 (${xhr.status})`))
          }
        }
      }

      xhr.onerror = () => reject(new Error('网络错误'))

      xhr.open('POST', '/api/voice/ref-audio/upload')
      const token = getToken()
      if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)
      xhr.send(form)
    })
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
    await get().loadVoiceRef(cardId)
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

  uploadCustomVoice: (file, name, onProgress) => {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      const form = new FormData()
      form.append('file', file)
      form.append('name', name)

      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) {
          onProgress(Math.round((e.loaded / e.total) * 100))
        }
      }

      xhr.onload = () => {
        if (xhr.status === 200) {
          get().loadVoices()
          resolve(JSON.parse(xhr.responseText))
        } else {
          try {
            const err = JSON.parse(xhr.responseText)
            reject(new Error(err.detail || '上传失败'))
          } catch {
            reject(new Error(`上传失败 (${xhr.status})`))
          }
        }
      }

      xhr.onerror = () => reject(new Error('网络错误'))

      xhr.open('POST', '/api/voice/upload')
      const token = getToken()
      if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)
      xhr.send(form)
    })
  },

  deleteCustomVoice: async (voiceId) => {
    await fetchWithTimeout(`/api/voice/${voiceId}`, { method: 'DELETE' })
    await get().loadVoices()
  },

  setVoiceEnabled: (bool) => set({ voiceEnabled: bool }),
  setVoiceSpeed: (speed) => set({ voiceSpeed: speed }),

  webSearchEnabled: false,
  setWebSearchEnabled: (val) => set({ webSearchEnabled: val }),

  affinity: { affinity: 50, trust: 30, mood: '平静', guard: 70, reason: '' },
  affinityOpen: localStorage.getItem('affinity_open') !== 'false',
  setAffinityOpen: (val) => {
    localStorage.setItem('affinity_open', val ? 'true' : 'false')
    set({ affinityOpen: val })
  },
  affinityEnabled: localStorage.getItem('affinity_enabled') !== 'false',
  setAffinityEnabled: (val) => {
    localStorage.setItem('affinity_enabled', val ? 'true' : 'false')
    set({ affinityEnabled: val })
  },
  fetchAffinity: async () => {
    const { sessionId } = get()
    if (!sessionId) return
    try {
      const res = await fetchWithTimeout(`/api/chat/affinity/${sessionId}`)
      const data = await res.json()
      set({ affinity: data })
    } catch { /* non-fatal */ }
  },

  // Recording
  isRecording: false,
  recordingDuration: 0,

  userAvatar: null,
  setUserAvatar: (url) => set({ userAvatar: url }),

  userBanner: null,
  setUserBanner: (url) => set({ userBanner: url }),

  fetchUserBanner: async () => {
    try {
      const res = await fetchWithTimeout('/api/auth/banner')
      const data = await res.json()
      if (data.banner_data) set({ userBanner: data.banner_data })
    } catch {}
  },

  uploadUserBanner: async (base64) => {
    await fetchWithTimeout('/api/auth/banner', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ banner_data: base64 }),
    })
    set({ userBanner: base64 })
  },

  loadUserAvatar: async () => {
    try {
      const res = await fetchWithTimeout('/api/auth/avatar')
      const data = await res.json()
      if (data.avatar_data) {
        set({ userAvatar: data.avatar_data })
      }
    } catch { /* non-fatal */ }
  },

  saveUserAvatar: async (base64) => {
    const res = await fetchWithTimeout('/api/auth/avatar', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ avatar_data: base64 }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: '保存失败' }))
      throw new Error(err.detail || '保存失败')
    }
    return res.json()
  },

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

  uploadText: async (file, title, description, textType = 'story') => {
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
      formData.append('text_type', textType || 'story')

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
          if (xhr.status === 401) removeAuth()
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
      const token = getToken()
      if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)
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

  loadStandaloneCards: async () => {
    try {
      const res = await fetchWithTimeout('/api/distill/cards/standalone')
      const data = await res.json()
      set({ standaloneCards: data })
    } catch (err) {
      console.error('[store] loadStandaloneCards failed:', err)
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

  distillCharacter: async (textId, characterName, force = false) => {
    set({ error: null })
    try {
      const data = await postJSON('/api/distill/start', { text_id: textId, character_name: characterName, force })
      if (data.task_id) {
        get().addDistillTask(data.task_id, textId, characterName)
      } else {
        set({ error: '蒸馏启动失败' })
      }
    } catch (err) {
      set({ error: err.message })
    }
  },

  addDistillTask: (taskId, textId, characterName) => {
    const task = { id: taskId, textId, character: characterName, status: 'queued', progress_pct: 0 }
    set((s) => ({ distillTasks: [...s.distillTasks, task], distilling: true }))
    get()._persistTasks()

    let retryCount = 0
    const MAX_RETRIES = 3

    const poll = () => {
      fetchWithTimeout(`/api/distill/task/${taskId}`)
        .then((r) => r.json())
        .then((payload) => {
          retryCount = 0
          set((s) => ({
            distillTasks: s.distillTasks.map((t) =>
              t.id === taskId
                ? { ...t, status: payload.status, progress_pct: payload.progress_pct || 0, card_id: payload.card_id, message: payload.message }
                : t,
            ),
          }))
          get()._persistTasks()
          if (payload.status === 'done') {
            fetchWithTimeout(`/api/distill/cards/by-text/${textId}`)
              .then((r) => r.json())
              .then((cards) => {
                // 模糊匹配：名字包含或被包含
                const card = cards.find((c) => c.name === characterName)
                  || cards.find((c) => c.name?.includes(characterName) || characterName?.includes(c.name))
                  || cards[cards.length - 1]  // 兜底：取最新的一张
                if (card) {
                  set((s) => {
                    const cardId = card.id   // 后端返回的字段是 id，不是 card_id
                    const exists = s.cards.some((c) => c.id === cardId)
                    const freshCard = { ...card, text_id: textId }
                    const updatedCards = exists
                      ? s.cards.map((c) => c.id === cardId ? freshCard : c)
                      : [freshCard, ...s.cards]
                    return {
                      cards: updatedCards,
                      distilling: s.distillTasks.every((t) => t.id === taskId || t.status === 'done' || t.status === 'error')
                        ? false : true,
                    }
                  })
                }
              })
              .catch(() => {})
            setTimeout(() => get().removeDistillTask(taskId), 5000)
            return
          }
          if (payload.status === 'error') {
            set((s) => ({
              distilling: s.distillTasks.every((t) => t.id === taskId || t.status === 'done' || t.status === 'error')
                ? false : s.distilling,
            }))
            get()._persistTasks()
            return
          }
          setTimeout(poll, 3000)
        })
        .catch((err) => {
          const status = err?.status
          if (status === 404) {
            // 服务重启，任务丢失
            set((s) => ({
              distillTasks: s.distillTasks.map((t) =>
                t.id === taskId
                  ? { ...t, status: 'error', message: '服务已重启，任务丢失，请重新蒸馏' }
                  : t,
              ),
              distilling: false,
            }))
            get()._persistTasks()
            return
          }
          if (status === 403) {
            retryCount++
            if (retryCount >= MAX_RETRIES) {
              console.warn('[distill] 403 retry exhausted, marking task as failed')
              set((s) => ({
                distillTasks: s.distillTasks.map((t) =>
                  t.id === taskId
                    ? { ...t, status: 'error', message: '权限验证失败，请重新发起蒸馏' }
                    : t,
                ),
                distilling: false,
              }))
              get()._persistTasks()
              return
            }
            console.warn(`[distill] 403 on poll (${retryCount}/${MAX_RETRIES}), will retry after re-auth`)
            setTimeout(poll, 5000)
            return
          }
          // 网络错误等，继续重试
          setTimeout(poll, 3000)
        })
    }
    setTimeout(poll, 1000)
  },

  _persistTasks: () => {
    const tasks = get().distillTasks.map((t) => ({
      id: t.id, textId: t.textId, character: t.character, status: t.status,
    }))
    if (tasks.length > 0) {
      localStorage.setItem('distill_tasks', JSON.stringify(tasks))
    } else {
      localStorage.removeItem('distill_tasks')
    }
  },

  removeDistillTask: (taskId) => {
    set((s) => ({
      distillTasks: s.distillTasks.filter((t) => t.id !== taskId),
      distilling: s.distillTasks.length <= 1 ? false : s.distilling,
    }))
    get()._persistTasks()
  },

  restoreDistillTasks: () => {
    try {
      const saved = JSON.parse(localStorage.getItem('distill_tasks') || '[]')
      const active = saved.filter((t) => t.status !== 'done' && t.status !== 'error')
      if (active.length === 0) {
        localStorage.removeItem('distill_tasks')
        return
      }
      // 先显示 checking 状态，尝试从后端恢复
      set({ distillTasks: active.map(t => ({ ...t, status: 'checking' })), distilling: true })
      active.forEach((t) => {
        fetchWithTimeout(`/api/distill/task/${t.id}`)
          .then((r) => r.json())
          .then((payload) => {
            set((s) => ({
              distillTasks: s.distillTasks.map((task) =>
                task.id === t.id ? { ...task, ...payload } : task,
              ),
            }))
            get()._persistTasks()
            if (payload.status !== 'done' && payload.status !== 'error') {
              // 任务还在跑，只启动轮询，不重复添加
              const poll = () => {
                fetchWithTimeout(`/api/distill/task/${t.id}`)
                  .then(r => r.json())
                  .then(p => {
                    set((s) => ({
                      distillTasks: s.distillTasks.map(task =>
                        task.id === t.id ? { ...task, ...p } : task
                      ),
                    }))
                    get()._persistTasks()
                    if (p.status !== 'done' && p.status !== 'error') setTimeout(poll, 3000)
                  })
                  .catch(() => setTimeout(poll, 5000))
              }
              setTimeout(poll, 3000)
            }
          })
          .catch(() => {
            set((s) => ({
              distillTasks: s.distillTasks.map((task) =>
                task.id === t.id
                  ? { ...task, status: 'error', message: '服务已重启，请重新蒸馏' }
                  : task,
              ),
              distilling: false,
            }))
            get()._persistTasks()
          })
      })
    } catch { /* ignore */ }
  },

  viewCard: (card) => {
    set({
      currentCard: card,
      currentView: 'character',
      sessionId: null,
    })
  },

  // AbortController for in-flight start_session requests
  _chatAbort: null,

  selectCard: async (card) => {
    const state = get()
    // Reuse existing session if same card
    if (state.currentCard?.id === card.id && state.sessionId) {
      set({ currentView: 'chat' })
      return
    }

    // Cancel previous in-flight request
    if (state._chatAbort) state._chatAbort.abort()

    const abort = new AbortController()
    set({
      currentCard: card,
      messages: card.first_message ? [{ role: 'char', content: card.first_message }] : [],
      currentView: 'chat',
      resumeLoading: true,
      userAvatar: null,
      _chatAbort: abort,
    })

    let sessionId = card.session_id || null
    if (!sessionId && card.text_id) {
      try {
        const result = await postJSON('/api/distill/start_session', {
          text_id: card.text_id,
          card_id: card.id || card.card_id,
          user_role: get().userRole,
        }, 120000, abort.signal)
        sessionId = result.session_id
      } catch (err) {
        if (err.name === 'AbortError') return
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

    const state = get()
    // Reuse existing session if same card
    if (state.currentCard?.id === card.id && state.sessionId) {
      set({ currentView: 'chat' })
      return
    }

    // Cancel previous in-flight request
    if (state._chatAbort) state._chatAbort.abort()

    const data = typeof card.card_json === 'string'
      ? JSON.parse(card.card_json)
      : card.card_json || card

    // Optimistic UI: switch to chat view immediately
    const abort = new AbortController()
    set({
      currentCard: card,
      currentView: 'chat',
      sending: true,
      messages: [],
      userAvatar: null,
      _chatAbort: abort,
    })

    let sessionId = card.session_id || null
    try {
      if (!sessionId) {
        const cardId = card.id || card.card_id
        if (!cardId) {
          set({ error: '缺少角色信息，无法创建会话', sending: false })
          return
        }
        const result = await postJSON('/api/distill/start_session', {
          text_id: card.text_id || '',
          card_id: cardId,
          user_role: get().userRole,
        }, undefined, abort.signal)
        sessionId = result.session_id
        // Use dynamically generated opening line from API response
        if (result.first_message) {
          data.first_message = result.first_message
        }
      }
    } catch (err) {
      if (err.name === 'AbortError') return
      console.error('[store] startChat create session failed:', err)
      set({ error: err.message, sending: false })
      return
    }

    const textTitle = card.text_id
      ? (get().texts.find((t) => t.id === card.text_id)?.title || get().currentTextTitle)
      : get().currentTextTitle

    set({
      currentCard: { ...card, session_id: sessionId },
      sessionId,
      sending: false,
      messages: data.first_message
        ? [{ role: 'char', content: data.first_message }]
        : [],
      currentTextTitle: textTitle || get().currentTextTitle,
      userAvatar: null,
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
        web_search: get().webSearchEnabled,
        affinity_enabled: get().affinityEnabled,
      })
      set((s) => {
        const msgs = [...s.messages]; msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], id: data.user_msg_id }; msgs.push({ role: 'char', content: data.reply, id: data.char_msg_id, retracted: data.retracted || false })
        if (data.summary) {
          msgs.splice(msgs.length - 2, 0, { role: 'summary', content: data.summary })
        }
        return { messages: msgs, sending: false }
      })

      if (voiceEnabled) {
        const { messages: currentMsgs } = get()
        get()._synthesizeVoiceReply(data.reply, currentMsgs.length - 1)
      }

      get().fetchAffinity()
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
      { session_id: sessionId, message, stream: true, user_role: get().userRole, web_search: get().webSearchEnabled, voice_mode: voiceEnabled, affinity_enabled: get().affinityEnabled },
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
          if (payload.retracted) {
            msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], retracted: true }
          }
          return { messages: msgs, sending: false }
        })

        if (voiceEnabled && fullReply) {
          const { messages: currentMsgs } = get()
          get()._synthesizeVoiceReply(fullReply, currentMsgs.length - 1)
        }

        get().fetchAffinity()
      },
      (err) => {
        console.error('[store] stream failed:', err)
        set({ sending: false, error: err.message })
      },
    )
  },

  revokeCooldown: false,

  revokeMessage: async () => {
    const { sessionId, messages, revokeCooldown } = get()
    if (!sessionId || revokeCooldown) return

    // Find last user message
    let lastUserIdx = -1
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'user') { lastUserIdx = i; break }
    }
    if (lastUserIdx === -1) return

    const lastUserMsg = messages[lastUserIdx]
    const removeCount = (lastUserIdx + 1 < messages.length && messages[lastUserIdx + 1].role === 'char') ? 2 : 1

    set((s) => ({
      messages: [...s.messages.slice(0, lastUserIdx), ...s.messages.slice(lastUserIdx + removeCount)],
      sending: false,
      revokeCooldown: true,
    }))

    try {
      await postJSON('/api/chat/revoke', { session_id: sessionId, message_id: lastUserMsg.id })
    } catch (err) {
      console.error('[store] revokeMessage failed:', err)
      set({ error: err.message, revokeCooldown: false })
      return
    }

    setTimeout(() => set({ revokeCooldown: false }), 3000)

    // Trigger character reaction to revocation
    get()._sendRevokeNotice()
  },

  _sendRevokeNotice: () => {
    const { sessionId, voiceEnabled } = get()
    if (!sessionId) return () => {}

    const hiddenMsg = '[系统提示：对方刚刚撤回了一条消息]'
    const charMsg = { role: 'char', content: '' }
    set((s) => ({ messages: [...s.messages, charMsg], sending: true }))

    let fullReply = ''

    return streamSSE(
      '/api/chat/send',
      { session_id: sessionId, message: hiddenMsg, stream: true, hidden: true, user_role: get().userRole },
      (token) => {
        fullReply += token
        set((s) => {
          const msgs = [...s.messages]
          const last = msgs[msgs.length - 1]
          msgs[msgs.length - 1] = { ...last, content: last.content + token }
          return { messages: msgs }
        })
      },
      (payload) => {
        set((s) => {
          const msgs = [...s.messages]
          if (payload.char_msg_id) msgs[msgs.length - 1] = { ...msgs[msgs.length - 1], id: payload.char_msg_id }
          return { messages: msgs, sending: false }
        })
        if (voiceEnabled && fullReply) {
          get()._synthesizeVoiceReply(fullReply, get().messages.length - 1)
        }
      },
      (err) => {
        console.error('[store] revoke notice failed:', err)
        set({ sending: false, error: err.message })
      },
    )
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
        userRole: session.user_role ?? '',
        messages,
        currentCard: {
          id: session.card_id,
          name: session.character_name,
          session_id: session.id || sessionId,
          text_id: session.text_id,
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

  updateCard: async (cardId, cardJson) => {
    try {
      const res = await fetchWithTimeout(`/api/distill/card/${cardId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ card_json: cardJson }),
      })
      const data = await res.json()
      if (data.ok) {
        set((s) => {
          const updated = {
            cards: s.cards.map((c) =>
              c.id === cardId
                ? { ...c, card_json: typeof c.card_json === 'string' ? JSON.stringify(cardJson) : cardJson }
                : c
            ),
            currentCard: s.currentCard?.id === cardId
              ? { ...s.currentCard, ...cardJson, card_json: cardJson }
              : s.currentCard,
          }
          if (s.sessionId && s.currentCard?.id === cardId) {
            updated.messages = [...s.messages, { role: 'system', content: '角色卡已更新' }]
          }
          return updated
        })
      }
      return data
    } catch (err) {
      set({ error: err.message })
      throw err
    }
  },
}))

export default useAppStore
