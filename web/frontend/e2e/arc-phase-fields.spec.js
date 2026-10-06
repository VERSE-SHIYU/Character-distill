// @ts-check
// §5 前端触发链的自动验收（arc-phase-fields）。全程 page.route 模拟（无真实后端、无凭据）。
// 断言三条：
//   1) character_arc.selectable=false：弹窗不出阶段单选
//   2) selectable=true：弹窗出阶段单选
//   3) 弧线列表展示阶段 overlay（阶段 2 的性格与口头禅）；截图一张给 Shiyu 看排版
//
// 用法：cd web/frontend && npx playwright test e2e/arc-phase-fields.spec.js
import { test, expect } from '@playwright/test'

const PHASES = [
  {
    label: '初入江湖', state: '少年意气',
    overlay: { personality_traits: ['天真烂漫'], 'speaking_style.catchphrases': ['我先走一步'] },
  },
  {
    label: '乱葬岗后', state: '心硬了',
    overlay: { personality_traits: ['不再轻信'], 'speaking_style.catchphrases': ['我劝你少管闲事'] },
  },
]

function cardJson(phases, selectable) {
  return JSON.stringify({
    name: '魏无羡',
    relationships: [],
    character_arc: { axis: '从冷到热', phases, selectable },
  })
}

function injectCard(page, phases, selectable) {
  return page.evaluate(({ json, sel }) => {
    window.__appStore.setState({
      isLoggedIn: true,
      currentView: 'character',
      currentTextId: 't1',
      texts: [{ id: 't1', filename: 'mock.txt' }],
      cards: [],
      currentCard: { id: 'c1', name: '魏无羡', text_id: 't1', card_json: json, arc_selectable: sel },
    })
  }, { json: cardJson(phases, selectable), sel: selectable })
}

async function setup(page, { phases = PHASES, selectable = true } = {}) {
  const sent = []
  await page.route((u) => new URL(u).pathname.startsWith('/api/'), async (route) => {
    const url = route.request().url()
    if (url.includes('/api/auth/me')) {
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ id: 'u1', username: 't', has_api_key: true }) })
    }
    if (url.includes('/api/history/list')) {
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ total: 0, items: [] }) })
    }
    if (url.includes('/api/distill/start_session')) {
      try { sent.push(JSON.parse(route.request().postData() || '{}')) } catch { sent.push(null) }
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ session_id: 's1', first_message: '……' }) })
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })
  await page.addInitScript(() => {
    localStorage.setItem('auth_token', 'x')
    localStorage.setItem('nav_view', 'character')
  })
  await page.goto('/')
  await page.waitForFunction(() => window.__appStore, { timeout: 8000 })
  await injectCard(page, phases, selectable)
  await expect(page.locator('.card-chat-btn')).toBeVisible({ timeout: 8000 })
  return sent
}

async function openRoleModal(page) {
  await page.locator('.card-chat-btn').click()
  await expect(page.locator('.role-setup-card')).toBeVisible()
  await page.fill('#role-setup-input', '江澄')
}

test('selectable=false：弹窗不出阶段单选', async ({ page }) => {
  await setup(page, { selectable: false })
  await openRoleModal(page)
  await expect(page.locator('.role-phase-select')).toHaveCount(0)
})

test('selectable=true：弹窗出阶段单选', async ({ page }) => {
  await setup(page, { selectable: true })
  await openRoleModal(page)
  await expect(page.locator('.role-phase-option')).toHaveCount(2)
})

test('弧线列表展示阶段 overlay（阶段 2 的性格与口头禅）', async ({ page }) => {
  await setup(page)
  const overlay = page.locator('.card-arc-overlay').nth(1)
  await expect(overlay).toContainText('不再轻信')
  await expect(overlay).toContainText('我劝你少管闲事')
  await page.screenshot({ path: 'e2e/arc-phase-fields-arclist.png' })
})
