// 前端上报接线（`src/observability.js`）。
//
// **SDK 整个被替掉**：这里要钉的是「我们怎么接」，不是 SDK 自己怎么干活 ——
// `reactErrorHandler → captureException` 那段是 SDK 的契约，由本地 docker 那一轮用真
// SDK 在真浏览器里验（抛一个未捕获错误，假接收端要收到 envelope）。若在这里放真 SDK，
// 就得把它的内部实现拖进断言，测的反而更少。
//
// 于是替身照 SDK 的契约来：`reactErrorHandler()` 返回的处理函数把收到的错误交给
// `captureException`。React 19 的三个钩子挂在 `createRoot` 上，所以必须用真的
// `createRoot` —— 换成 `render()` 就没法传钩子，这条用例也就无从谈起。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import ErrorBoundary from '../components/common/ErrorBoundary'
import { initErrorReporting, reactErrorHooks } from '../observability'

const { mockInit, mockCaptureException } = vi.hoisted(() => ({
  mockInit: vi.fn(),
  mockCaptureException: vi.fn(),
}))

vi.mock('@sentry/react', () => ({
  init: mockInit,
  reactErrorHandler: () => (error, errorInfo) => mockCaptureException(error, errorInfo),
}))

const DSN = 'https://public-probe@errors.example.test/7'

let container

beforeEach(() => {
  mockInit.mockClear()
  mockCaptureException.mockClear()
  container = document.createElement('div')
  document.body.appendChild(container)
})

afterEach(() => {
  container.remove()
  vi.unstubAllGlobals()
})

function stubConfig(payload, ok = true) {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok, json: async () => payload })))
}

function render(children) {
  const root = createRoot(container, reactErrorHooks())
  return act(async () => { root.render(children) })
}

describe('initErrorReporting', () => {
  it('配置为空 → 不 init，界面照常渲染', async () => {
    stubConfig({})

    await initErrorReporting()

    expect(mockInit).not.toHaveBeenCalled()
    await render(<div>ok</div>)
    expect(container.textContent).toBe('ok')
  })

  it('有 DSN → init 一次，release / region 取接口给的值，且关掉 session', async () => {
    stubConfig({ sentry_dsn: DSN, region: 'cn-shenzhen', release: 'sha-abc' })

    await initErrorReporting()

    expect(mockInit).toHaveBeenCalledTimes(1)
    const opts = mockInit.mock.calls[0][0]
    expect(opts.dsn).toBe(DSN)
    expect(opts.release).toBe('sha-abc')
    expect(opts.initialScope.tags.region).toBe('cn-shenzhen')
    expect(opts.tracesSampleRate).toBe(0)
    expect(opts.sendDefaultPii).toBe(false)
    // GlitchTip 不支持 session 上报：能报的集成集合里不该留着它。
    const names = opts.integrations([{ name: 'BrowserSession' }, { name: 'GlobalHandlers' }])
    expect(names.map((i) => i.name)).toEqual(['GlobalHandlers'])
  })

  it('取配置这条路自己炸了，也不该拦住渲染', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('network down') }))
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})

    await initErrorReporting()

    expect(mockInit).not.toHaveBeenCalled()
    expect(spy).toHaveBeenCalled()
    spy.mockRestore()
    await render(<div>ok</div>)
    expect(container.textContent).toBe('ok')
  })
})

describe('reactErrorHooks', () => {
  it('子组件渲染时抛错 → 现有 fallback 出现，且上报被调用一次', async () => {
    stubConfig({ sentry_dsn: DSN, region: 'cn-shenzhen' })
    await initErrorReporting()

    function Boom() {
      throw new Error('obs-probe')
    }

    // 组件抛错时 React 自己会往 console.error 写一份，别把它当失败信号。
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    await render(<ErrorBoundary><Boom /></ErrorBoundary>)
    spy.mockRestore()

    expect(container.textContent).toContain('页面出错了')
    expect(mockCaptureException).toHaveBeenCalledTimes(1)
    expect(mockCaptureException.mock.calls[0][0].message).toBe('obs-probe')
  })
})
