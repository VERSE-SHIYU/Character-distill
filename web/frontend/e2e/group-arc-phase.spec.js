// @ts-check
// ⑤ 段 A：建群第 2 步给每个 AI 人物选时期 + 群成员标签显示时期。全程 page.route 模拟
// （无真实后端、无凭据），注入 store 状态直达群聊页（沿用 e2e/arc-phase-select.spec.js 的写法）。
//
// 断言见 docs/specs/group-arc-phase.md §6：6 组触发链 + 两张截图。
// 用法：cd web/frontend && npx playwright test e2e/group-arc-phase.spec.js
import { test, expect } from '@playwright/test'

// 桌面宽度：群聊右栏（成员标签）默认与消息区并排，不用在移动端先切 pane。
test.use({ viewport: { width: 1280, height: 800 } })

const sel = true
const unsel = false
const cardJson = (name, phases, selectable) =>
  JSON.stringify({
    name,
    character_arc: { axis: 'x', selectable, phases: phases.map((l) => ({ label: l, state: l })) },
  })

// 甲/乙 可选（各 2 阶段），丙 不可选，丁 可选但无阶段（成员标签不显示阶段行）。
const CARDS = [
  { id: 'a', name: '甲', card_json: cardJson('甲', ['冷', '热'], sel) },
  { id: 'b', name: '乙', card_json: cardJson('乙', ['初', '终'], sel) },
  { id: 'c', name: '丙', card_json: cardJson('丙', ['单'], unsel) },
  { id: 'd', name: '丁', card_json: cardJson('丁', [], sel) },
]

// 布局用：名字长短不同（孔乙己 3 字 / 阿Q 2 字），都可有阶段。
const LONG_SHORT = [
  { id: 'a', name: '孔乙己', card_json: cardJson('孔乙己', ['冷', '热'], sel) },
  { id: 'b', name: '阿Q', card_json: cardJson('阿Q', ['初', '终'], sel) },
]

