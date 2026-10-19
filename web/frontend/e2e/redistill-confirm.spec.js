// @ts-check
// F1 重新蒸馏确认的 e2e 验收。全程 page.route 模拟（无真实后端、无凭据），
// 用 window.__appStore.setState 注入状态（沿用 e2e/arc-phase-select.spec.js）。
//
// 断言五条：
//   1) 已有卡角色点「重新蒸馏」→ 弹窗出现（角色名 + 书名）；取消后 0 次请求，按钮复原
//   2) 再点并确认 → 1 次请求，force=true，character_name 正确
//   3) 无卡角色点「蒸馏角色」→ 不弹窗，1 次请求，force=false
//   4) 工作台卡片详情点「重新蒸馏」→ 弹出同一个弹窗
//   5) 弹窗出现时截图到 e2e/redistill-confirm-modal.png
//
// 用法：cd web/frontend && npx playwright test e2e/redistill-confirm.spec.js
import { test, expect } from '@playwright/test'

const CARD = { id: 'c1', name: '祈灼', text_id: 't1', card_json: JSON.stringify({ name: '祈灼' }) }
const TEXTS = [{ id: 't1', filename: 'a.md', title: '某书' }]

function json(route, body) {
  return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
}

// 模拟全部 /api/*。sent 保存 /api/distill/start 的请求体。
// view 决定注入后 App 渲染哪个 PANELS 页面。
async function setup(page, { view = 'character' } = {}) {
  const sent = []
  await page.route((u) => new URL(u).pathname.startsWith('/api/'), async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/auth/me') {
      return json(route, { id: 'u1', username: 'testadmin', role: 'user', has_api_key: true })
    }
    if (path === '/api/distill/cards/by-text/t1') return json(route, [CARD])
    if (path === '/api/distill/cards/standalone') return json(route, [])
    if (path === '/api/distill/start') {
      try { sent.push(JSON.parse(route.request().postData() || '{}')) } catch { sent.push(null) }
      return json(route, { task_id: 'tk1' })
    }
    return json(route, {})
  })
  await page.addInitScript(() => {
    localStorage.setItem('auth_token', 'x')
    localStorage.setItem('nav_view', 'home')
    window.__E2E = true
  })
  await page.goto('/')
  await page.waitForFunction(() => window.__appStore?.getState().authUser, { timeout: 8000 })
  await page.evaluate(({ view, card, texts }) => {
    window.__appStore.setState({
      isLoggedIn: true,
      authUser: { id: 'u1', username: 'testadmin', role: 'user' },
      currentView: view,
      currentTextId: 't1',
      texts,
      cards: [card],
      currentCard: null,
      identifiedChars: [{ name: '祈灼' }, { name: '江澄' }],
      identifying: false,
      distilling: false,
      distillTokenCount: 0,
      distillStatus: '',
      cardAvatars: {},
      userRolesByCard: {},
      standaloneCards: [],
      distillTasks: [],
      redistillPrompt: null,
      error: null,
    })
  }, { view, card: CARD, texts: TEXTS })
  return sent
}

async function openRedistillModal(page) {
  await page.getByRole('button', { name: '重新蒸馏' }).click()
  await expect(page.locator('.modal-card')).toBeVisible()
  return page.locator('.modal-card')
}

test('1) 已有卡：弹窗可取消，0 次请求，按钮复原', async ({ page }) => {
  const sent = await setup(page)
  const modal = await openRedistillModal(page)
  await expect(modal.locator('.modal-title')).toHaveText('重新蒸馏「祈灼」？')
  await expect(modal.locator('.modal-body')).toContainText('《某书》')

  await modal.locator('.modal-actions .btn-ghost').click()
  await expect(page.locator('.modal-card')).toHaveCount(0)
  expect(sent, '取消后不应发 distill/start').toHaveLength(0)
  await expect(page.getByRole('button', { name: '重新蒸馏' })).toBeVisible()
})

test('2) 已有卡：确认后发 1 次 force=true', async ({ page }) => {
  const sent = await setup(page)
  const modal = await openRedistillModal(page)
  await modal.locator('.modal-actions .btn-danger').click()

  await expect(page.locator('.modal-card')).toHaveCount(0)
  await expect.poll(() => sent.length).toBe(1)
  expect(sent[0]).toEqual({ text_id: 't1', character_name: '祈灼', force: true })
})

test('3) 无卡角色：不弹窗，force=false', async ({ page }) => {
  const sent = await setup(page)
  await page.getByRole('button', { name: '蒸馏角色' }).click()

  await expect.poll(() => sent.length).toBe(1)
  await expect(page.locator('.modal-card')).toHaveCount(0)
  expect(sent[0]).toEqual({ text_id: 't1', character_name: '江澄', force: false })
})

test('4) 工作台卡片详情：弹出同一个弹窗', async ({ page }) => {
  await setup(page, { view: 'distillWorkbench' })
  const cardItem = page.locator('.dw-task-item').filter({ hasText: '祈灼' })
  await expect(cardItem).toBeVisible({ timeout: 8000 })
  await cardItem.click()
  await expect(page.locator('.dw-detail-scroll')).toBeVisible()

  const modal = await openRedistillModal(page)
  await expect(modal.locator('.modal-title')).toHaveText('重新蒸馏「祈灼」？')
  await expect(modal.locator('.modal-body')).toContainText('《某书》')
})

test('5) 弹窗出现时截图', async ({ page }) => {
  await setup(page)
  await openRedistillModal(page)
  // 弹窗有淡入动画（modal-fade-in），关掉动画才截到最终样子
  await page.screenshot({ path: 'e2e/redistill-confirm-modal.png', animations: 'disabled' })
})
