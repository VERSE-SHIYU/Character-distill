// Minimal repro: inject store state, 40 messages, check topbar visibility & scroll-to-top on desktop + mobile.
import { test, expect } from '@playwright/test'

const msgs = Array.from({ length: 40 }, (_, i) => ({
  role: i % 2 ? 'assistant' : 'user',
  content: `消息 ${i}：这是第 ${i} 条测试消息，用于撑开滚动容器的高度。`,
  id: i + 1,
  timestamp: new Date(Date.now() - (40 - i) * 60000).toISOString(),
  _cid: `c${i}`,
}))

async function setup(page) {
  // 注意：必须按 pathname 匹配，'**/api/**' 会误拦 vite 的 /src/api/client.js 模块请求
  await page.route((u) => new URL(u).pathname.startsWith('/api/'), async (route) => {
    const url = route.request().url()
    if (url.includes('/api/auth/me')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ id: 'u1', username: 't', has_api_key: true }) })
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })
  // token 键名必须是 auth_token（api/client.js TOKEN_KEY），否则停在登录页
  await page.addInitScript(() => { localStorage.setItem('auth_token', 'x'); localStorage.setItem('nav_view', 'home') })
  page.on('console', m => { if (m.type() === 'error') console.log('PAGE-ERR', m.text()) })
  await page.goto('/')
  // __appStore 仅 DEV 暴露（App.jsx），vite dev server 下可用
  await page.waitForFunction(() => window.__appStore, { timeout: 8000 })
  await page.evaluate(({ msgs }) => {
    window.__appStore.setState({
      isLoggedIn: true,
      currentView: 'chat',
      currentCard: { id: 'card1', name: '测试角色', session_id: 's1' },
      sessionId: 's1',
      messages: msgs,
      sending: false,
    })
  }, { msgs })
  await page.waitForTimeout(800)
}

async function probe(page, label) {
  const r = await page.evaluate(() => {
    const topbar = document.querySelector('.chat-topbar-compact')
    const list = document.querySelector('.chat-messages')
    const tb = topbar?.getBoundingClientRect()
    const out = {
      topbarExists: !!topbar,
      topbarRect: tb ? { top: tb.top, height: tb.height, visible: tb.bottom > 0 && tb.top < innerHeight && tb.height > 0 } : null,
      listScrollable: list ? list.scrollHeight > list.clientHeight : null,
      listScrollTop: list?.scrollTop,
      bodyScrollY: window.scrollY,
      docScrollH: document.documentElement.scrollHeight,
      winH: innerHeight,
    }
    if (list) { list.scrollTop = 0 }
    return out
  })
  await page.waitForTimeout(600)
  const afterTop = await page.evaluate(() => {
    const list = document.querySelector('.chat-messages')
    const first = list?.querySelector('[data-msg-id="1"], div')
    const fr = first?.getBoundingClientRect()
    return { scrollTopAfter: list?.scrollTop, firstMsgTop: fr?.top, listTop: list?.getBoundingClientRect().top }
  })
  console.log(label, JSON.stringify({ ...r, ...afterTop }, null, 1))
  await page.screenshot({ path: `e2e/repro-top-${label}.png` })
  return { ...r, ...afterTop }
}

test('desktop chat top reachable', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await setup(page)
  const r = await probe(page, 'desktop')
  expect(r.topbarExists).toBe(true)
  expect(r.listScrollable).toBe(true)
  expect(r.firstMsgTop).toBeGreaterThanOrEqual(0)
})

test('mobile chat top reachable', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await setup(page)
  const r = await probe(page, 'mobile')
  expect(r.topbarExists).toBe(true)
  expect(r.listScrollable).toBe(true)
  expect(r.firstMsgTop).toBeGreaterThanOrEqual(0)
})
