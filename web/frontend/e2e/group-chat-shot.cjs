// 切片⑧ 截图：GroupChatPage 群列表 + 进群聊天 + 成员面板 桌面/移动（testadmin，mock）
const { openApp, login, goToView, shot } = require('./helpers.cjs')

const FAKE_USER = {
  id: 'u-testadmin', username: 'testadmin', nickname: '', bio: '',
  avatar_data: '', banner_data: '', has_api_key: true, has_embedding_key: false,
  embedding_region: 'cn', base_url: 'https://api.deepseek.com', model: 'deepseek-v4-pro', is_admin: false,
}
const CARDS = {
  c1: { id: 'c1', name: '沈若言', card_json: JSON.stringify({ name: '沈若言', identity: '深夜电台 · 主播' }), avatar_data: '' },
  c2: { id: 'c2', name: '林知夏', card_json: JSON.stringify({ name: '林知夏', identity: '古风 · 侠女' }), avatar_data: '' },
  c3: { id: 'c3', name: '顾之遥', card_json: JSON.stringify({ name: '顾之遥', identity: '城市 · 观测者' }), avatar_data: '' },
}
const GROUPS = [
  { id: 'g1', name: '深夜剧组', card_ids: ['c1', 'c2'], created_at: '2026-08-16T09:00:00', user_persona_type: 'director' },
  { id: 'g2', name: '山海之旅', card_ids: ['c2', 'c3'], created_at: '2026-08-15T20:00:00', user_persona_type: 'director' },
]
const HISTORY = [
  { id: 'h1', role: 'assistant', speaker: '沈若言', speaker_card_id: 'c1', content: '夜深了，要不要听我放首歌？', created_at: '2026-08-16T09:10:00' },
  { id: 'h2', role: 'assistant', speaker: '林知夏', speaker_card_id: 'c2', content: '山中月色正好。', created_at: '2026-08-16T09:11:00' },
]

async function mockRoutes(page) {
  await page.route('**/api/announcement/active', (r) => r.fulfill({ json: {} }))
  await page.route('**/api/history/list*', (r) => r.fulfill({ json: { items: [], total: 0 } }))
  await page.route('**/api/market/featured*', (r) => r.fulfill({ json: [] }))
  await page.route('**/api/distill/cards/standalone', (r) => r.fulfill({ json: [] }))
  await page.route('**/api/distill/cards/by-text**', (r) => r.fulfill({ json: [] }))
  await page.route('**/api/text/list*', (r) => r.fulfill({ json: [] }))
  await page.route('**/api/text/reading-progress/all', (r) => r.fulfill({ json: [] }))
  await page.route('**/api/auth/banner', (r) => r.fulfill({ json: { banner_data: '' } }))
  await page.route('**/api/auth/avatar', (r) => r.fulfill({ json: { avatar_data: '' } }))
  await page.route('**/api/auth/presence-visibility', (r) => r.fulfill({ json: { presence_visibility: 'all' } }))
  await page.route('**/api/auth/user/*/online', (r) => r.fulfill({ json: { online: true, hidden: false, last_active_at: '' } }))
  await page.route('**/api/market/my/following', (r) => r.fulfill({ json: { following: [] } }))
  await page.route('**/api/market/author**', (r) => r.fulfill({ json: { author: { id: 'u-testadmin', username: 'testadmin' }, cards: [], texts: [] } }))
  await page.route('**/api/settings/config', (r) => r.fulfill({ json: { summary_threshold: 50 } }))
  await page.route('**/api/auth/me', (r) => r.fulfill({ json: FAKE_USER }))
  await page.route('**/api/group/list', (r) => r.fulfill({ json: { groups: GROUPS } }))
  await page.route('**/api/group/*/history', (r) => r.fulfill({ json: { messages: HISTORY } }))
  await page.route('**/api/group/*/affinities', (r) => r.fulfill({ json: [] }))
  await page.route('**/api/cards/*', (r) => r.fulfill({ json: CARDS[r.request().url().split('/').pop()] || { id: '?', name: '?', card_json: '{}', avatar_data: '' } }))
}

async function main() {
  // 桌面群列表
  {
    const { browser, page } = await openApp({ width: 1280, height: 900 })
    await mockRoutes(page)
    await login(page, { settleMs: 1200 })
    await goToView(page, 'groupChat', { viaHome: true })
    await page.waitForSelector('.messages-conv-item', { timeout: 10000 })
    await page.waitForTimeout(200)
    await shot(page, 'group/group-list-desktop.png')
    await browser.close()
  }
  // 桌面群聊
  {
    const { browser, page } = await openApp({ width: 1280, height: 900 })
    await mockRoutes(page)
    await login(page, { settleMs: 1200 })
    await goToView(page, 'groupChat', { viaHome: true })
    await page.waitForSelector('.messages-conv-item', { timeout: 10000 })
    await page.locator('.messages-conv-item').first().click()
    await page.waitForSelector('.messages-row', { timeout: 10000 })
    await page.waitForTimeout(400)
    await shot(page, 'group/group-chat-desktop.png')
    await browser.close()
  }
  // 桌面成员面板
  {
    const { browser, page } = await openApp({ width: 1280, height: 900 })
    await mockRoutes(page)
    await login(page, { settleMs: 1200 })
    await goToView(page, 'groupChat', { viaHome: true })
    await page.waitForSelector('.messages-conv-item', { timeout: 10000 })
    await page.locator('.messages-conv-item').first().click()
    await page.waitForSelector('.private-chat-body', { timeout: 10000 })
    await page.locator('.chat-topbar-btn[title="历史记录"]').click()
    await page.waitForSelector('.group-right-tab-bar', { timeout: 10000 })
    await page.locator('.group-right-tab', { hasText: '成员' }).click()
    await page.waitForSelector('.group-member-item', { timeout: 10000 })
    await page.waitForTimeout(300)
    await shot(page, 'group/group-members-desktop.png')
    await browser.close()
  }
  // 移动群聊
  {
    const { browser, page } = await openApp({ width: 390, height: 844 })
    await mockRoutes(page)
    await login(page, { settleMs: 1200 })
    await goToView(page, 'groupChat', { viaHome: true })
    await page.waitForSelector('.messages-conv-item', { timeout: 10000 })
    await page.locator('.messages-conv-item').first().click()
    await page.waitForSelector('.messages-row', { timeout: 10000 })
    await page.waitForTimeout(400)
    await shot(page, 'group/group-chat-mobile.png')
    await browser.close()
  }
  console.log('screenshots saved to e2e/screenshots/group/')
}
main().catch(e => { console.error(e); process.exit(1) })
