// 切片⑤ 截图：MinePage 桌面/移动 + ProfilePage（testadmin，mock 作者数据）
const { openApp, login, goToView, shot } = require('./helpers.cjs')

const MAIN_AUTHOR = {
  author: { id: 'u-testadmin', username: 'testadmin', nickname: '', bio: '资深测试员，专注角色蒸馏质量验证。' },
  cards: [
    { id: 'c1', name: '沈若言', text_id: 't1', text_title: '深夜电台.txt', visibility: 'public', avatar_data: '', created_at: '2026-08-01T10:00:00', likes: 128, chat_count: 1024, card_json: JSON.stringify({ name: '沈若言', identity: '深夜电台 · 主播', personality_traits: ['温柔', '倾听者', '慢热'], speaking_style: {}, values: [], key_memories: [], background: '', relationships: [] }) },
    { id: 'c2', name: '林知夏', text_id: 't2', text_title: '山海手记.txt', visibility: 'public', avatar_data: '', created_at: '2026-08-02T10:00:00', likes: 86, chat_count: 512, card_json: JSON.stringify({ name: '林知夏', identity: '古风 · 侠女', personality_traits: ['洒脱', '重情'], speaking_style: {}, values: [], key_memories: [], background: '', relationships: [] }) },
    { id: 'c3', name: '顾之遥', text_id: 't1', text_title: '深夜电台.txt', visibility: 'private', avatar_data: '', created_at: '2026-08-03T10:00:00', likes: 32, chat_count: 98, card_json: JSON.stringify({ name: '顾之遥', identity: '城市 · 观测者', personality_traits: ['冷静', '敏锐'], speaking_style: {}, values: [], key_memories: [], background: '', relationships: [] }) },
  ],
  texts: [
    { id: 't1', title: '深夜电台', filename: '深夜电台.txt', cover_data: '' },
    { id: 't2', title: '山海手记', filename: '山海手记.txt', cover_data: '' },
  ],
  followers_count: 1284,
  following_count: 37,
  is_following: false,
  follows_me: false,
}

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
  await page.route('**/api/auth/user/*/online', (r) => r.fulfill({ json: { online: true, last_active_at: '' } }))
  await page.route('**/api/market/my/following', (r) => r.fulfill({ json: { following: [] } }))
  await page.route('**/api/market/author**', (r) => {
    const url = r.request().url()
    if (url.includes('/followers')) return r.fulfill({ json: { followers: [] } })
    if (url.includes('/following')) return r.fulfill({ json: { following: [] } })
    if (url.includes('/posts')) return r.fulfill({ json: { posts: [] } })
    return r.fulfill({ json: MAIN_AUTHOR })
  })
}

async function main() {
  // 桌面 mine
  {
    const { browser, page } = await openApp({ width: 1280, height: 900 })
    await mockRoutes(page)
    await login(page, { settleMs: 1200 })
    await goToView(page, 'mine', { viaHome: true })
    await page.waitForSelector('.mine-stat-card', { timeout: 10000 })
    await shot(page, 'mine/mine-desktop.png')
    await browser.close()
  }
  // 桌面 profile
  {
    const { browser, page } = await openApp({ width: 1280, height: 900 })
    await mockRoutes(page)
    await login(page, { settleMs: 1200 })
    await goToView(page, 'mine', { viaHome: true })
    await page.waitForSelector('.mine-edit-icon', { timeout: 10000 })
    await page.click('.mine-edit-icon')
    await page.waitForSelector('.profile-page', { timeout: 10000 })
    await page.waitForTimeout(400)
    await shot(page, 'mine/profile-desktop.png')
    await browser.close()
  }
  // 移动 mine
  {
    const { browser, page } = await openApp({ width: 390, height: 844 })
    await mockRoutes(page)
    await login(page, { settleMs: 1200 })
    await goToView(page, 'mine', { viaHome: true })
    await page.waitForSelector('.mine-stat-card', { timeout: 10000 })
    await shot(page, 'mine/mine-mobile.png')
    await browser.close()
  }
  console.log('screenshots saved to e2e/screenshots/mine/')
}
main().catch(e => { console.error(e); process.exit(1) })
