import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import ApiConfigPanel from '../ApiConfigPanel'

// 默认模型名只有一处真源：`/api/settings/config` 返回的 `model`。面板里写死模型名就会
// 与配置分叉（前端显示一个、实际请求另一个），这三条用例把三个写死点各锁一遍。

const { mockState, mutate, meta } = vi.hoisted(() => {
  const state = {
    popView: () => {},
    affinityEnabled: false,
    setAffinityEnabled: () => {},
  }
  return { mockState: state, mutate: (patch) => Object.assign(state, patch), meta: { meModel: '' } }
})

vi.mock('../../store/useAppStore', () => {
  const hook = (sel) => sel(mockState)
  hook.getState = () => mockState
  hook.setState = (patch) => Object.assign(mockState, patch)
  return { default: hook }
})

vi.mock('../../api/client', () => ({
  fetchWithTimeout: async (url) => ({
    json: async () => (url === '/api/settings/config'
      ? { model: 'probe-model', summary_threshold: 50 }
      : { model: meta.meModel, has_api_key: true, base_url: '' }),
  }),
  getMyUsage: async () => ({}),
  updateApiConfig: async () => ({}),
}))

vi.mock('../PageHeader', () => ({ default: () => null }))

const modelInput = (container) =>
  [...container.querySelectorAll('.settings-field')]
    .find((f) => f.querySelector('.settings-label')?.textContent === 'model')
    ?.querySelector('input')

// 点「其他模型」回到自定义模式（该卡片只切 provider，不动 model），好读回卡片填的值。
const cards = (container) => [...container.querySelectorAll('.provider-card')]

beforeEach(() => { meta.meModel = '' })

describe('ApiConfigPanel 的默认模型来自设置接口', () => {
  it('未设模型的用户：输入框回落为设置接口给的默认模型', async () => {
    const { container } = render(<ApiConfigPanel />)
    await waitFor(() => expect(modelInput(container)).toBeTruthy())
    expect(modelInput(container).value).toBe('probe-model')
  })

  it('点 DeepSeek 卡片后填的也是默认模型', async () => {
    const { container } = render(<ApiConfigPanel />)
    await waitFor(() => expect(modelInput(container)).toBeTruthy())
    fireEvent.click(cards(container)[0])
    fireEvent.click(cards(container)[1])
    expect(modelInput(container).value).toBe('probe-model')
  })

  it('用户自己存过模型：填的是用户那份，不被默认值盖掉', async () => {
    meta.meModel = 'mine'
    const { container } = render(<ApiConfigPanel />)
    await waitFor(() => expect(modelInput(container)).toBeTruthy())
    expect(modelInput(container).value).toBe('mine')
  })
})
