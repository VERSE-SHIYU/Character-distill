import { create } from 'zustand'
import { postJSON, streamSSE, fetchWithTimeout } from '../api/client'

const useAppStore = create((set, get) => ({
  currentView: 'home',
  setView: (view) => set({ currentView: view }),

  texts: [],
  currentTextId: null,

  cards: [],
  currentCard: null,
  sessionId: null,
  identifiedChars: [],
  distilling: false,
  identifying: false,

  messages: [],
  sending: false,
  userRole: '',
  setUserRole: (role) => set({ userRole: role }),

  error: null,
  setError: (err) => set({ error: err }),

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

  uploadText: async (file) => {
    const form = new FormData()
    form.append('file', file)
    try {
      const res = await fetchWithTimeout('/api/text/upload', {
        method: 'POST',
        body: form,
      })
      const record = await res.json()
      set((s) => ({ texts: [record, ...s.texts], currentTextId: record.id }))
      return record
    } catch (err) {
      console.error('[store] uploadText failed:', err)
      set({ error: err.message })
      throw err
    }
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
    set({ currentTextId: textId, currentView: 'character' })
  },

  loadCards: async (textId) => {
    if (!textId) return
    try {
      const res = await fetchWithTimeout(`/api/distill/cards/${textId}`)
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

  distillCharacter: async (textId, characterName) => {
    set({ distilling: true, error: null })
    try {
      const card = await postJSON('/api/distill/run', {
        text_id: textId,
        character_name: characterName,
      })
      set((s) => {
        const exists = s.cards.some((c) => c.id === card.id)
        return {
          cards: exists ? s.cards : [card, ...s.cards],
          currentCard: card,
          sessionId: card.session_id,
          messages: card.first_message
            ? [{ role: 'char', content: card.first_message }]
            : [],
          distilling: false,
        }
      })
      return card
    } catch (err) {
      console.error('[store] distillCharacter failed:', err)
      set({ error: err.message, distilling: false })
      throw err
    }
  },

  selectCard: (card) => {
    set({
      currentCard: card,
      sessionId: card.session_id || null,
      messages: card.first_message
        ? [{ role: 'char', content: card.first_message }]
        : [],
    })
  },

  startChat: () => {
    set({ currentView: 'chat' })
  },

  sendMessage: async (message) => {
    const { sessionId, messages } = get()
    if (!sessionId || !message.trim()) return

    const userMsg = { role: 'user', content: message }
    set({ messages: [...messages, userMsg], sending: true, error: null })

    try {
      const data = await postJSON('/api/chat/send', {
        session_id: sessionId,
        message,
      })
      set((s) => ({
        messages: [...s.messages, { role: 'char', content: data.reply }],
        sending: false,
      }))
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
    const { sessionId, messages } = get()
    if (!sessionId || !message.trim()) return () => {}

    const userMsg = { role: 'user', content: message }
    const charMsg = { role: 'char', content: '' }
    set({ messages: [...messages, userMsg, charMsg], sending: true, error: null })

    return streamSSE(
      '/api/chat/send',
      { session_id: sessionId, message, stream: true },
      (token) => {
        set((s) => {
          const msgs = [...s.messages]
          const last = msgs[msgs.length - 1]
          msgs[msgs.length - 1] = { ...last, content: last.content + token }
          return { messages: msgs }
        })
      },
      () => set({ sending: false }),
      (err) => {
        console.error('[store] stream failed:', err)
        set({ sending: false, error: err.message })
      },
    )
  },

  revokeMessage: async (index) => {
    const { sessionId, messages } = get()
    if (!sessionId || index < 0 || index >= messages.length) return
    set({ messages: messages.slice(0, index) })
    try {
      await postJSON('/api/chat/revoke', {
        session_id: sessionId,
        message_id: index,
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
    try {
      const res = await fetchWithTimeout(`/api/history/${sessionId}`)
      const data = await res.json()
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
      })
    } catch (err) {
      console.error('[store] resumeSession failed:', err)
      set({ error: err.message })
      throw err
    }
  },
}))

export default useAppStore
