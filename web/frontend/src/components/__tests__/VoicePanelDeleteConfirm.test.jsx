import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import VoicePanel from '../VoicePanel'
import useAppStore from '../../store/useAppStore'

// 5445431c 把 window.confirm 换成 ConfirmModal 时只换了判断、没删掉紧随其后的
// 删除调用：点一下删两次（第二次 404 假报错），确认框实际不拦任何东西。
// 这里守住「确认框是唯一删除落点」，以及「store 层删除失败会抛」。

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

import { fetchWithTimeout } from '../../api/client'

const VOICE = { voice_id: 'v1', type: 'custom', name: '我的音色' }

const deleteCalls = () =>
  vi.mocked(fetchWithTimeout).mock.calls.filter(([, o]) => o?.method === 'DELETE')

const mockApi = ({ deleteBehavior }) => {
  vi.mocked(fetchWithTimeout).mockImplementation((url, opts) => {
    if (opts?.method === 'DELETE') return deleteBehavior()
    if (url === '/api/voice/list') return Promise.resolve({ ok: true, json: () => Promise.resolve([VOICE]) })
    if (url === '/api/voice/ref-audio/c1') return Promise.resolve({ ok: true, json: () => Promise.resolve({ exists: true, filename: 'ref.wav' }) })
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
  })
}
const deleteOk = () => mockApi({ deleteBehavior: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }) })
const deleteFails = () => mockApi({
  deleteBehavior: () => Promise.reject(Object.assign(new Error('音色不存在'), { status: 404 })),
})

const clickDeleteVoice = () => {
  const li = document.querySelector('.voice-list-item')
  fireEvent.click(li.querySelector('.text-list-action-danger'))
}
const clickConfirm = () =>
  fireEvent.click([...document.querySelectorAll('.modal-actions button')].find((b) => b.textContent === '确定'))

beforeEach(() => {
  vi.mocked(fetchWithTimeout).mockReset()
  localStorage.clear()
  useAppStore.setState({
    currentCard: { id: 'c1', name: '角色甲' },
    voiceList: [VOICE],
    voiceRefInfo: { exists: true, filename: 'ref.wav' },
    voiceStatus: {},
  })
})

describe('VoicePanel 删除：确认框是唯一落点', () => {
  it('点删除只打开确认框，不发 DELETE', async () => {
    deleteOk()
    render(<VoicePanel />)
    await waitFor(() => expect(document.querySelector('.voice-list-item')).toBeInTheDocument())

    clickDeleteVoice()

    expect(document.querySelector('.modal-title').textContent).toBe('删除音色')
    expect(deleteCalls()).toHaveLength(0)
  })

  it('点确认后 DELETE 只发一次', async () => {
    deleteOk()
    render(<VoicePanel />)
    await waitFor(() => expect(document.querySelector('.voice-list-item')).toBeInTheDocument())

    clickDeleteVoice()
    clickConfirm()

    await waitFor(() => expect(document.querySelector('.modal-title')).toBeNull())
    expect(deleteCalls()).toHaveLength(1)
  })

  it('删除失败：错误显示出来，音色还在列表里', async () => {
    deleteFails()
    render(<VoicePanel />)
    await waitFor(() => expect(document.querySelector('.voice-list-item')).toBeInTheDocument())

    clickDeleteVoice()
    clickConfirm()

    await waitFor(() => expect(document.querySelector('.voice-msg-error')).toBeInTheDocument())
    expect(document.querySelector('.voice-msg-error').textContent).toContain('音色不存在')
    expect(document.querySelector('.voice-list-item')).toBeInTheDocument()
  })

  it('参考音频：点删除只打开确认框，确认后才发一次 DELETE', async () => {
    deleteOk()
    render(<VoicePanel />)
    await waitFor(() => expect(document.querySelector('.voice-configured-card')).toBeInTheDocument())

    fireEvent.click([...document.querySelectorAll('.voice-configured-card button')].find((b) => b.textContent === '删除'))
    expect(document.querySelector('.modal-title').textContent).toBe('删除参考音频')
    expect(deleteCalls()).toHaveLength(0)

    clickConfirm()
    await waitFor(() => expect(deleteCalls()).toHaveLength(1))
  })
})

describe('store 层：删除失败要抛给调用方', () => {
  it('deleteCustomVoice 失败时 reject', async () => {
    deleteFails()
    await expect(useAppStore.getState().deleteCustomVoice('v1')).rejects.toThrow('音色不存在')
  })

  it('deleteVoiceRef 失败时 reject', async () => {
    deleteFails()
    await expect(useAppStore.getState().deleteVoiceRef('c1')).rejects.toThrow('音色不存在')
  })
})