/** 模拟全部 /api/*，返回 sentCreate：POST /api/group/create 的请求体数组。 */
async function setup(page, { groups = [], cards = CARDS } = {}) {
  const sentCreate = []
  await page.route((u) => new URL(u).pathname.startsWith('/api/'), (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    const json = (body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/api/auth/me') return json({ id: 'u1', username: 't', has_api_key: true })
    if (path === '/api/group/list') return json({ groups })
    if (path.startsWith('/api/distill/cards/by-text/')) return json(cards)
    if (/^\/api\/cards\/.+/.test(path)) return json(cards.find((c) => c.id === path.split('/').pop()) || {})
    if (path.endsWith('/history')) return json({ messages: [] })
    if (path.endsWith('/affinities')) return json([])
    if (path === '/api/group/create') {
      let body = {}
      try { body = JSON.parse(route.request().postData() || '{}') } catch { /* ignore */ }
      sentCreate.push(body)
      // 后端落库后返回整行群（与 /api/group/list 同形状），前端据此走 enterGroup。
      return json({
        group: {
          id: 'gnew', name: body.name || '群聊',
          card_ids: body.card_ids || [],
          user_persona_type: body.user_persona_type || 'director',
          user_persona_card_id: body.user_persona_card_id || '',
          user_persona_name: body.user_persona_name || '',
          user_persona_desc: body.user_persona_desc || '',
          member_phases: body.member_phases || {},
          created_at: '2026-01-01T00:00:00Z',
        },
      })
    }
    return json({})
  })
  await page.addInitScript(() => {
    window.__E2E = true
    localStorage.setItem('auth_token', 'x')
  })
  await page.goto('/')
  await page.waitForFunction(() => window.__appStore?.getState().authUser, { timeout: 8000 })
  await page.evaluate(() => window.__appStore.setState({
    isLoggedIn: true,
    currentView: 'groupChat',
    authUser: { id: 'u1', username: 't' },
    texts: [{ id: 't1', filename: '书' }],
  }))
  await expect(page.locator('.group-chat-page')).toBeVisible({ timeout: 8000 })
  return sentCreate
}

async function openCreateStep2(page) {
  await page.locator('.messages-sidebar-header .btn-primary').click()
  await expect(page.locator('.group-create-modal')).toBeVisible()
  await expect(page.locator('.group-create-card')).toHaveCount(4)   // 甲/乙/丙/丁（同一本书）
  await page.locator('.group-create-card', { hasText: '甲' }).click()
  await page.locator('.group-create-card', { hasText: '乙' }).click()
  await page.locator('.group-create-modal .modal-actions .btn-primary').click()   // 下一步
  await expect(page.locator('.modal-label', { hasText: '你以谁的身份参与？' })).toBeVisible()
}

/** 打开右栏并切到「成员」标签（右栏已开就不重复点开）。 */
async function openMembers(page) {
  await page.locator('.chat-topbar-btn').first().waitFor({ state: 'visible', timeout: 8000 })
  const toggle = page.locator('.chat-topbar-btn[title="历史记录"]')   // 关着时才是「历史记录」
  if (await toggle.count()) await toggle.first().click()
  await page.getByRole('button', { name: /^成员 \(/ }).click()
}

test('建群第 2 步：可选卡各一个下拉默认最后阶段，不可选卡没有下拉', async ({ page }) => {
  await setup(page)
  await openCreateStep2(page)

  const rows = page.locator('.group-create-section', { hasText: '每个角色的时期' }).locator('.group-create-row')
  await expect(rows).toHaveCount(2)                                   // 丙 不可选 → 不在列表
  await expect(rows.filter({ hasText: '甲' }).locator('.ui-select')).toContainText('阶段 2 · 热')   // 默认最后阶段
  await expect(rows.filter({ hasText: '乙' }).locator('.ui-select')).toContainText('阶段 2 · 终')
  await expect(rows.filter({ hasText: '丙' })).toHaveCount(0)

  await page.screenshot({ path: 'e2e/group-arc-phase-step2.png' })
})

test('扮演某张卡：它的下拉消失；改回导演：回来', async ({ page }) => {
  await setup(page)
  await openCreateStep2(page)

  await page.locator('.persona-option', { hasText: '扮演已选角色' }).click()
  await page.locator('.persona-sub .persona-char-btn', { hasText: '甲' }).click()

  const rows = page.locator('.group-create-section', { hasText: '每个角色的时期' }).locator('.group-create-row')
  await expect(rows).toHaveCount(1)                                   // 甲 是扮演的 → 退出列表
  await expect(rows.filter({ hasText: '甲' })).toHaveCount(0)

  await page.locator('.persona-option', { hasText: '导演' }).click()
  await expect(rows).toHaveCount(2)                                   // 改回导演 → 甲 回来
})

test('选甲/乙/丙、扮演乙、改甲为阶段 1 → 创建 → 请求体 member_phases 恰为 { 甲: 1 }', async ({ page }) => {
  const sent = await setup(page)
  await page.locator('.messages-sidebar-header .btn-primary').click()
  await expect(page.locator('.group-create-card')).toHaveCount(4)
  await page.locator('.group-create-card', { hasText: '甲' }).click()
  await page.locator('.group-create-card', { hasText: '乙' }).click()
  await page.locator('.group-create-card', { hasText: '丙' }).click()   // 丙 不可选：不进时期列表
  await page.locator('.group-create-modal .modal-actions .btn-primary').click()   // 下一步
  await expect(page.locator('.modal-label', { hasText: '你以谁的身份参与？' })).toBeVisible()

  // 扮演乙：乙 从时期列表里退出
  await page.locator('.persona-option', { hasText: '扮演已选角色' }).click()
  await page.locator('.persona-sub .persona-char-btn', { hasText: '乙' }).click()

  await page.locator('.group-create-row', { hasText: '甲' }).locator('.ui-select').click()
  await page.getByRole('option', { name: '阶段 1 · 冷' }).click()
  await page.locator('.group-create-modal .modal-actions .btn-primary').click()   // 创建群聊

  await expect.poll(() => sent.length).toBe(1)
  expect(sent[0].card_ids).toEqual(['a', 'b', 'c'])
  // 乙 是扮演的、丙 不可选 → 只有甲（改成阶段 1）进请求体
  expect(sent[0].member_phases, `请求体：${JSON.stringify(sent[0])}`).toEqual({ a: 1 })
})

test('成员标签：新建群按 member_phases 显示；旧群无该列 → 最后阶段；无阶段卡不显示', async ({ page }) => {
  // 旧群（无 member_phases）：甲 有一阶段 → 显示最后阶段；丁 无阶段 → 不显示阶段行。
  await setup(page, { groups: [{ id: 'g1', name: '旧群', card_ids: ['a', 'd'], created_at: '2026-01-01T00:00:00Z' }] })
  await page.locator('.messages-conv-item', { hasText: '旧群' }).click()
  await openMembers(page)

  const items = page.locator('.group-right-member-list .group-member-item')
  await expect(items).toHaveCount(2)
  await expect(items.filter({ hasText: '甲' })).toContainText('阶段 2/2 · 热')   // 无该列 → 最后阶段
  await expect(items.filter({ hasText: '丁' })).not.toContainText('阶段')        // 无阶段卡 → 无阶段行

  // 新建群（带 member_phases）：甲 阶段 1、乙 最后阶段。
  await page.locator('.messages-sidebar-header .btn-primary').click()
  await expect(page.locator('.group-create-card')).toHaveCount(4)
  await page.locator('.group-create-card', { hasText: '甲' }).click()
  await page.locator('.group-create-card', { hasText: '乙' }).click()
  await page.locator('.group-create-modal .modal-actions .btn-primary').click()
  await page.locator('.group-create-row', { hasText: '甲' }).locator('.ui-select').click()
  await page.getByRole('option', { name: '阶段 1 · 冷' }).click()
  await page.locator('.group-create-modal .modal-actions .btn-primary').click()
  await openMembers(page)

  const nitems = page.locator('.group-right-member-list .group-member-item')
  await expect(nitems.filter({ hasText: '甲' })).toContainText('阶段 1/2 · 冷')
  await expect(nitems.filter({ hasText: '乙' })).toContainText('阶段 2/2 · 终')

  await page.screenshot({ path: 'e2e/group-arc-phase-members.png' })
})

test('建群成功直接进群：消息区为空、成员「尚未评估」；再从侧栏点进，扮演的卡不在成员里', async ({ page }) => {
  await setup(page)
  await openCreateStep2(page)

  await page.locator('.persona-option', { hasText: '扮演已选角色' }).click()
  await page.locator('.persona-sub .persona-char-btn', { hasText: '甲' }).click()
  await page.locator('.group-create-modal .modal-actions .btn-primary').click()   // 创建群聊

  // 直接进入新群：没有真实消息（只有「xx 加入了群聊」系统分隔），不刷新。
  await expect(page.locator('.group-chat-messages-area')).toBeVisible({ timeout: 8000 })
  await expect(page.locator('.group-chat-messages-area .messages-row')).toHaveCount(0)
  await openMembers(page)
  const items = page.locator('.group-right-member-list .group-member-item')
  await expect(items).toHaveCount(1)                                  // 选了 甲、乙；甲 是扮演的 → 成员只剩 乙
  await expect(items.filter({ hasText: '甲' })).toHaveCount(0)
  await expect(items.filter({ hasText: '乙' })).toContainText('尚未评估')   // 新群好感为空 → 尚未评估

  // 不刷新，从侧栏再点进这个新群：仍不出现扮演的卡。
  await page.locator('.messages-conv-item', { hasText: '群聊' }).click()
  await openMembers(page)
  await expect(page.locator('.group-right-member-list .group-member-item').filter({ hasText: '甲' })).toHaveCount(0)
})

// 问题 1：时期行以前借用成员条目类（没内边距、下拉跟着名字长度左右漂），现在用行布局类 group-create-row。
test('时期行布局：长短名字下拉右边缘对齐、相邻行下拉有间距；时期行不是可点卡片', async ({ page }) => {
  await setup(page, { cards: LONG_SHORT })
  await page.locator('.messages-sidebar-header .btn-primary').click()
  await expect(page.locator('.group-create-card')).toHaveCount(2)
  // 第 1 步角色卡拆类后仍是可点卡片
  await expect(page.locator('.group-create-card').first()).toHaveCSS('cursor', 'pointer')

  await page.locator('.group-create-card', { hasText: '孔乙己' }).click()
  await page.locator('.group-create-card', { hasText: '阿Q' }).click()
  await page.locator('.group-create-modal .modal-actions .btn-primary').click()

  const rows = page.locator('.group-create-section', { hasText: '每个角色的时期' }).locator('.group-create-row')
  await expect(rows).toHaveCount(2)
  // 时期行只有行布局、没有卡片交互：cursor 不是 pointer
  await expect(rows.first()).not.toHaveCSS('cursor', 'pointer')

  const s0 = await rows.nth(0).locator('.ui-select').boundingBox()
  const s1 = await rows.nth(1).locator('.ui-select').boundingBox()
  // 名字长短不一，下拉右边缘仍对齐
  expect(Math.abs((s0.x + s0.width) - (s1.x + s1.width))).toBeLessThanOrEqual(1)
  // 相邻两行的下拉包围盒上下间距 ≥ 4px（行本体相邻；行内 8px padding 提供呼吸感，量谁见报告）
  expect(s1.y - (s0.y + s0.height)).toBeGreaterThanOrEqual(4)

  await page.screenshot({ path: 'e2e/group-arc-phase-step2-names.png' })
})
