import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import CharCard from '../CharCard'
import DistillWorkbench from '../DistillWorkbench'
import useAppStore from '../../store/useAppStore'
import { fetchWithTimeout } from '../../api/client'
import { fetchAllCards } from '../../api/cards'
import StartChatButton from '../StartChatButton'

// 蒸馏后的「试聊」要跟角色管理「开始对话」走同一条路：同一个 StartChatButton，
// 同一条链（身份弹窗 → pushView('chat') + startChat）。
// 把 StartChatButton 换成一个「照常转发真身」的 spy：两处渲染都落在同一个函数上，
// 这是「用的是同一个组件」的直接证据；转发真身又保证点击链是真的，不是把组件替空后
// 测了个假流程。
vi.mock('../StartChatButton', async (importOriginal) => {
  const actual = await importOriginal()
  return { default: vi.fn(actual.default) }
})

if (typeof window !== 'undefined') {
  // 宽度感知：useIsMobile 依赖它；1024 走桌面分栏（右侧直接是卡详情）
  window.matchMedia = (query) => {
    const m = /max-width:\s*(\d+)px/.exec(query)
    return {
      matches: m ? window.innerWidth <= Number(m[1]) : false,
      media: query,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, onchange: null,
      dispatchEvent: () => false,
    }
  }
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}
  if (!window.IntersectionObserver) {
    window.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} }
  }
}

vi.mock('../../api/client', () => ({
  fetchWithTimeout: vi.fn(),
  getAuthHeaders: vi.fn(() => ({})),
  postJSON: vi.fn(),
  streamSSE: vi.fn(),
  getToken: vi.fn(() => null),
  setToken: vi.fn(),
  removeToken: vi.fn(),
  setRefreshToken: vi.fn(),
  removeAuth: vi.fn(),
  exportCard: vi.fn(),
}))
vi.mock('../../store/db', () => ({
  saveAvatar: vi.fn(),
  getAvatar: vi.fn(() => Promise.resolve(null)),
  loadCardAvatar: vi.fn(() => Promise.resolve(null)),
}))
vi.mock('../../api/cards', () => ({
  fetchAllCards: vi.fn(() => Promise.resolve([])),
  fetchCardsByText: vi.fn(() => Promise.resolve([])),
  fetchStandaloneCards: vi.fn(() => Promise.resolve([])),
}))
vi.mock('../common/GlobalSearchBox', () => ({ default: () => null }))
vi.mock('../EditCardModal', () => ({ default: () => null }))
vi.mock('../common/ImageCropModal', () => ({ default: () => null }))
vi.mock('../common/ConfirmModal', () => ({ default: () => null }))

const CARD = { id: 'card1', name: '角色甲', card_json: '{"name":"角色甲"}', text_id: 't1' }
// 已完成的蒸馏任务（status=done → TERMINAL_VIEW.done.canChat=true，任务详情才出「试聊当前版本」）
const DONE_TASK = {
  id: 'task1', textId: 't1', card_id: 'card1', character: '角色甲',
  status: 'done', done: true, progress_pct: 100,
}

// 有存档：startChat 发完 /api/history/list 就弹存档列表并返回（这正是「试聊」与
// 「开始对话」要共用的那段）。没有存档才会去建会话，那条路是 startChat 自己的事，不在本次改动面。
const net = ({ history = 1, byText = [CARD] } = {}) => {
  vi.mocked(fetchWithTimeout).mockImplementation((url) => {
    const u = String(url)
    const json = (body) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
    if (u.startsWith('/api/history/list')) {
      return json(history > 0 ? { total: history, items: [{ id: 'a1', title: '旧会话' }] } : { total: 0, items: [] })
    }
    if (u.startsWith('/api/distill/cards/by-text/')) return json(byText)
    if (u === '/api/text/list') return json([{ id: 't1', filename: 'a.md' }])
    return json({})
  })
  vi.mocked(fetchAllCards).mockResolvedValue([CARD])
}

beforeEach(() => {
  window.innerWidth = 1024
  StartChatButton.mockClear()
  vi.mocked(fetchWithTimeout).mockReset()
  vi.mocked(fetchAllCards).mockReset()
  net()
  useAppStore.setState({
    authUser: { id: 'u1', role: 'user' },
    currentView: 'home', viewHistory: [],
    currentTextId: 't1', texts: [{ id: 't1', filename: 'a.md' }],
    cards: [CARD], currentCard: CARD, cardAvatars: {}, userRolesByCard: {},
    sessionId: null, messages: [], distillTasks: [],
    archiveModalOpen: false, archiveList: [], pendingCard: null, _pendingChatCardId: null,
  })
})

const renderCharCardDetail = async () => {
  const r = render(<CharCard />)
  await screen.findByRole('button', { name: '开始对话' })
  return r
}

const renderWorkbenchCardDetail = async () => {
  const r = render(<DistillWorkbench />)
  await screen.findByRole('button', { name: '试聊' })
  return r
}

