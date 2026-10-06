import { create } from 'zustand'
import { postJSON, streamSSE, fetchWithTimeout, getToken, setToken, removeToken, setRefreshToken, removeAuth } from '../api/client'
import { parseCardJson } from '../utils/card'
import { resolveOpeningMessages } from './openingMessage'
import { TERMS_VERSION, PRIVACY_VERSION } from '../legal/versions'
import { checkRepeat } from '../utils/repeatGuard'
import { applyFlushReport, pendingSaveKeys, withSaveResult } from '../utils/withSaveResult'
import { FALLBACK } from '../config/navigation'
import { scoped, bumpScope } from './scope'

let _cidSeq = 0
const withCid = (msg) => ({ ...msg, _cid: msg._cid ?? `m${++_cidSeq}` })

// ── 任务状态契约：前端归一化边界 ──────────────────────────────────────────────
// 服务端下发 done / actions / poll_after_ms（见后端 _task_affordances）。前端不复刻
// 任何业务规则：是否终态只读 done，能做什么只读 actions，多久后再问只读 poll_after_ms。
const DEFAULT_POLL_MS = 3000  // 服务端未给节奏时的兜底
const POLL_MIN_MS = 500       // 钳位下界：服务端给 0/负数而直接 setTimeout 会打成紧密循环
const POLL_MAX_MS = 30000     // 钳位上界：给超大值会让任务看起来卡死。上下界硬编码，不信服务端
// rollout shim，后端字段稳定发布满一个版本后删除：前端已部署而后端回滚、或用户加载了
// 缓存的旧 bundle 时 done 缺失，靠它仍能判终态，避免无限轮询。
const TERMINAL_FALLBACK = new Set(['done', 'error', 'interrupted'])
// 上传任务是独立域：不落库、无 status/actions/poll_after_ms 契约，节奏由前端固定。
const UPLOAD_POLL_MS = 500
// done 后任务在任务栏的展示停留时长 —— 不是轮询节奏。终态 poll_after_ms 恒为 0，
// 硬套会让完成态瞬间消失、用户来不及看到。
const DONE_LINGER_MS = 5000
// 「成功」这一支的 status 取值 —— 全 store 只此一处字面量。是否终态一律读 done；
// 这里只用来挑成功后的处理（苏醒台词 / 补卡 / 停留后移除），失败与中断统一走 isTerminal。
const STATUS_SUCCEEDED = 'done'

/** 服务端 / localStorage 任务对象 → store 的唯一入口。所有来源都必须先过这里。
 *
 *  入参门：缺 task_id 或 status 一律丢弃并 warn —— 这道门挡住 SSE 的 done_payload
 *  （{'done':true,'awakening':...}，有 done 无 status），也挡住将来任何形状不对的输入。
 *  形状不对返回 null，调用方跳过（不得整条塞进 store）。
 */
export function normalizeTask(payload) {
  if (!payload || typeof payload !== 'object' || !payload.task_id || !payload.status) {
    console.warn('[distill] normalizeTask 丢弃形状不符的任务对象:', payload)
    return null
  }
  // 终态：服务端 done 优先；缺失走重启兜底集合（见 TERMINAL_FALLBACK 注释）
  const done = payload.done ?? TERMINAL_FALLBACK.has(payload.status)
  // 缺省(undefined/null) → 兜底节奏；给了数值则强制钳位。钳位是安全要求：服务端给 0/负数
  // 而直接 setTimeout 会打成紧密循环，给超大值又会让任务看起来卡死。
  const ms = Number(payload.poll_after_ms ?? DEFAULT_POLL_MS)
  const poll_after_ms = Number.isFinite(ms)
    ? Math.min(Math.max(ms, POLL_MIN_MS), POLL_MAX_MS)
    : DEFAULT_POLL_MS
  // actions 非数组 → []；未知 token 保留在数组里（UI 不渲染即可），不抛、不丢整条
  const actions = Array.isArray(payload.actions) ? payload.actions : []
  return { ...payload, id: payload.id ?? payload.task_id, done, poll_after_ms, actions }
}

/** 终态判据 —— 唯一来源是归一化后的 done，不再看 status。 */
export const isTerminal = (task) => task?.done === true

/** 可用动作 —— 只认归一化后的 actions。UI 不得在 actions 之外自行推断。 */
export const taskActions = (task) => (Array.isArray(task?.actions) ? task.actions : [])

// 建会话请求体与 URL：只此一处（S3），三处建会话都走 startSessionBody。
const START_SESSION_URL = '/api/distill/start_session'

// 按卡偏好持久化：键名即 localStorage key，身份与阶段共用这一对（S10）。
function readPrefs(key) {
  try { return JSON.parse(localStorage.getItem(key) || '{}') } catch { return {} }
}

