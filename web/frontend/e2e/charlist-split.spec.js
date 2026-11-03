// @ts-check
// 11-03 回归：角色页侧栏「已有卡片」被长名单挤到高度 0（看不见、点不到）。全程 page.route 模拟（无后端、无凭据）。
// 触发链：进角色页 → loadCards（GET /cards/by-text）+ loadRoster（GET /roster）→ 两块都有
//        → StackSplit 上下分栏；拖分隔条 → 上栏高度变化。
// 断言：桌面 1280×800 下 3 张卡与名单首项同时可点、「蒸馏所选」可点、卡能点开；拖动后上栏变高；手机不分栏且卡片可点。
// 本沙箱 Playwright 版本与仓库锁定版本的浏览器不配，审计方用等价脚本跑过（11-03，改前桌面 / 手机卡片均不可点，改后均可点）。
// 用法：cd web/frontend && npx playwright test e2e/charlist-split.spec.js
import { test, expect } from '@playwright/test'

const CARDS = ['云绮', '祈灼', '谢凛羽'].map((name, i) => ({
  id: `c${i}`, name, text_id: 't1', card_json: JSON.stringify({ name, identity: `${name}的身份` }),
}))
const ROSTER = Array.from({ length: 40 }, (_, i) => ({
  name: `人物${i}`, importance: '主要', reason: '有大量对话和行为描写，'.repeat(3),
}))

async function setup(page, viewport) {
  await page.setViewportSize(viewport)
  await page.route((u) => new URL(u).pathname.startsWith('/api/'), async (route) => {
    const url = route.request().url()
    const json = (body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
    if (url.includes('/api/auth/me')) return json({ id: 'u1', username: 't', has_api_key: true, role: 'user' })
    if (url.includes('/api/distill/cards/by-text/')) return json(CARDS)
    if (url.includes('/api/distill/cards/')) return json([])
    if (url.includes('/api/distill/roster/')) return json({ characters: ROSTER })
    if (url.includes('/api/texts')) return json([{ id: 't1', filename: 'mock.txt' }])
    return json({})
  })
  await page.addInitScript(() => {
    localStorage.setItem('auth_token', 'x')
    localStorage.setItem('nav_view', 'home')
  })
  await page.goto('/')
  await page.waitForFunction(() => window.__appStore?.getState().authUser, { timeout: 8000 })
  await page.evaluate(() => {
    const s = window.__appStore.getState()
    window.__appStore.setState({ isLoggedIn: true, texts: [{ id: 't1', filename: 'mock.txt' }] })
    s.selectText('t1')
  })
  await expect(page.getByText('人物0', { exact: true })).toBeVisible({ timeout: 8000 })
}

const cardItem = (page, name) => page.locator('.char-list-item', { hasText: name })

// 「看得见」按命中测试判：元素中心点上最顶层的元素属于它（被挤到 0 高或被裁掉时 boundingBox 仍有值）
const reachable = (page, locator) => locator.evaluate((el) => {
  const r = el.getBoundingClientRect()
  const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)
  return !!hit && el.contains(hit)
})

test('桌面：3 张卡与 40 人名单同时可见，卡能点开，分隔条可拖', async ({ page }) => {
  await setup(page, { width: 1280, height: 800 })
  for (const c of CARDS) {
    expect(await reachable(page, cardItem(page, c.name)), `${c.name} 卡片可点`).toBe(true)
  }
  // 「蒸馏所选」固定在下栏底部，不随名单滚走
  expect(await reachable(page, page.getByRole('button', { name: /蒸馏所选/ }))).toBe(true)
  await cardItem(page, '云绮').click()
  await expect.poll(() => page.evaluate(() => window.__appStore.getState().currentCard?.name)).toBe('云绮')
  await page.screenshot({ path: 'e2e/charlist-split-desktop.png' })

  const top = page.locator('[data-pane="top"]')
  const before = (await top.boundingBox()).height
  const bar = await page.getByRole('separator').boundingBox()
  await page.mouse.move(bar.x + bar.width / 2, bar.y + bar.height / 2)
  await page.mouse.down()
  await page.mouse.move(bar.x + bar.width / 2, bar.y + 150, { steps: 5 })
  await page.mouse.up()
  const after = (await top.boundingBox()).height
  expect(after).toBeGreaterThan(before + 100)
  await page.screenshot({ path: 'e2e/charlist-split-dragged.png' })
})

test('手机：不分栏，卡片和名单顺排', async ({ page }) => {
  await setup(page, { width: 375, height: 667 })
  await expect(page.getByRole('separator')).toHaveCount(0)
  expect(await reachable(page, cardItem(page, '云绮'))).toBe(true)
})
