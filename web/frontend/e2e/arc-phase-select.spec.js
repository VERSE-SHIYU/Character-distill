// @ts-check
// §4 前端触发链的自动验收。全程 page.route 模拟（无真实后端、无凭据），注入 store 状态直达
// 角色详情（沿用 e2e/repro-chat-top.spec.js 的既有写法：auth_token + nav_view + __appStore）。
//
// 断言五条：
//   1) 有阶段卡：弹窗出现阶段单选，默认最后阶段
//   2) 选阶段 1 → start_session 请求体 arc_phase=1；进入聊天头部显示「阶段 1/n · label」
//   3) 无阶段卡：弹窗不出现单选，头部不显示阶段
//   4) 恢复存档：头部显示存档里的阶段
//
// 用法：cd web/frontend && npx playwright test e2e/arc-phase-select.spec.js
import { test, expect } from '@playwright/test'

const PHASES = [
  { label: '初入江湖', state: '少年意气' },
  { label: '乱葬岗后', state: '心硬了' },
  { label: '归来', state: '看开了' },
]

function cardJson(phases) {
  return JSON.stringify({
    name: '魏无羡',
    relationships: [],
    character_arc: { axis: '从冷到热', phases },
  })
}

// 直达角色详情：currentView='character' + currentTextId + currentCard（CardDetail 渲染 StartChatButton）
function injectCard(page, phases) {
  return page.evaluate((json) => {
    window.__appStore.setState({
      isLoggedIn: true,
      currentView: 'character',
      currentTextId: 't1',
      texts: [{ id: 't1', filename: 'mock.txt' }],
      cards: [],
      currentCard: { id: 'c1', name: '魏无羡', text_id: 't1', card_json: json },
    })
  }, cardJson(phases))
}

// 模拟全部 /api/*。返回 sent：start_session 的请求体数组。
async function setup(page, { phases = PHASES, archives = null } = {}) {
  const sent = []
  // 必须按 pathname 匹配：'**/api/**' 会误拦 vite 的 /src/api/client.js 模块请求
  await page.route((u) => new URL(u).pathname.startsWith('/api/'), async (route) => {
    const url = route.request().url()
    if (url.includes('/api/auth/me')) {
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ id: 'u1', username: 't', has_api_key: true }) })
    }
    if (url.includes('/api/history/list')) {
      const items = archives ? [archives] : []
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ total: items.length, items }) })
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
  await injectCard(page, phases)
  await expect(page.locator('.card-chat-btn')).toBeVisible({ timeout: 8000 })
  return sent
}

// 打开弹窗 → 填身份名（≠ 角色名）→ 停在输入步
async function openRoleModal(page) {
  await page.locator('.card-chat-btn').click()
  await expect(page.locator('.role-setup-card')).toBeVisible()
  await page.fill('#role-setup-input', '江澄')
}

async function enterChat(page) {
  await page.locator('.modal-actions .btn-primary').click()        // 确认并开始对话 → 确认步
  await expect(page.locator('.role-confirm-text')).toBeVisible()
  await page.locator('.modal-actions .btn-primary').click()        // 进入对话 → onConfirm
}

test('有阶段卡：弹窗出现单选且默认最后阶段', async ({ page }) => {
  await setup(page)
  await openRoleModal(page)

  const options = page.locator('.role-phase-option')
  await expect(options).toHaveCount(3)
  await expect(options.nth(2).locator('input[type=radio]')).toBeChecked()   // 默认最后阶段
  await expect(options.nth(0)).toContainText('阶段 1 · 初入江湖')

  await page.screenshot({ path: 'e2e/arc-phase-modal.png' })
})

test('选阶段 1：请求体 arc_phase=1，头部显示「阶段 1/n」', async ({ page }) => {
  const sent = await setup(page)
  await openRoleModal(page)

  await page.locator('.role-phase-option').nth(0).locator('input[type=radio]').check()
  await expect(page.locator('.role-phase-option').nth(0).locator('input')).toBeChecked()

  // 确认步带上所选阶段
  await page.locator('.modal-actions .btn-primary').click()
  await expect(page.locator('.role-confirm-text')).toContainText('阶段 1/3 · 初入江湖')

  await page.locator('.modal-actions .btn-primary').click()        // 进入对话
  await expect(page.locator('.user-role-phase')).toContainText('阶段 1/3 · 初入江湖', { timeout: 8000 })

  expect(sent, '应发出一次 start_session').toHaveLength(1)
  expect(sent[0].arc_phase, `请求体应带 arc_phase=1：${JSON.stringify(sent[0])}`).toBe(1)

  await page.screenshot({ path: 'e2e/arc-phase-header.png' })
})

test('无阶段卡：弹窗无单选，头部无阶段显示', async ({ page }) => {
  const sent = await setup(page, { phases: [] })
  await openRoleModal(page)

  await expect(page.locator('.role-phase-select')).toHaveCount(0)

  await enterChat(page)
  await expect(page.locator('.user-role-bar')).toBeVisible({ timeout: 8000 })
  await expect(page.locator('.user-role-phase')).toHaveCount(0)

  expect(sent[0]?.arc_phase ?? null, '无阶段卡的请求体 arc_phase 应为 null').toBeNull()
})

test('恢复存档：头部显示存档里的阶段', async ({ page }) => {
  await setup(page, { archives: {
    id: 's9', card_id: 'c1', arc_phase: 2, user_role: '我',
    last_message: '上一句', affinity: 50, trust: 30, guard: 70,
  } })
  await openRoleModal(page)
  await enterChat(page)

  // 有存档 → 存档弹窗；选它 → enterArchive 用 session.arc_phase
  await expect(page.locator('.archive-slot-item')).toBeVisible({ timeout: 8000 })
  await page.locator('.archive-slot-item').click()

  await expect(page.locator('.user-role-phase')).toContainText('阶段 2/3 · 乱葬岗后', { timeout: 8000 })
})
