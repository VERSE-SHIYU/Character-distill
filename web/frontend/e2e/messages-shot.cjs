// 切片⑦ 截图：MessagesPage 列表/空态 + PrivateMessageChat 桌面/移动（testadmin，mock）
const { openApp, login, goToView, shot } = require('./helpers.cjs')

const FAKE_USER = {
  id: 'u-testadmin', username: 'testadmin', nickname: '', bio: '',
  avatar_data: '', banner_data: '', has_api_key: true, has_embedding_key: false,
  embedding_region: 'cn', base_url: 'https://api.deepseek.com', model: 'deepseek-v4-pro', is_admin: false,
}
const OTHER = { id: 'u-testuser', username: 'testuser', nickname: '山野闲人' }
const CONVS = [
  { other_id: OTHER.id, username: OTHER.username, nickname: OTHER.nickname, avatar_data: '', last_message: '那本《山海手记》你看了吗？', last_time: '2026-08-16T10:20:00', unread: 2 },
  { other_id: 'u-another', username: 'another_writer', nickname: '暮色旅人', avatar_data: '', last_message: '谢谢你的收藏', last_time: '2026-08-15T22:10:00', unread: 0 },
]
const MSGS = [
  { id: 'm1', sender_id: OTHER.id, content: '你好，最近在写什么新角色？', created_at: '2026-08-16T10:10:00' },
  { id: 'm2', sender_id: OTHER.id, content: '那本《山海手记》你看了吗？', created_at: '2026-08-16T10:20:00' },
]

async function mockRoutes(page, convs) {
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
  await page.route('**/api/auth/user/*/online', (r) => r.fulfill({ json: { online: true, hidden: false, last_active_at: '2026-08-16T10:30:00' } }))
  await page.route('**/api/market/my/following', (r) => r.fulfill({ json: { following: [] } }))
  await page.route('**/api/market/author**', (r) => r.fulfill({ json: { author: { id: OTHER.id, username: OTHER.username, nickname: OTHER.nickname, avatar_data: '' }, cards: [], texts: [] } }))
  await page.route('**/api/settings/config', (r) => r.fulfill({ json: { summary_threshold: 50 } }))
  await page.route('**/api/auth/me', (r) => r.fulfill({ json: FAKE_USER }))
  await page.route('**/api/messages/conversations', (r) => r.fulfill({ json: { conversations: convs } }))
  await page.route('**/api/messages/read**', (r) => r.fulfill({ json: { ok: true } }))
  await page.route('**/api/messages/with**', (r) => {
    if (r.request().url().includes('/reactions')) return r.fulfill({ json: { reactions: {} } })
    return r.fulfill({ json: { messages: MSGS } })
  })
}

async function main() {
  // 桌面列表
  {
    const { browser, page } = await openApp({ width: 1280, height: 900 })
    await mockRoutes(page, CONVS)
    await login(page, { settleMs: 1200 })
    await goToView(page, 'messages', { viaHome: true })
    await page.waitForSelector('.messages-conv-item', { timeout: 10000 })
    await shot(page, 'messages/list-desktop.png')
    await browser.close()
  }
  // 桌面聊天
  {
    const { browser, page } = await openApp({ width: 1280, height: 900 })
    await mockRoutes(page, CONVS)
    await login(page, { settleMs: 1200 })
    await goToView(page, 'messages', { viaHome: true })
    await page.waitForSelector('.messages-conv-item', { timeout: 10000 })
    await page.locator('.messages-conv-item').first().click()
    await page.waitForSelector('.private-chat-body', { timeout: 10000 })
    await page.waitForTimeout(300)
    await shot(page, 'messages/chat-desktop.png')
    await browser.close()
  }
  // 桌面空态
  {
    const { browser, page } = await openApp({ width: 1280, height: 900 })
    await mockRoutes(page, [])
    await login(page, { settleMs: 1200 })
    await goToView(page, 'messages', { viaHome: true })
    await page.waitForSelector('.messages-empty-state', { timeout: 10000 })
    await shot(page, 'messages/empty-desktop.png')
    await browser.close()
  }
  // 移动聊天
  {
    const { browser, page } = await openApp({ width: 390, height: 844 })
    await mockRoutes(page, CONVS)
    await login(page, { settleMs: 1200 })
    await goToView(page, 'messages', { viaHome: true })
    await page.waitForSelector('.messages-conv-item', { timeout: 10000 })
    await page.locator('.messages-conv-item').first().click()
    await page.waitForSelector('.private-chat-body', { timeout: 10000 })
    await page.waitForTimeout(300)
    await shot(page, 'messages/chat-mobile.png')
    await browser.close()
  }
  console.log('screenshots saved to e2e/screenshots/messages/')
}
main().catch(e => { console.error(e); process.exit(1) })
