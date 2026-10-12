// @ts-check
// CSS 死选择器 PR1：群聊成员行样式合成一份 + 清掉 .group-chat-layout 死样式。
// 断言见 docs/specs/group-member-style.md §3（G1–G6）。全程 page.route 模拟（无真实后端、无凭据），
// 沿用 e2e/group-arc-phase.spec.js 的写法。
// 用法：cd web/frontend && npx playwright test e2e/group-member-style.spec.js
import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const CSS = fs.readFileSync(path.resolve(HERE, '../src/styles/global.css'), 'utf8')

const cardJson = (name, phases, selectable) =>
  JSON.stringify({ name, character_arc: { axis: 'x', selectable, phases: phases.map((l) => ({ label: l, state: l })) } })

const CARDS = [
  { id: 'a', name: '甲', card_json: cardJson('甲', ['冷', '热'], true) },
  { id: 'b', name: '乙', card_json: cardJson('乙', ['初', '终'], true) },
]
const GROUP = { id: 'g1', name: '群', card_ids: ['a', 'b'], created_at: '2026-01-01T00:00:00Z' }

async function setup(page) {
  await page.route((u) => new URL(u).pathname.startsWith('/api/'), (route) => {
    const p = new URL(route.request().url()).pathname
    const json = (body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
    if (p === '/api/auth/me') return json({ id: 'u1', username: 't', has_api_key: true })
    if (p === '/api/group/list') return json({ groups: [GROUP] })
    if (p.startsWith('/api/distill/cards/by-text/')) return json(CARDS)
    if (/^\/api\/cards\/.+/.test(p)) return json(CARDS.find((c) => c.id === p.split('/').pop()) || {})
    if (p.endsWith('/history')) return json({ messages: [] })
    if (p.endsWith('/affinities')) return json([])
    return json({})
  })
  await page.addInitScript(() => { window.__E2E = true; localStorage.setItem('auth_token', 'x') })
  await page.goto('/')
  await page.waitForFunction(() => window.__appStore?.getState().authUser, { timeout: 8000 })
  await page.evaluate(() => window.__appStore.setState({
    isLoggedIn: true, currentView: 'groupChat', authUser: { id: 'u1', username: 't' }, texts: [{ id: 't1', filename: '书' }],
  }))
  await expect(page.locator('.group-chat-page')).toBeVisible({ timeout: 8000 })
}

async function enterGroup(page) {
  await page.locator('.messages-conv-item', { hasText: '群' }).first().click()
  await expect(page.locator('.private-chat-body')).toBeVisible({ timeout: 8000 })
}

/** 打开右栏并切到「成员」标签（右栏已开就不重复点开）。 */
async function openMembers(page) {
  await page.locator('.chat-topbar-btn').first().waitFor({ state: 'visible', timeout: 8000 })
  const toggle = page.locator('.chat-topbar-btn[title="历史记录"]')
  if (await toggle.count()) await toggle.first().click()
  await page.getByRole('button', { name: /^成员 \(/ }).click()
}

/** 取一个元素的关键计算样式。 */
const computed = (loc) => loc.first().evaluate((el) => {
  const s = getComputedStyle(el)
  return {
    padding: s.padding, gap: s.gap, backgroundColor: s.backgroundColor, width: s.width, display: s.display,
    alignItems: s.alignItems, borderRadius: s.borderRadius, fontSize: s.fontSize, flexGrow: s.flexGrow,
    minWidth: s.minWidth, borderBottomWidth: s.borderBottomWidth, cursor: s.cursor, fontWeight: s.fontWeight,
    marginTop: s.marginTop, whiteSpace: s.whiteSpace, styleAttr: el.getAttribute('style'),
  }
})

// G4：成员条目悬停底色。取值 = head 默认主题下 `rgba(var(--accent-rgb), 0.04)` 的计算值
// （`--accent-rgb: 123, 97, 255`），在 head 上读出后写死，不再只判「非透明」。
const HOVER_BG = 'rgba(123, 97, 255, 0.04)'

const expectWidth = (actual, expected) => expect(Math.abs(parseFloat(actual) - expected)).toBeLessThanOrEqual(1)

test.describe('桌面 1280×900', () => {
  test.use({ viewport: { width: 1280, height: 900 } })

  test('G1–G4 成员条目/名字/身份/hover 的实际样式', async ({ page }) => {
    await setup(page)
    await enterGroup(page)
    await openMembers(page)

    const item = page.locator('.group-right-member-list .group-member-item').first()
    await expect(item).toBeVisible()

    // G1 成员条目：行布局（来自 .group-row）+ 分隔线 + 悬停光标；无内联 style
    const it = await computed(item)
    expect(it.padding).toBe('8px 10px')
    expect(it.gap).toBe('10px')
    expect(it.borderRadius).toBe('6px')
    expect(it.borderBottomWidth).toBe('1px')
    expect(it.cursor).toBe('pointer')
    expect(it.display, '成员条目应是 flex 行（行布局由 .group-row 提供）').toBe('flex')
    expect(it.alignItems, '成员条目应垂直居中（行布局由 .group-row 提供）').toBe('center')
    expect(it.styleAttr, '成员条目不应再有内联 style').toBeNull()

    // G2 名字
    const name = await computed(item.locator('.group-member-name'))
    expect(name.fontSize).toBe('15px')
    expect(name.fontWeight).toBe('600')

    // G3 身份行
    const identity = await computed(item.locator('.group-member-identity'))
    expect(identity.fontSize).toBe('13px')
    expect(identity.marginTop).toBe('2px')
    expect(identity.whiteSpace).toBe('nowrap')

    // G4 悬停：淡底色（不是透明）。background 有 0.15s 过渡，轮询等过渡结束再比具体值。
    expect(it.backgroundColor, '未悬停时应为透明').toBe('rgba(0, 0, 0, 0)')
    await item.hover()
    await expect.poll(async () => (await computed(item)).backgroundColor, { timeout: 3000 })
      .toBe(HOVER_BG)

    // S3 三处 .group-row 消费者同源：建群第 1 步卡片、第 2 步时期行，也必须是同一种行布局
    await page.locator('.messages-sidebar-header .btn-primary').click()
    await expect(page.locator('.group-create-card')).toHaveCount(2)
    const card = await computed(page.locator('.group-create-card').first())
    expect(card.display, '建群卡片应是 flex 行（行布局由 .group-row 提供）').toBe('flex')
    expect(card.alignItems).toBe('center')

    await page.locator('.group-create-card', { hasText: '甲' }).click()
    await page.locator('.group-create-card', { hasText: '乙' }).click()
    await page.locator('.group-create-modal .modal-actions .btn-primary').click()
    await expect(page.locator('.modal-label', { hasText: '你以谁的身份参与？' })).toBeVisible()
    const row = await computed(page.locator('.group-create-section', { hasText: '每个角色的时期' }).locator('.group-row').first())
    expect(row.display, '时期行应是 flex 行（行布局由 .group-row 提供）').toBe('flex')
    expect(row.alignItems).toBe('center')
  })

  test('G6-desktop 其余布局未变（消息区 / 成员面板 / 建群弹窗）', async ({ page }) => {
    await setup(page)
    await enterGroup(page)

    const body = await computed(page.locator('.private-chat-body'))
    expect(body.padding).toBe('16px'); expect(body.gap).toBe('8px')
    expect(body.backgroundColor).toBe('rgba(0, 0, 0, 0)'); expect(body.display).toBe('flex')
    expectWidth(body.width, 964)

    const area = await computed(page.locator('.group-chat-messages-area'))
    expect(area.padding).toBe('16px'); expect(area.gap).toBe('8px')
    expect(area.backgroundColor).toBe('rgba(0, 0, 0, 0)'); expect(area.display).toBe('flex')
    expectWidth(area.width, 932)

    await openMembers(page)
    const list = await computed(page.locator('.group-right-member-list'))
    expect(list.padding).toBe('8px 12px'); expect(list.gap).toBe('2px')
    expect(list.backgroundColor).toBe('rgba(0, 0, 0, 0)'); expect(list.display).toBe('flex')
    expectWidth(list.width, 287.094)

    // 建群弹窗第 1 步角色卡片 + 文字列
    await page.locator('.messages-sidebar-header .btn-primary').click()
    await expect(page.locator('.group-create-card')).toHaveCount(2)
    const card = await computed(page.locator('.group-create-card'))
    expect(card.padding).toBe('8px 10px'); expect(card.gap).toBe('10px')
    expect(card.borderRadius).toBe('6px'); expect(card.fontSize).toBe('14px')
    const cardInfo = await computed(page.locator('.group-create-card .group-row-info'))
    expect(cardInfo.flexGrow).toBe('1'); expect(cardInfo.minWidth).toBe('0px')

    // 第 2 步时期行 + 文字列
    await page.locator('.group-create-card', { hasText: '甲' }).click()
    await page.locator('.group-create-card', { hasText: '乙' }).click()
    await page.locator('.group-create-modal .modal-actions .btn-primary').click()
    await expect(page.locator('.modal-label', { hasText: '你以谁的身份参与？' })).toBeVisible()
    const section = page.locator('.group-create-section', { hasText: '每个角色的时期' })
    const row = await computed(section.locator('.group-row'))
    expect(row.padding).toBe('8px 10px'); expect(row.gap).toBe('10px')
    expect(row.borderRadius).toBe('6px'); expect(row.fontSize).toBe('14px')
    const rowInfo = await computed(section.locator('.group-row .group-row-info'))
    expect(rowInfo.flexGrow).toBe('1'); expect(rowInfo.minWidth).toBe('0px')
  })
})

test.describe('手机 390×844', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('G6-mobile 其余布局未变（消息区 / 成员面板）', async ({ page }) => {
    await setup(page)
    await enterGroup(page)

    // 移动端开右栏会整块卸载 main，故先测主区元素。
    const body = await computed(page.locator('.private-chat-body'))
    expect(body.padding).toBe('12px 12px 80px'); expect(body.gap).toBe('8px')
    expect(body.backgroundColor).toBe('rgba(0, 0, 0, 0)'); expect(body.display).toBe('flex')
    expectWidth(body.width, 388)

    const area = await computed(page.locator('.group-chat-messages-area'))
    expect(area.padding).toBe('16px'); expect(area.gap).toBe('8px')
    expect(area.backgroundColor).toBe('rgba(0, 0, 0, 0)'); expect(area.display).toBe('flex')
    expectWidth(area.width, 364)

    await openMembers(page)
    const list = await computed(page.locator('.group-right-member-list'))
    expect(list.padding).toBe('8px 12px'); expect(list.gap).toBe('2px')
    expect(list.backgroundColor).toBe('rgba(0, 0, 0, 0)'); expect(list.display).toBe('flex')
    expectWidth(list.width, 390)
  })
})

test('G5 一处定义：死类清空、行布局唯一、成员条目只写自己那几样', async () => {
  const count = (re) => (CSS.match(re) || []).length

  expect(count(/group-chat-layout/g), 'group-chat-layout 应为 0').toBe(0)
  expect(count(/group-create-row/g), 'group-create-row 应为 0').toBe(0)
  expect(count(/group-create-card-info/g), 'group-create-card-info 应为 0').toBe(0)
  expect(count(/\.group-row\s*\{/g), '.group-row 规则应恰有 1 条').toBe(1)
  expect(count(/\.group-row-info\s*\{/g), '.group-row-info 规则应恰有 1 条').toBe(1)

  // 白名单：以 .group-member-item 结尾的选择器里只允许自己特有的这几样；
  // 布局（display/padding/gap/align-items/border-radius/font-size/…）只能由 .group-row 管。
  const ALLOWED = new Set(['border-bottom', 'transition', 'cursor', 'background'])
  const rules = [...CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
  const guarded = []
  for (const [, selectorText, body] of rules) {
    const selectors = selectorText.split(',').map((s) => s.trim())
    if (!selectors.some((s) => /\.group-member-item(:[a-z-]+)?$/.test(s))) continue
    guarded.push(selectors.join(' , '))
    for (const decl of body.split(';')) {
      const prop = decl.split(':')[0].trim()
      if (!prop) continue
      expect(ALLOWED.has(prop), `「${selectors.join(', ')}」不允许出现 ${prop}（布局应交给 .group-row）`).toBe(true)
    }
  }
  expect(guarded.length, '应能找到以 .group-member-item 结尾的选择器').toBeGreaterThan(0)
})