const useAppStore = create((set, get) => {
  // 结构性竞态防护：写会话/角色态数据的 async action 用 protect 包装，越界写自动丢弃。
  // guard 只存在 scoped 里，action 永不手写 `if (get().sessionId !== ...) return`。
  const protect = (fn) => scoped(fn, set, get)

  // 回复流（`sendMessageStream` / `_sendRevokeNotice`）共用的落字段方式：只改 `cid` 对应的
  // 那条气泡，不在当前列表里（会话换了 / 重载了）就一个字符不改。两条流各写一份的话，
  // 收尾帧那几段（user id / summary / retracted / 语音下标）很容易只改一处、漏另一处。
  const patchByCid = (cid, fn) => set((s) => {
    const idx = s.messages.findIndex((m) => m._cid === cid)
    if (idx === -1) return {}
    const msgs = [...s.messages]
    msgs[idx] = fn(msgs[idx])
    return { messages: msgs }
  })

  return {
  // ---- Auth ----

  authUser: null,
  isLoggedIn: false,

  login: async (username, password) => {
    const data = await postJSON('/api/auth/login', { username, password })
    setToken(data.access_token)
    if (data.refresh_token) setRefreshToken(data.refresh_token)
    set({ authUser: data.user, isLoggedIn: true, currentView: 'home' })
  },

  register: async (username, password, inviteCode = '', email = '', code = '', agreed = false) => {
    if (!agreed) throw new Error('请先同意用户协议与隐私政策')
    const data = await postJSON('/api/auth/register', {
      username, password, invite_code: inviteCode, email, code,
      agreed_terms_version: TERMS_VERSION,
      agreed_privacy_version: PRIVACY_VERSION,
    })
    setToken(data.access_token)
    if (data.refresh_token) setRefreshToken(data.refresh_token)
    set({ authUser: data.user, isLoggedIn: true, currentView: 'home', pendingCrossBorderConsent: true })
  },

  pendingCrossBorderConsent: false,
  grantCrossBorderConsent: () => set({ pendingCrossBorderConsent: false }),

  _clearNavState: () => {
    const keys = ['nav_view', 'nav_author_user_id', 'nav_text_detail_id', 'nav_market_card_id', 'nav_msg_target_user_id']
    keys.forEach((k) => localStorage.removeItem(k))
  },

  logout: () => {
    // Best-effort server-side logout
    fetchWithTimeout('/api/auth/logout', { method: 'POST' }).catch(() => {})
    removeAuth()
    if (get()._chatAbort) get()._chatAbort.abort()
    get()._clearNavState()
    set({
      authUser: null,
      isLoggedIn: false,
      currentView: 'home',
      viewHistory: [],
      texts: [],
      cards: [],
      standaloneCards: [],
      currentCard: null,
      sessionId: null,
      currentSessionAvatar: null,
      messages: [],
      currentTextId: null,
      currentTextTitle: '',
      identifiedChars: [],
    })
  },

  // ---- Navigation ----

  currentView: localStorage.getItem('nav_view') || 'home',
  viewHistory: [],
  chatSnapshot: null,     // { sessionId, messages, currentCard } for restoring chat after character list detour

  // Unified navigation: push current view onto stack, switch to new view,
  // and apply optional context (authorUserId, marketCardId, etc.)
  navigateTo: (view, context) => {
    const { currentView, viewHistory } = get()
    set({
      viewHistory: [...viewHistory, currentView],
      currentView: view,
      error: null,
    })
    localStorage.setItem('nav_view', view)

    // Apply context side-effects
    if (context) {
      if (context.authorUserId !== undefined) get().setAuthorUserId(context.authorUserId)
      if (context.marketCardId !== undefined) get().setCurrentMarketCardId(context.marketCardId)
      if (context.textDetailId !== undefined) get().setCurrentTextDetailId(context.textDetailId)
      if (context.messageTargetUserId !== undefined) get().setMessageTargetUserId(context.messageTargetUserId)
      if (context.messageTargetUsername !== undefined) get().setMessageTargetUsername(context.messageTargetUsername)
      if (context.groupId !== undefined) get().setResumeGroupId(context.groupId)
      if (context.readerTextId !== undefined) get().setReaderTextId(context.readerTextId)
    }
  },

  // Back: pop from history stack (with FALLBACK for empty stack)
  navigateBack: () => {
    get().popView()
  },

  // Restore a saved chat snapshot
  restoreChatSnapshot: () => {
    const snap = get().chatSnapshot
    if (snap?.sessionId) {
      set({
        currentView: 'chat',
        sessionId: snap.sessionId,
        messages: snap.messages || [],
        currentCard: snap.currentCard,
        chatSnapshot: null,
      })
      return true
    }
    return false
  },

  // Legacy push/pop stack — used internally by navigateTo, selectCard, startChat
  pushView: (view) => {
    const { currentView, viewHistory } = get()
    set({
      viewHistory: [...viewHistory, currentView],
      currentView: view,
      error: null,
    })
    localStorage.setItem('nav_view', view)
  },
  popView: () => {
    const { viewHistory } = get()
    if (viewHistory.length === 0) {
      const fb = FALLBACK[get().currentView] || 'home'
      set({ currentView: fb, error: null })
      localStorage.setItem('nav_view', fb)
      return
    }
    const prevView = viewHistory[viewHistory.length - 1]
    set({
      currentView: prevView,
      viewHistory: viewHistory.slice(0, -1),
      error: null,
    })
    localStorage.setItem('nav_view', prevView)
  },
  authorUserId: null,
  setAuthorUserId: (userId) => {
    set({ authorUserId: userId })
    if (userId) localStorage.setItem('nav_author_user_id', userId)
    else localStorage.removeItem('nav_author_user_id')
  },
  currentTextDetailId: null,
  setCurrentTextDetailId: (id) => {
    set({ currentTextDetailId: id })
    if (id) localStorage.setItem('nav_text_detail_id', id)
    else localStorage.removeItem('nav_text_detail_id')
  },
  currentMarketCardId: null,
  setCurrentMarketCardId: (id) => {
    set({ currentMarketCardId: id })
    if (id) localStorage.setItem('nav_market_card_id', id)
    else localStorage.removeItem('nav_market_card_id')
  },
  messageTargetUserId: null,
  setMessageTargetUserId: (id) => {
    set({ messageTargetUserId: id })
    if (id) localStorage.setItem('nav_msg_target_user_id', id)
    else localStorage.removeItem('nav_msg_target_user_id')
  },
  messageTargetUsername: null,
  setMessageTargetUsername: (name) => set({ messageTargetUsername: name }),
  // tab-level: reset stack for bottom-tab switches (home/text/market/mine/chat)
  setView: (view) => {
    const updates = { currentView: view, error: null, viewHistory: [] }
    if (view === 'home' || view === 'text') updates.currentTextTitle = ''
    set(updates)
    localStorage.setItem('nav_view', view)
  },

  setResumeGroupId: (groupId) => set({ resumeGroupId: groupId }),

  // ---- Conversation immersion ----

  inConversation: false,
  setInConversation: (val) => set({ inConversation: val }),

  // ---- Global unread count ----

  unreadTotal: 0,
  setUnreadTotal: (val) => set({ unreadTotal: val }),
  refreshUnread: async () => {
    try {
      const res = await fetchWithTimeout('/api/messages/unread-count')
      const data = await res.json()
      set({ unreadTotal: data.count ?? 0 })
    } catch {
      set({ unreadTotal: 0 })
    }
  },

  // Legal
  legalTab: 'terms',
  setLegalTab: (tab) => set({ legalTab: tab }),

  readerTextId: null,
  setReaderTextId: (id) => set({ readerTextId: id }),

  texts: [],
  textProgress: {},
  loadTextProgress: async () => {
    try {
      const res = await fetchWithTimeout('/api/text/reading-progress/all')
      const data = await res.json()
      const map = {}
      ;(Array.isArray(data) ? data : []).forEach((p) => { map[p.text_id] = p })
      set({ textProgress: map })
    } catch (err) {
      console.error('[store] loadTextProgress failed:', err)
    }
  },
  currentTextId: null,
  currentTextTitle: '',

  cards: [],
  standaloneCards: [],
  currentCard: null,
  sessionId: null,
  resumeGroupId: null,
  sessionList: [],
  sessionListLoading: false,
  identifiedChars: [],
  distilling: false,
  distillTokenCount: 0,
  distillStatus: '',
  distillIncrementalActive: false,
  distillTasks: [],
  lastDistilledCardId: null,
  awakeningToast: null,
  setAwakeningToast: (toast) => set({ awakeningToast: toast }),
  dismissAwakeningToast: () => set({ awakeningToast: null }),
  identifying: false,

  messages: [],
  loading: false,
  resumeLoading: false,
  sending: false,
  // 按卡偏好：{cardId: value}，持久化在 localStorage。身份与阶段共用这一对读写函数。
  userRolesByCard: readPrefs('user_roles_by_card'),
  arcPhasesByCard: readPrefs('arc_phases_by_card'),
  setCardPref: (key, cardId, value) => {
    if (!cardId) return
    const updated = { ...get()[key], [cardId]: value }
    localStorage.setItem(key, JSON.stringify(updated))
    set({ [key]: updated })
  },
  getCardPref: (key, cardId, legacyKey) => {
    if (!cardId) return undefined
    const map = get()[key]
    if (map[cardId] !== undefined) return map[cardId]
    // 旧的全站值只读一次就搬到按卡（只有身份有这一步），原值即焚。
    const legacy = legacyKey ? localStorage.getItem(legacyKey) : null
    if (legacy != null) {
      const updated = { ...map, [cardId]: legacy }
      localStorage.setItem(key, JSON.stringify(updated))
      localStorage.removeItem(legacyKey)
      set({ [key]: updated })
      return legacy
    }
    return undefined
  },
  setUserRole: (cardId, role) => get().setCardPref('userRolesByCard', cardId, role),
  getUserRole: (cardId) => get().getCardPref('userRolesByCard', cardId, 'user_role') || '',
  setArcPhase: (cardId, k) => get().setCardPref('arcPhasesByCard', cardId, k),
  getArcPhase: (cardId) => {
    const v = get().getCardPref('arcPhasesByCard', cardId)
    return v == null ? null : v
  },
  defaultArcPhase: (card) => {
    const phases = parseCardJson(card)?.character_arc?.phases || []
    return phases.length ? phases.length : null
  },
  /** 建会话请求体：只此一处（S3）。可按的卡带选中的（未选过用最后阶段）；不可按的不带。 */
  startSessionBody: (card) => {
    const cardId = card.id || card.card_id
    const chosen = get().getArcPhase(cardId)
    const arc = parseCardJson(card).character_arc
    return {
      text_id: card.text_id || '',
      card_id: cardId,
      user_role: get().getUserRole(cardId),
      // 不可按（起点不全 / 无指纹）的卡按最后阶段聊，前端不替它挑：带上去后端也只会
      // 归一成最后阶段，不如让「没选」在请求里就是 null（DA8）。
      arc_phase: arc?.selectable ? (chosen ?? get().defaultArcPhase(card)) : null,
    }
  },

  // 会话身份：当前活跃会话实际使用的身份与阶段。随会话走，不回写卡片偏好。
  // **赋值只走 applySessionIdentity**（S7）：新会话从卡片默认取，恢复存档从 session 取。
  sessionUserRole: '',
  sessionArcPhase: null,
  setSessionUserRole: (role) => set({ sessionUserRole: role }),
  applySessionIdentity: ({ user_role, arc_phase }) => set({
    sessionUserRole: user_role || '',
    sessionArcPhase: arc_phase ?? null,
  }),

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
    const token = localStorage.getItem('auth_token')
    if (!token) return // skip if not logged in
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
        } else if (xhr.status === 401) {
          removeAuth()
          window.dispatchEvent(new CustomEvent('auth:expired'))
          reject(new Error('请重新登录'))
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

  loadVoiceRef: protect(async (setScoped, get, cardId) => {
    if (!cardId) { setScoped({ voiceRefInfo: null }); return }
    try {
      const res = await fetchWithTimeout(`/api/voice/ref-audio/${cardId}`)
      const data = await res.json()
      setScoped({ voiceRefInfo: data })
    } catch {
      setScoped({ voiceRefInfo: null })
    }
  }),

  // 删除失败必须抛出（fetchWithTimeout 对非 2xx 抛 AppError），调用方负责显示。
  // 非乐观：本地状态只在 DELETE 成功后向服务端重拉，失败时无需回滚。
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
        } else if (xhr.status === 401) {
          removeAuth()
          window.dispatchEvent(new CustomEvent('auth:expired'))
          reject(new Error('请重新登录'))
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

  // 同 deleteVoiceRef：失败抛出、非乐观、无需回滚。
  deleteCustomVoice: async (voiceId) => {
    await fetchWithTimeout(`/api/voice/${voiceId}`, { method: 'DELETE' })
    await get().loadVoices()
  },

  setVoiceEnabled: (bool) => set({ voiceEnabled: bool }),
  setVoiceSpeed: (speed) => set({ voiceSpeed: speed }),

  webSearchEnabled: false,
  setWebSearchEnabled: (val) => set({ webSearchEnabled: val }),

  agentMode: true,
  setAgentMode: (val) => set({ agentMode: val }),

  // affinity: null 表达"无数据"（后端 204）。有真实数据时为 10 字段规范 dict。
  affinity: null,
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
  fetchAffinity: protect(async (setScoped, get) => {
    const { sessionId } = get()
    if (!sessionId) return
    try {
      const res = await fetchWithTimeout(`/api/chat/affinity/${sessionId}`)
      // 204 = 无已评估数据 → affinity:null（与后端契约一致，不吞也不造假）
      const data = res.status === 204 ? null : await res.json()
      setScoped({ affinity: data })
    } catch (err) {
      if (err?.status !== 401) console.warn('[affinity]', err)
      setScoped({ affinity: null })
    }
  }),

  resetAffinity: () => set({ affinity: null }),

  // Recording
  isRecording: false,
  recordingDuration: 0,

  userAvatar: null,
  setUserAvatar: (url) => set({ userAvatar: url }),

  currentSessionAvatar: null,
  setCurrentSessionAvatar: (url) => set({ currentSessionAvatar: url }),

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

  updateNickname: async (newNickname) => {
    const res = await fetchWithTimeout('/api/auth/nickname', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nickname: newNickname }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: '保存失败' }))
      throw new Error(err.detail || '保存失败')
    }
    const data = await res.json()
    set((s) => ({
      authUser: s.authUser ? { ...s.authUser, nickname: data.nickname } : null,
    }))
    return data
  },

  _synthesizeVoiceReply: protect(async (setScoped, get, reply, charIdx) => {
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
        setScoped((s) => {
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
  }),

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

  uploadTaskProgress: null,
  setUploadTaskProgress: (val) => set({ uploadTaskProgress: val }),

  uploadText: async (file, title, description, textType = 'story') => {
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

          // Start polling upload task if present (story/classic)
          const uploadTaskId = data.upload_task_id
          if (uploadTaskId) {
            const poll = () => {
              fetchWithTimeout(`/api/text/upload-task/${uploadTaskId}`)
                .then((r) => r.json())
                .then((task) => {
                  if (task.status === 'done' || task.status === 'error') {
                    set({ uploadTaskProgress: null })
                    get().loadTexts()
                  } else {
                    set({ uploadTaskProgress: task })
                    setTimeout(poll, UPLOAD_POLL_MS)
                  }
                })
                .catch(() => {
                  set({ uploadTaskProgress: null })
                })
            }
            poll()
          }

          resolve(data)
        } else {
          if (xhr.status === 401) {
            removeAuth()
            window.dispatchEvent(new CustomEvent('auth:expired'))
          }
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

  deleteText: async (textId, keep_cards = false) => {
    try {
      await fetchWithTimeout(`/api/text/${textId}?keep_cards=${keep_cards}`, { method: 'DELETE' })
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
    get()._cancelChatStream()
    const text = get().texts.find((t) => t.id === textId)
    set({
      currentTextId: textId,
      currentTextTitle: text?.title || text?.filename || '',
      identifiedChars: [],
      currentCard: null,
      sessionId: null,
      messages: [],
    })
    get().pushView('character')
    return get().loadCards(textId)
  },

  openCharacterList: (textId) => {
    const { sessionId, messages, currentCard } = get()
    const text = get().texts.find((t) => t.id === textId)
    // Save chat snapshot before switching to character view
    if (sessionId) {
      set({ chatSnapshot: { sessionId, messages, currentCard } })
    }
    // Push character onto view stack so back returns to chat
    get().pushView('character')
    set({
      currentTextId: textId,
      currentTextTitle: text?.title || text?.filename || '',
      identifiedChars: [],
    })
    get().loadCards(textId)
  },

  loadCards: protect(async (setScoped, get, textId) => {
    if (!textId) return
    try {
      const res = await fetchWithTimeout(`/api/distill/cards/by-text/${textId}`)
      const data = await res.json()
      setScoped({ cards: data })
    } catch (err) {
      console.error('[store] loadCards failed:', err)
      setScoped({ error: err.message })
    }
  }),

  loadStandaloneCards: async () => {
    try {
      const res = await fetchWithTimeout('/api/distill/cards/standalone')
      const data = await res.json()
      set({ standaloneCards: data })
    } catch (err) {
      console.error('[store] loadStandaloneCards failed:', err)
    }
  },

  identifyCharacters: protect(async (setScoped, get, textId) => {
    setScoped({ identifying: true, identifiedChars: [], error: null })
    try {
      const data = await postJSON('/api/distill/identify', { text_id: textId })
      const chars = data.characters || []
      setScoped({ identifiedChars: chars, identifying: false })
      return chars
    } catch (err) {
      console.error('[store] identifyCharacters failed:', err)
      setScoped({ error: err.message, identifying: false })
      throw err
    }
  }),

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
    // 本地乐观种子：形状对齐契约（过 normalizeTask 门），actions 与「刚启动的 running
    // 任务」一致，让首响应到达前也有可取消按钮；下一拍即被服务端真值覆盖。
    const task = normalizeTask({
      task_id: taskId, id: taskId, textId, character: characterName,
      status: 'running', progress_pct: 0, actions: ['cancel'],
    })
    set((s) => ({ distillTasks: [...s.distillTasks, task], distilling: true }))
    get()._persistTasks()

    let retryCount = 0
    const MAX_RETRIES = 3

    const poll = () => {
      fetchWithTimeout(`/api/distill/task/${taskId}`)
        .then((r) => r.json())
        .then((raw) => {
          const payload = normalizeTask(raw)
          if (!payload) {
            // 形状不符：丢弃并停止轮询（不排下一次，否则无限打一个坏端点）
            set((s) => ({ distillTasks: s.distillTasks.filter((t) => t.id !== taskId) }))
            get()._persistTasks()
            return
          }
          retryCount = 0
          set((s) => ({
            distillTasks: s.distillTasks.map((t) =>
              t.id === taskId
                ? { ...t, ...payload, progress_pct: Math.max(t.progress_pct ?? 0, payload.progress_pct ?? t.progress_pct ?? 0) }
                : t,
            ),
          }))
          get()._persistTasks()
          if (payload.status === STATUS_SUCCEEDED) {
            // Show awakening toast (first done transition, once per task)
            if (payload.awakening) {
              set({ awakeningToast: {
                character: characterName,
                awakening: payload.awakening,
                card_id: payload.card_id || '',
                textId,
              }})
            }
            // only refresh cards when user is viewing this text, else leave it to selectText/loadCards
            const s = get()
            set((s2) => ({
              distilling: s2.distillTasks.every((t) => isTerminal(t))
                ? false : s2.distilling,
              currentTextId: s2.currentTextId || textId,
            }))
            if (payload.card_id) {
              if (s.currentTextId === textId) {
                fetchWithTimeout(`/api/distill/cards/by-text/${textId}`)
                  .then((r) => r.json())
                  .then((cards) => {
                    set({ cards, lastDistilledCardId: payload.card_id })
                  })
                  .catch((err) => console.warn('[distill] Failed to refresh cards on done:', err))
              }
            } else {
              fetchWithTimeout(`/api/distill/cards/by-text/${textId}`)
                .then((r) => r.json())
                .then((cards) => {
                  const card = cards.find((c) => c.name === characterName)
                    || cards.find((c) => c.name?.includes(characterName) || characterName?.includes(c.name))
                    || cards[cards.length - 1]
                  if (card) {
                    const cur = get()
                    if (cur.currentTextId === textId) {
                      set((s2) => {
                        const cardId = card.id
                        const exists = s2.cards.some((c) => c.id === cardId)
                        const freshCard = { ...card, text_id: textId }
                        return {
                          cards: exists
                            ? s2.cards.map((c) => c.id === cardId ? freshCard : c)
                            : [freshCard, ...s2.cards],
                          lastDistilledCardId: cardId,
                        }
                      })
                    }
                  } else {
                    console.warn(`[distill] Card not found by name matching: ${characterName}, cards=`, cards)
                  }
                })
                .catch((err) => console.warn('[distill] Failed to fetch cards for fallback name matching:', err))
            }
            setTimeout(() => get().removeDistillTask(taskId), DONE_LINGER_MS)
            return
          }
          if (isTerminal(payload)) {
            // error / interrupted：终态 → 停止轮询，但保留在列表（interrupted 由动作按钮续跑）
            set((s) => ({
              distilling: s.distillTasks.every((t) => t.id === taskId || isTerminal(t))
                ? false : s.distilling,
            }))
            get()._persistTasks()
            return
          }
          setTimeout(poll, payload.poll_after_ms)
        })
        .catch((err) => {
          const status = err?.status
          if (status === 404) {
            // 任务不存在，或本机存着的任务不属于当前账号——非属主与不存在在后端同判 404
            // （403 会让人靠状态码枚举出 task_id 存在）。文本被硬删也走这里。开机 reconcile
            // 后，重启的任务在 DB 里是 interrupted 而非消失。不得标 error 后留在列表里。
            set((s) => ({ distillTasks: s.distillTasks.filter((t) => t.id !== taskId) }))
            get()._persistTasks()
            return
          }
          if (status === 403) {
            // 后端只剩「账号已被禁用」会在此路径返 403（属主拒绝已改 404）。等重登拿新
            // token；连续失败则标 error 让用户重新发起。
            retryCount++
            if (retryCount >= MAX_RETRIES) {
              console.warn('[distill] 403 retry exhausted, marking task as failed')
              set((s) => ({
                distillTasks: s.distillTasks.map((t) =>
                  t.id === taskId
                    ? normalizeTask({ ...t, status: 'error', message: '权限验证失败，请重新发起蒸馏' })
                    : t,
                ),
                distilling: false,
              }))
              get()._persistTasks()
              return
            }
            console.warn(`[distill] 403 on poll (${retryCount}/${MAX_RETRIES}), will retry after re-auth`)
            setTimeout(poll, DEFAULT_POLL_MS)
            return
          }
          // 网络错误等，继续重试
          setTimeout(poll, DEFAULT_POLL_MS)
        })
    }
    setTimeout(poll, DEFAULT_POLL_MS)
  },

  _persistTasks: () => {
    const tasks = get().distillTasks.map((t) => ({
      id: t.id, task_id: t.task_id ?? t.id,
      textId: t.textId, character: t.character, status: t.status,
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

  // 取消蒸馏 = 服务端动作（actions 含 cancel 才发 DELETE）+ 本地移出列表。
  // DELETE 成功才移出；失败保持任务原样并把 detail 抛给调用方渲染 —— 不许静默吞（106）。
  // 无 cancel 动作（如已终结、或刚建还没拿到第一次响应）纯本地移除，不发请求。
  cancelDistillTask: async (task) => {
    if (taskActions(task).includes('cancel')) {
      await fetchWithTimeout(`/api/distill/task/${task.id}`, { method: 'DELETE' })
    }
    get().removeDistillTask(task.id)
  },

  setLastDistilledCardId: (id) => set({ lastDistilledCardId: id }),

  restoreDistillTasks: () => {
    try {
      const savedRaw = JSON.parse(localStorage.getItem('distill_tasks') || '[]')
      // 旧形状兼容：本改动前写入的是 {id, textId, character, status}，入口门要求 task_id
      // —— 在这一处边界补上再归一化，旧记录因此仍能被恢复、被判终态。
      const saved = savedRaw
        .map((t) => normalizeTask({ ...t, task_id: t.task_id ?? t.id }))
        .filter(Boolean)
      const active = saved.filter((t) => !isTerminal(t))
      if (active.length === 0) {
        localStorage.removeItem('distill_tasks')
        return
      }
      // 先显示 checking 状态，尝试从后端恢复
      set({ distillTasks: active.map(t => ({ ...t, status: 'checking' })), distilling: true })
      active.forEach((t) => {
        fetchWithTimeout(`/api/distill/task/${t.task_id}`)
          .then((r) => r.json())
          .then((raw) => {
            const payload = normalizeTask(raw)
            if (!payload) {
              set((s) => ({ distillTasks: s.distillTasks.filter((task) => task.id !== t.id) }))
              get()._persistTasks()
              return
            }
            set((s) => ({
              distillTasks: s.distillTasks.map((task) =>
                task.id === t.id ? { ...task, ...payload } : task,
              ),
            }))
            get()._persistTasks()
            if (!isTerminal(payload)) {
              // 任务还在跑，只启动轮询，不重复添加
              const poll = () => {
                fetchWithTimeout(`/api/distill/task/${t.task_id}`)
                  .then(r => r.json())
                  .then(raw2 => {
                    const p = normalizeTask(raw2)
                    if (!p) {
                      set((s) => ({ distillTasks: s.distillTasks.filter((task) => task.id !== t.id) }))
                      get()._persistTasks()
                      return
                    }
                    set((s) => ({
                      distillTasks: s.distillTasks.map(task =>
                        task.id === t.id ? { ...task, ...p } : task
                      ),
                    }))
                    get()._persistTasks()
                    if (!isTerminal(p)) setTimeout(poll, p.poll_after_ms)
                  })
                  .catch(() => setTimeout(poll, DEFAULT_POLL_MS))
              }
              setTimeout(poll, payload.poll_after_ms)
            }
          })
          .catch((err) => {
            if (err?.status === 404) {
              // 行已不存在（文本被硬删）→ 从列表移除并停止轮询，不标 error 后留着
              set((s) => {
                const distillTasks = s.distillTasks.filter((task) => task.id !== t.id)
                return { distillTasks, distilling: distillTasks.some((task) => !isTerminal(task)) }
              })
              get()._persistTasks()
              return
            }
            set((s) => ({
              distillTasks: s.distillTasks.map((task) =>
                task.id === t.id
                  ? normalizeTask({ ...task, status: 'error', message: '状态查询失败，请重试' })
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
    get()._cancelChatStream()
    set({
      currentCard: card,
      sessionId: null,
    })
  },

  // AbortController for in-flight start_session requests
  _chatAbort: null,
  // 在途回复流：`{ cid, cancel }`。cid 是它要写的那条角色气泡。流只改自己这条气泡、
  // 只按自己的归属解锁「发送中」—— 判据不是 sessionId：切会话时 sessionId 先换掉，
  // 收尾帧若按 sessionId 认领就会早退，「发送中」永远停在 true，输入框 `disabled` 卡死。
  _chatStream: null,

  // 放下在途流 —— **唯一**入口。先同步交出所有权（清 `_chatStream`、解锁 `sending`）再
  // abort：abort 引发的收尾帧是异步到的（`api/client.js` 的 AbortError 分支），先 abort
  // 再清的话，迟到的回调会撞上刚接手的新流（或新会话）并把它误解锁 / 改错气泡。
  _cancelChatStream: () => {
    const stream = get()._chatStream
    if (!stream) return
    set({ _chatStream: null, sending: false })
    stream.cancel()
  },

  // Archive list modal (multi-save slot selection)
  archiveModalOpen: false,
  archiveList: [],
  pendingCard: null,
  _pendingChatCardId: null,

  selectCard: async (card) => {
    const state = get()
    const _selId = card.id || card.card_id
    if (state._pendingChatCardId === _selId) return
    set({ _pendingChatCardId: _selId })

    get().pushView('chat')
    if (state.lastDistilledCardId === card.id) {
      set({ lastDistilledCardId: null })
    }
    // Reuse existing session if same card
    if (state.currentCard?.id === card.id && state.sessionId) {
      set({ currentView: 'chat', _pendingChatCardId: null })
      return
    }

    // Cancel previous in-flight request + stream
    if (state._chatAbort) state._chatAbort.abort()
    get()._cancelChatStream()

    const abort = new AbortController()

    set({
      currentCard: card,
      messages: [],
      currentView: 'chat',
      resumeLoading: true,
      userAvatar: null,
      _chatAbort: abort,
    })

    let sessionId = card.session_id || null
    if (!sessionId && card.text_id) {
      try {
        const result = await postJSON(START_SESSION_URL, get().startSessionBody(card), 120000, abort.signal)

        sessionId = result.session_id
      } catch (err) {
        if (err.name === 'AbortError' || err.status === 408) { set({ _pendingChatCardId: null }); return }
        set({ _pendingChatCardId: null, error: err.message, resumeLoading: false })
        return
      }
    }
    const _cardId = card.id || card.card_id
    set({ _pendingChatCardId: null, sessionId, resumeLoading: false })
    get().applySessionIdentity({
      user_role: get().getUserRole(_cardId),
      arc_phase: get().getArcPhase(_cardId) ?? get().defaultArcPhase(card),
    })
    get().loadVoiceRef(_cardId)
  },

  startChat: async (card) => {
    if (!card) {
      set({ _pendingChatCardId: null, currentView: 'chat' })
      return
    }

    const state = get()
    // Idempotency guard: prevent duplicate calls for the same card
    // (covers the race where ChatArea's auto-recovery effect fires
    //  while an archive-check is already in-flight or has returned early)
    if (state._pendingChatCardId === card.id) return
    set({ _pendingChatCardId: card.id })

    get().pushView('chat')
    // Reuse existing session if same card
    if (state.currentCard?.id === card.id && state.sessionId) {
      set({ _pendingChatCardId: null, currentView: 'chat' })
      return
    }

    // Cancel previous in-flight request + stream
    if (state._chatAbort) state._chatAbort.abort()
    get()._cancelChatStream()

    const data = parseCardJson(card)
    const cardId = card.id || card.card_id

    // Check for existing archives before entering chat
    try {
      const archiveRes = await fetchWithTimeout(`/api/history/list?card_id=${encodeURIComponent(cardId)}&page_size=50`)
      const archiveData = await archiveRes.json()
      if (archiveData.total > 0) {
        set({
          _pendingChatCardId: null,
          archiveModalOpen: true,
          archiveList: archiveData.items,
          pendingCard: card,
        })
        return
      }
    } catch (err) {
      console.warn('[store] Failed to fetch archives, falling through to new session:', err)
    }

    // Optimistic UI: switch to chat view immediately
    const abort = new AbortController()

    set({
      currentCard: card,
      currentView: 'chat',
      sending: true,
      messages: [],
      currentSessionAvatar: null,
      userAvatar: null,
      _chatAbort: abort,
    })

    let sessionId = card.session_id || null
    let backendFirstMessage = null
    let backendFirstMessageSave = null
    let sessionBody = null
    try {
      if (!sessionId) {
        if (!cardId) {
          set({ _pendingChatCardId: null, error: '缺少角色信息，无法创建会话', sending: false })
          return
        }

        sessionBody = get().startSessionBody(card)
        const result = await postJSON(START_SESSION_URL, sessionBody, undefined, abort.signal)

        sessionId = result.session_id
        backendFirstMessage = result?.first_message
        backendFirstMessageSave = result?.first_message_save
      }
      // when card.session_id already exists, backend stays null
      // and the opening line will come from history loading instead
    } catch (err) {
      if (err.name === 'AbortError' || err.status === 408) return
      console.error('[store] startChat create session failed:', err)
      set({ _pendingChatCardId: null, error: err.message, sending: false })
      return
    }

    const textTitle = card.text_id
      ? (get().texts.find((t) => t.id === card.text_id)?.title || get().currentTextTitle)
      : get().currentTextTitle

    // 开场白是后端写的第一笔：写失败时它也带 `save`，标上「未保存」等后续补写认领。
    // `save` 缺省（写成功了 / 走到本地兜底那条）时 withSaveResult 原样返回。
    const _startChatMsgs = resolveOpeningMessages(
      { backendFirstMessage, cardFirstMessage: data.first_message },
      withCid,
    ).map((m) => withSaveResult(m, backendFirstMessageSave))

    set({
      _pendingChatCardId: null,
      currentCard: { ...card, session_id: sessionId },
      sessionId,
      currentSessionAvatar: null,
      sending: false,
      messages: _startChatMsgs,
      currentTextTitle: textTitle || get().currentTextTitle,
      userAvatar: null,
    })
    get().applySessionIdentity(sessionBody ?? {
      user_role: get().getUserRole(cardId),
      arc_phase: get().getArcPhase(cardId) ?? get().defaultArcPhase(card),
    })
    get().resetAffinity()
    get().fetchAffinity()
  },

  enterArchive: async (session) => {
    const { pendingCard } = get()
    if (!pendingCard) return
    const card = pendingCard
    const cardId = card.id || card.card_id
    if (get()._pendingChatCardId === cardId) return
    set({ _pendingChatCardId: cardId })

    const data = parseCardJson(card)

    const _enterMsgs = resolveOpeningMessages(
      { sessionLastMessage: session.last_message, cardFirstMessage: data.first_message },
      withCid,
    )

    set({
      archiveModalOpen: false,
      archiveList: [],
      pendingCard: null,
      _pendingChatCardId: null,
      currentCard: { ...card, session_id: session.id },
      sessionId: session.id,
      currentView: 'chat',
      sending: false,
      messages: _enterMsgs,
      currentSessionAvatar: session.avatar_data ?? null,
      userAvatar: null,
      error: null,
    })
    get().applySessionIdentity({
      user_role: session.user_role || get().getUserRole(session.card_id),
      arc_phase: session.arc_phase ?? null,
    })
    get().resetAffinity()
    get().fetchAffinity()
  },

  createNewArchive: async () => {
    const { pendingCard } = get()
    if (!pendingCard) return
    const card = pendingCard
    const cardId = card.id || card.card_id
    if (get()._pendingChatCardId === cardId) return
    set({ _pendingChatCardId: cardId })

    const data = parseCardJson(card)

    if (!cardId) {
      set({ _pendingChatCardId: null, error: '缺少角色信息，无法创建会话', archiveModalOpen: false, pendingCard: null })
      return
    }


    set({
      archiveModalOpen: false,
      archiveList: [],
      pendingCard: null,
      currentCard: card,
      currentView: 'chat',
      sending: true,
      messages: [],
      currentSessionAvatar: null,
      userAvatar: null,
    })


    try {
      const sessionBody = get().startSessionBody(card)
      const result = await postJSON(START_SESSION_URL, sessionBody)

      const sessionId = result.session_id
      const textTitle = card.text_id
        ? (get().texts.find((t) => t.id === card.text_id)?.title || get().currentTextTitle)
        : get().currentTextTitle

      const _newArchiveMsgs = resolveOpeningMessages(
        { backendFirstMessage: result?.first_message, cardFirstMessage: data.first_message },
        withCid,
      )

      set({
        currentCard: { ...card, session_id: sessionId },
        sessionId,
        sending: false,
        _pendingChatCardId: null,
        messages: _newArchiveMsgs,
        currentTextTitle: textTitle || get().currentTextTitle,
      })
      get().applySessionIdentity(sessionBody)
      get().resetAffinity()
      get().fetchAffinity()
    } catch (err) {
      console.error('[store] createNewArchive failed:', err)
      set({ _pendingChatCardId: null, error: err.message, sending: false })
    }
  },

  closeArchiveModal: () => set({
    archiveModalOpen: false,
    archiveList: [],
    pendingCard: null,
    _pendingChatCardId: null,
  }),

  sendMessage: protect(async (setScoped, get, message) => {
    const { sessionId, messages, voiceEnabled, voiceRefInfo } = get()
    if (!sessionId || !message.trim()) return

    const userMsg = withCid({ role: 'user', content: message })
    setScoped({ messages: [...messages, userMsg], sending: true, error: null })

    try {
      const data = await postJSON('/api/chat/send', {
        session_id: sessionId,
        message,
        user_role: get().sessionUserRole,
        web_search: get().webSearchEnabled,
        agent_mode: get().agentMode,
        affinity_enabled: get().affinityEnabled,
      })
      setScoped((s) => {
        const msgs = [...s.messages]
        // `?? prev.id`：存失败时后端给 null，别把 id 抹成 null（_cid 保住了 React key，
        // 但撤回/反应都拿 msg.id 当参数，抹掉就是真的丢了）。
        const prevUser = msgs[msgs.length - 1]
        msgs[msgs.length - 1] = withSaveResult(
          { ...prevUser, id: data.user_msg_id ?? prevUser.id, timestamp: data.user_created_at },
          data.user_save,
        )
        msgs.push(withSaveResult(withCid({
          role: 'char', content: data.reply, id: data.char_msg_id,
          retracted: data.retracted || false, timestamp: data.char_created_at,
        }), data.char_save))
        if (data.summary) {
          msgs.splice(msgs.length - 2, 0, withCid({ role: 'summary', content: data.summary }))
        }
        // 这一轮里每一次写都会顺带补写队头，读数（flushed/dropped）随响应体一起来 ——
        // 先前的「未保存」据此填回真 id 或翻成「保存失败」。
        return { messages: applyFlushReport(msgs, data), sending: false }
      })

      if (voiceEnabled) {
        const { messages: currentMsgs } = get()
        get()._synthesizeVoiceReply(data.reply, currentMsgs.length - 1)
      }

      get().fetchAffinity()
    } catch (err) {
      console.error('[store] sendMessage failed:', err)
      setScoped((s) => ({
        messages: [
          ...s.messages,
          withCid({ role: 'char', content: `[Error] ${err.message}` }),
        ],
        sending: false,
        error: err.message,
      }))
    }
  }),

  sendMessageStream: (message, reply_to_id = null, reply_to_preview = '') => {
    const { sessionId, messages, voiceEnabled, voiceRefInfo } = get()
    if (!sessionId || !message.trim()) return () => {}

    // ★ 重复消息拦截
    const { blocked, message: blockMsg } = checkRepeat(message, messages)
    if (blocked) {
      set({ error: blockMsg })
      return () => {}
    }

    get()._cancelChatStream()   // 一条会话里只留一条在途流：上一条先放下
    const userMsg = withCid({ role: 'user', content: message, reply_to_id, reply_to_preview })
    const charMsg = withCid({ role: 'char', content: '' })
    const cid = charMsg._cid
    set({ messages: [...messages, userMsg, charMsg], sending: true, error: null })

    let fullReply = ''

    // done 与 error 两种帧的**唯一**收尾函数。`err` 有值 = 错误帧：它不带 user_msg_id /
    // summary / retracted 这些只有成功一轮才有的字段，只走两帧共用的那一段（补写报告 +
    // 解锁）。两帧分开写的话，error 那条路会漏掉 `applyFlushReport`。
    //
    // 归属判据是「我还是不是当前那条流」：不是（已被放下 / 已被新流顶替）就什么都不做 ——
    // 迟到的帧既不解锁别的流，也不改别的会话。
    const settle = (payload, err) => {
      if (get()._chatStream?.cid !== cid) return
      if (err) console.error('[store] stream failed:', err)
      set((s) => {
        const msgs = [...s.messages]
        const idx = msgs.findIndex((m) => m._cid === cid)
        if (!err && idx >= 1 && msgs[idx - 1].role === 'user') {
          // 没落库时后端给的 msg_id 是 null，这里不再拿它当「有没有这段」的门闩 ——
          // 要不要标「未保存」只由 user_save/char_save 说了算。
          const prevUser = msgs[idx - 1]
          msgs[idx - 1] = withSaveResult(
            { ...prevUser, id: payload.user_msg_id ?? prevUser.id, timestamp: payload.user_created_at },
            payload.user_save,
          )
        }
        if (!err && idx !== -1) {
          msgs[idx] = withSaveResult(
            { ...msgs[idx], id: payload.char_msg_id ?? msgs[idx].id, timestamp: payload.char_created_at },
            payload.char_save,
          )
        }
        if (!err && payload.summary && idx >= 1) {
          msgs.splice(idx - 1, 0, withCid({ role: 'summary', content: payload.summary }))
        }
        if (!err && payload.retracted && idx !== -1) {
          msgs[idx] = { ...msgs[idx], retracted: true }
        }
        // 两种帧都走到这里：本轮补写成功的消息据此填回真 id、翻成「已保存」
        const next = { messages: applyFlushReport(msgs, payload), sending: false, _chatStream: null }
        if (err) next.error = err.message
        return next
      })
      if (err) return

      if (voiceEnabled && fullReply) {
        // 合成的是**自己那条**气泡：按 `cid` 找下标，不按 `length - 1` —— 回复途中列表末尾
        // 可能被插进别的消息，按末尾算会把语音挂到别人头上。
        const idx = get().messages.findIndex((m) => m._cid === cid)
        if (idx !== -1) get()._synthesizeVoiceReply(fullReply, idx)
      }

      get().fetchAffinity()
    }

    const body = { session_id: sessionId, message, stream: true, user_role: get().sessionUserRole, web_search: get().webSearchEnabled, agent_mode: get().agentMode, voice_mode: voiceEnabled, affinity_enabled: get().affinityEnabled }
    if (reply_to_id) { body.reply_to_id = reply_to_id; body.reply_to_preview = reply_to_preview }

    const cancel = streamSSE(
      '/api/chat/send',
      body,
      (token) => {
        if (get()._chatStream?.cid !== cid) return
        fullReply += token
        patchByCid(cid, (m) => ({ ...m, content: (m.content || '') + token }))
      },
      // done 帧与 error 帧走**同一个**收尾函数：两种帧由后端同一个出口构造，都带本轮
      // 的 `flushed` / `dropped`。分开两处处理时 error 那条路漏了 `applyFlushReport`，
      // 这一轮里顺路补写成功的消息就永远停在「未保存」。
      async (payload) => settle(payload, undefined),
      (err, payload) => settle(payload, err),
      undefined,
    )

    set({ _chatStream: { cid, cancel } })
    // 交给组件的是「仍是当前流才放下」的包装，不是裸 `cancel`：ChatArea 在「重置对话 / 撤回」
    // 前直接调它（`ChatArea.jsx:827/:843`），裸 cancel 会绕过 `_cancelChatStream`，abort 后
    // 迟到的收尾帧仍被认作当前流，写出一条假的「请求超时」。
    return () => {
      if (get()._chatStream?.cid !== cid) return
      get()._cancelChatStream()
    }
  },

  // 后端「重试」入口：把这条会话里没落库的消息按原顺序再写一遍。
  // 每次写本身也会顺带补写队头，但用户不该为了补一条消息被迫再发一条 —— 这就是那个按钮。
  flushMessages: protect(async (setScoped, get) => {
    const { sessionId, messages } = get()
    if (!sessionId) return
    try {
      const data = await postJSON(`/api/chat/${sessionId}/flush`, { keys: pendingSaveKeys(messages) })
      setScoped((s) => ({ messages: applyFlushReport(s.messages, data) }))
    } catch (err) {
      console.error('[store] flushMessages failed:', err)
      setScoped({ error: err.message })
    }
  }),

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

    get()._cancelChatStream()   // 与 `sendMessageStream` 同一条规矩：只留一条在途流
    const hiddenMsg = '[系统提示：对方刚刚撤回了一条消息]'
    const charMsg = withCid({ role: 'char', content: '' })
    const cid = charMsg._cid
    set((s) => ({ messages: [...s.messages, charMsg], sending: true }))

    let fullReply = ''

    // 与 `sendMessageStream` 同一条规矩：done 与 error 帧共用一个收尾函数，按自己的归属
    // 解锁；本轮补写报告两种帧都带（撤回通知这一轮同样会把队里积压的消息顺路补上）。
    const settle = (payload, err) => {
      if (get()._chatStream?.cid !== cid) return
      if (err) console.error('[store] revoke notice failed:', err)
      set((s) => {
        const msgs = [...s.messages]
        const idx = msgs.findIndex((m) => m._cid === cid)
        if (!err && idx !== -1) {
          msgs[idx] = withSaveResult(
            { ...msgs[idx], id: payload.char_msg_id ?? msgs[idx].id }, payload.char_save,
          )
        }
        const next = { messages: applyFlushReport(msgs, payload), sending: false, _chatStream: null }
        if (err) next.error = err.message
        return next
      })
      if (err) return
      if (voiceEnabled && fullReply) {
        const idx = get().messages.findIndex((m) => m._cid === cid)
        if (idx !== -1) get()._synthesizeVoiceReply(fullReply, idx)
      }
    }

    const cancel = streamSSE(
      '/api/chat/send',
      { session_id: sessionId, message: hiddenMsg, stream: true, hidden: true, user_role: get().sessionUserRole },
      (token) => {
        if (get()._chatStream?.cid !== cid) return
        fullReply += token
        patchByCid(cid, (m) => ({ ...m, content: (m.content || '') + token }))
      },
      (payload) => settle(payload, undefined),
      (err, payload) => settle(payload, err),
    )

    set({ _chatStream: { cid, cancel } })
    return () => {
      if (get()._chatStream?.cid !== cid) return
      get()._cancelChatStream()
    }
  },

  resetChat: protect(async (setScoped, get) => {
    const { sessionId, currentCard } = get()
    if (!sessionId) return
    try {
      await postJSON('/api/chat/reset', { session_id: sessionId })
      const _resetMsgs = resolveOpeningMessages(
        { cardFirstMessage: currentCard?.first_message },
        withCid,
      )

      setScoped({ messages: _resetMsgs })
    } catch (err) {
      console.error('[store] resetChat failed:', err)
      setScoped({ error: err.message })
    }
  }),

  loadHistory: async ({ keyword, page = 1, page_size = 100 } = {}) => {
    set({ sessionListLoading: true })
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(page_size) })
      if (keyword) params.set('keyword', keyword)
      const res = await fetchWithTimeout(`/api/history/list?${params.toString()}`)
      const data = await res.json()
      set({ sessionList: data.items || [], sessionListLoading: false })
    } catch (err) {
      console.error('[store] loadHistory failed:', err)
      set({ error: err.message, sessionListLoading: false })
    }
  },

  resumeSession: async (sessionId) => {
    // 回到正在生成回复的**同一个**会话：只切回聊天页，不重载。重载会用服务器那份历史
    // （回复还没落库）替换列表，后台的流接着把字拼进用户自己那条消息里。
    // 与 selectCard / startChat 的「同卡复用」同一做法。
    if (get()._chatStream && get().sessionId === sessionId) {
      set({ currentView: 'chat', resumeLoading: false })
      return
    }
    get()._cancelChatStream()   // 换到别的会话前放下在途流（原来漏了）
    set({ resumeLoading: true })
    try {
      const data = await postJSON(`/api/history/${sessionId}/resume`, { voice_mode: get().voiceEnabled })
      const session = data.session || {}
      const messages = (data.messages || []).map((m) => withSaveResult(withCid({
        role: m.role,
        content: m.content,
        id: m.id,
        timestamp: m.created_at,
        retracted: m.retracted || false,
        // 打字机标记认后端那个 `reunion` 标记，不认 id：重逢问候没落库时它还没有 id。
        ...(m.reunion ? { _reunionTyping: true } : {}),
      }), m.save))
      set({
        sessionId: session.id || sessionId,
        currentSessionAvatar: session.avatar_data ?? null,
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
      get().applySessionIdentity({
        user_role: session.user_role || get().getUserRole(session.card_id),
        arc_phase: session.arc_phase ?? null,
      })
    } catch (err) {
      console.error('[store] resumeSession failed:', err)
      set({ error: err.message, resumeLoading: false })
      throw err
    }
  },

  clearMessageTyping: (cid) => set((s) => ({
    messages: s.messages.map((m) => {
      if (m._cid === cid && m._reunionTyping) {
        const { _reunionTyping, ...rest } = m
        return rest
      }
      return m
    }),
  })),

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
            updated.messages = [...s.messages, withCid({ role: 'system', content: '角色卡已更新' })]
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
  }
})

// 身份一变就递增代际：会话切换 / 换卡 / 换文本都会让在途的旧写回失效。
// 用 subscribe 而非在 switch action 里手工调用，新加的切换 action 也自动受保护。
useAppStore.subscribe((state, prev) => {
  if (
    state.sessionId !== prev.sessionId ||
    (state.currentCard?.id ?? null) !== (prev.currentCard?.id ?? null) ||
    state.currentTextId !== prev.currentTextId
  ) {
    bumpScope()
  }
})

export default useAppStore
