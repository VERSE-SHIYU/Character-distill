import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, fireEvent, screen, waitFor } from '@testing-library/react'
import ApiConfigPanel from '../ApiConfigPanel'

// DeepSeek 模式下让用户自己选模型：跟随默认 / 具体型号（名单来自后端 available_models）。
// 保存时的关键约定（承接 #156）：只在用户真动过下拉时才提交，且「跟随默认」翻译成
// 请求字段 `reset_model: true`，不是一个模型名。未动下拉 → model/reset_model 都不发。

const { mockState, meta } = vi.hoisted(() => {
  const state = { popView: () => {}, affinityEnabled: false, setAffinityEnabled: () => {} }
  return {
    mockState: state,
    meta: { model: 'deepseek-flash', modelSource: 'default', calls: [] },
  }
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
      ? { model: 'probe-model', available_models: ['deepseek-flash', 'deepseek-v4-pro'] }
      : {
        model: meta.model,
        model_source: meta.modelSource,
        has_api_key: true,
        base_url: 'https://api.deepseek.com',
      }),
  }),
  getMyUsage: async () => ({}),
  updateApiConfig: async (payload) => { meta.calls.push(payload); return {} },
}))

vi.mock('../PageHeader', () => ({ default: () => null }))

const saveButton = () => screen.getByRole('button', { name: '保存配置' })

async function openPickerAndPick(name) {
  await waitFor(() => expect(screen.getByRole('combobox')).toBeInTheDocument())
  fireEvent.click(screen.getByRole('combobox'))
  fireEvent.click(screen.getByRole('option', { name }))
}

async function save() {
  fireEvent.click(saveButton())
  await waitFor(() => expect(meta.calls.length).toBe(1))
}

beforeEach(() => {
  meta.model = 'deepseek-flash'
  meta.modelSource = 'default'
  meta.calls = []
})

describe('DeepSeek 模式的模型下拉', () => {
  it('三个选项：跟随默认 + 两个型号', async () => {
    render(<ApiConfigPanel />)
    await waitFor(() => expect(screen.getByRole('combobox')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('combobox'))

    const names = screen.getAllByRole('option').map((o) => o.textContent)
    expect(names).toHaveLength(3)
    expect(names.some((n) => n.includes('跟随默认'))).toBe(true)
    expect(names).toContain('deepseek-flash')
    expect(names).toContain('deepseek-v4-pro')
  })

  it('显示当前模型与来源：跟随默认', async () => {
    const { container } = render(<ApiConfigPanel />)
    await waitFor(() =>
      expect(container.textContent).toContain('当前使用：deepseek-flash（跟随系统默认）'))
  })

  it('显示当前模型与来源：用户指定', async () => {
    meta.model = 'deepseek-v4-pro'
    meta.modelSource = 'user'
    const { container } = render(<ApiConfigPanel />)
    await waitFor(() =>
      expect(container.textContent).toContain('当前使用：deepseek-v4-pro（你指定的）'))
  })
})

describe('保存时按所选发送', () => {
  it('选「跟随默认」→ 发 reset_model: true，不发 model', async () => {
    meta.model = 'deepseek-v4-pro'
    meta.modelSource = 'user'
    render(<ApiConfigPanel />)
    await openPickerAndPick(/跟随默认/)

    await save()

    expect(meta.calls[0].reset_model).toBe(true)
    expect(meta.calls[0].model).toBeUndefined()
  })

  it('选 pro → 发对应 model，不发 reset_model', async () => {
    render(<ApiConfigPanel />)
    await openPickerAndPick('deepseek-v4-pro')

    await save()

    expect(meta.calls[0].model).toBe('deepseek-v4-pro')
    expect(meta.calls[0].reset_model).toBeUndefined()
  })

  it('选 flash → 发对应 model', async () => {
    meta.model = 'deepseek-v4-pro'
    meta.modelSource = 'user'
    render(<ApiConfigPanel />)
    await openPickerAndPick('deepseek-flash')

    await save()

    expect(meta.calls[0].model).toBe('deepseek-flash')
  })

  it('不动下拉直接保存 → model 和 reset_model 都不发（#156 的口径）', async () => {
    render(<ApiConfigPanel />)
    await waitFor(() => expect(screen.getByRole('combobox')).toBeInTheDocument())

    await save()

    expect(meta.calls[0].model).toBeUndefined()
    expect(meta.calls[0].reset_model).toBeUndefined()
  })
})