// 任务详情：桌面上默认选中第一个任务；store.cards 故意留空 —— 卡必须靠 loadCards 现取，
// 否则「找不到 card_id」那条测试会因为 store 里本来就有卡而假绿。
const renderWorkbenchTaskDetail = async () => {
  useAppStore.setState({ distillTasks: [DONE_TASK], cards: [] })
  const r = render(<DistillWorkbench />)
  await screen.findByRole('button', { name: '试聊当前版本' })
  return r
}

// 身份弹窗两步：填角色名 → 确认并开始对话 → 进入对话。
// 「确认并开始对话」在角色名为空时是 disabled —— 名字填进去后要等重渲染把它解锁再点，
// 否则重页面（蒸馏任务详情）里那一次点击会打在还 disabled 的按钮上、静默失效。
const confirmIdentity = async (role = '魏无羡') => {
  fireEvent.change(document.querySelector('#role-setup-input'), { target: { value: role } })
  await waitFor(() =>
    expect(screen.getByRole('button', { name: '确认并开始对话' })).toBeEnabled(),
  )
  fireEvent.click(screen.getByRole('button', { name: '确认并开始对话' }))
  fireEvent.click(await screen.findByRole('button', { name: '进入对话' }))
}

describe('试聊 / 开始对话 共用同一个 StartChatButton', () => {
  // 这里渲染的是两个完整页面（真 store + 真弹窗），整套并发跑时 5s 默认超时不够。
  const SLOW = 20000

  it('两处渲染落在同一个组件上（同一个 spy），文案各自保持原样', async () => {
    const propsSeen = () => StartChatButton.mock.calls.map(([props]) => props)

    await renderCharCardDetail()
    expect(propsSeen().map((p) => p.label)).toContain('开始对话')

    await renderWorkbenchCardDetail()
    // 两次渲染落在同一个 mock 上 —— 就是同一个 StartChatButton
    expect(propsSeen().map((p) => p.label)).toContain('试聊')
  }, SLOW)

  it('CharCard：点「开始对话」→ 身份弹窗 → 确认 → pushView(chat) + startChat', async () => {
    await renderCharCardDetail()

    fireEvent.click(screen.getByRole('button', { name: '开始对话' }))
    expect(document.querySelector('#role-setup-input')).toBeInTheDocument()

    await confirmIdentity()

    await waitFor(() => expect(useAppStore.getState().currentView).toBe('chat'))
    expect(useAppStore.getState().archiveModalOpen).toBe(true)
    expect(useAppStore.getState().pendingCard?.id).toBe('card1')
  }, SLOW)

  it('DistillWorkbench：点「试聊」→ 身份弹窗 → 确认 → 同样的链', async () => {
    await renderWorkbenchCardDetail()

    fireEvent.click(screen.getByRole('button', { name: '试聊' }))
    expect(document.querySelector('#role-setup-input')).toBeInTheDocument()

    await confirmIdentity()

    await waitFor(() => expect(useAppStore.getState().currentView).toBe('chat'))
    expect(useAppStore.getState().archiveModalOpen).toBe(true)
    expect(useAppStore.getState().pendingCard?.id).toBe('card1')
  }, SLOW)

  it('TaskDetail：点「试聊当前版本」→ loadCards 现取到卡 → 身份弹窗 → 确认 → 同样的链', async () => {
    await renderWorkbenchTaskDetail()
    // 卡一开始不在 store 里，必须先 loadCards 才找得到
    expect(useAppStore.getState().cards).toEqual([])

    const loadCardsSpy = vi.spyOn(useAppStore.getState(), 'loadCards')
    fireEvent.click(screen.getByRole('button', { name: '试聊当前版本' }))

    await waitFor(() => expect(loadCardsSpy).toHaveBeenCalledWith('t1'))
    // 弹窗在 loadCards 的 await 之后才开，先在 act 里把它挂载副作用跑完，
    // 否则紧接着的 change 会被挂载时那次 setRole('') 覆盖掉。
    await screen.findByRole('button', { name: '确认并开始对话' })
    expect(document.querySelector('#role-setup-input')).toBeInTheDocument()

    await confirmIdentity()

    await waitFor(() => expect(useAppStore.getState().currentView).toBe('chat'))
    expect(useAppStore.getState().archiveModalOpen).toBe(true)
    // startChat 收到的是 loadCards 找出来的那张卡（不是任务里的占位）
    expect(useAppStore.getState().pendingCard?.id).toBe('card1')
  }, SLOW)

  it('TaskDetail：card_id 在 cards 里找不到 → 不弹身份弹窗、不进聊天', async () => {
    net({ byText: [] }) // loadCards 回来是空列表
    await renderWorkbenchTaskDetail()

    fireEvent.click(screen.getByRole('button', { name: '试聊当前版本' }))

    // 等异步 resolveCard 跑完（确实发过这次取卡请求），再断言什么都没发生
    await waitFor(() => expect(vi.mocked(fetchWithTimeout)).toHaveBeenCalledWith('/api/distill/cards/by-text/t1'))
    expect(document.querySelector('#role-setup-input')).toBeNull()
    expect(useAppStore.getState().currentView).not.toBe('chat')
    expect(useAppStore.getState().archiveModalOpen).toBe(false)
  }, SLOW)
})
