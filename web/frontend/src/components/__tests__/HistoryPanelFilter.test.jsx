import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import HistoryPanel from '../HistoryPanel'
import { fetchWithTimeout } from '../../api/client'

// 历史页两个筛选框换成 Select 后的接线：选项的 value 要原样落进请求参数，
// 「全部文本 / 全部角色」这种空项必须回到 ''（URL 上不出现该参数）。
// 空项 value 若写成 'all' 之类的哨兵，用户一选「全部」就会被当成具体筛选发出去。
// 两个筛选各测一次 —— 共用一个 onChange 断开的形态，只测一个漏一个。

const { mockState } = vi.hoisted(() => ({
  mockState: {
    texts: [],
    textProgress: {},
    loadTextProgress: () => {},
    cards: [],
    resumeSession: () => {},
    resumeLoading: false,
    cardAvatars: {},
    setCardAvatar: () => {},
    popView: () => {},
    navigateTo: () => {},
    pushView: () => {},
    setResumeGroupId: () => {},
  },
}))

vi.mock('../../store/useAppStore', async (importOriginal) => {
  const actual = await importOriginal()
  const hook = (sel) => sel(mockState)
  hook.getState = () => mockState
  hook.setState = (patch) => Object.assign(mockState, patch)
  return { ...actual, default: hook }
})

vi.mock('../../api/client', () => ({ fetchWithTimeout: vi.fn() }))

const listUrls = () =>
  fetchWithTimeout.mock.calls.map(([url]) => String(url)).filter((u) => u.includes('/api/history/list'))

beforeEach(() => {
  fetchWithTimeout.mockClear()
  fetchWithTimeout.mockImplementation(() =>
    Promise.resolve({ ok: true, json: () => Promise.resolve({ items: [], total: 0 }) }),
  )
  mockState.texts = []
  mockState.cards = []
})

describe('HistoryPanel 筛选接线', () => {
  it('选某文本后请求带 text_id，选「全部文本」后不带', async () => {
    mockState.texts = [{ id: 7, filename: '文本A.txt' }]
    render(<HistoryPanel />)
    await waitFor(() => expect(listUrls().length).toBeGreaterThan(0))
    expect(listUrls().at(-1)).not.toContain('text_id')

    const trigger = screen.getByRole('combobox', { name: '文本筛选' })
    fireEvent.click(trigger)
    fireEvent.click(screen.getByRole('option', { name: '文本A.txt' }))
    await waitFor(() => expect(listUrls().at(-1)).toContain('text_id=7'))

    fireEvent.click(trigger)
    fireEvent.click(screen.getByRole('option', { name: '全部文本' }))
    await waitFor(() => expect(listUrls().at(-1)).not.toContain('text_id'))
  })

  it('角色筛选：选「甲」后请求带 character，选「全部角色」后不带', async () => {
    mockState.cards = [{ name: '甲' }]
    render(<HistoryPanel />)
    await waitFor(() => expect(listUrls().length).toBeGreaterThan(0))
    expect(listUrls().at(-1)).not.toContain('character=')

    const trigger = screen.getByRole('combobox', { name: '角色筛选' })
    fireEvent.click(trigger)
    fireEvent.click(screen.getByRole('option', { name: '甲' }))
    await waitFor(() =>
      expect(listUrls().at(-1)).toContain(`character=${encodeURIComponent('甲')}`),
    )

    fireEvent.click(trigger)
    fireEvent.click(screen.getByRole('option', { name: '全部角色' }))
    await waitFor(() => expect(listUrls().at(-1)).not.toContain('character='))
  })
})
