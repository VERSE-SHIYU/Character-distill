// 切片⑥ 截图：SettingsPanel 桌面 + ApiConfigPanel 桌面/移动（testadmin，mock 配置）
const { openApp, login, goToView, shot } = require('./helpers.cjs')

const FAKE_USER = {
  id: 'u-testadmin', username: 'testadmin', nickname: '', bio: '',
  avatar_data: '', banner_data: '',
  has_api_key: false, has_embedding_key: false, embedding_region: 'cn',
  base_url: '', model: '', is_admin: false,
}
const USAGE = { total_calls: 42, total_prompt_tokens: 1500, total_completion_tokens: 300, by_action: {}, by_model: {} }

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
  await page.route('**/api/market/author**', (r) => r.fulfill({ json: { author: { id: 'u-testadmin', username: 'testadmin' }, cards: [], texts: [] } }))
  await page.route('**/api/settings/config', (r) => r.fulfill({ json: { summary_threshold: 50 } }))
  await page.route('**/api/auth/me', (r) => r.fulfill({ json: FAKE_USER }))
  await page.route('**/api/auth/usage', (r) => r.fulfill({ json: USAGE }))
}

async function main() {
  // 桌面 settings 入口
  {
    const { browser, page } = await openApp({ width: 1280, height: 900 })
    await mockRoutes(page)
    await login(page, { settleMs: 1200 })
    await goToView(page, 'settings', { viaHome: true })
    await page.waitForSelector('.settings-logout-btn', { timeout: 10000 })
    await shot(page, 'settings/settings-desktop.png')
    await browser.close()
  }
  // 桌面 api config
  {
    const { browser, page } = await openApp({ width: 1280, height: 900 })
    await mockRoutes(page)
    await login(page, { settleMs: 1200 })
    await goToView(page, 'settings', { viaHome: true })
    await page.waitForSelector('.settings-logout-btn', { timeout: 10000 })
    await page.locator('.entry-list-item', { hasText: 'API 配置' }).click()
    await page.waitForSelector('.provider-card', { timeout: 10000 })
    await page.waitForTimeout(300)
    await shot(page, 'settings/api-desktop.png')
    await browser.close()
  }
  // 移动 api config
  {
    const { browser, page } = await openApp({ width: 390, height: 844 })
    await mockRoutes(page)
    await login(page, { settleMs: 1200 })
    await goToView(page, 'apiConfig', { viaHome: true })
    await page.waitForSelector('.provider-card', { timeout: 10000 })
    await page.waitForTimeout(300)
    await shot(page, 'settings/api-mobile.png')
    await browser.close()
  }
  console.log('screenshots saved to e2e/screenshots/settings/')
}
main().catch(e => { console.error(e); process.exit(1) })
