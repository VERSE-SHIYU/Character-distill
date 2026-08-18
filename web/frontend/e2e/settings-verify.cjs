// 切片⑥ 探针：SettingsPanel 设置入口 + ApiConfigPanel API 配置卡片语言
// 断言：面板 600px 居中、6 张 settings-section 卡片（radius-xl+shadow-sm+card-bg+20px padding）、
// section 标题/用量数字走 font-display（宋体开关联动）、provider-card 激活态（accent 边框+内描边）、
// api-config-alert（radius-lg+--danger）、用量统计 3 格、移动端无溢出
// 用法: node e2e/settings-verify.cjs
const { openApp, login, goToView, cs } = require('./helpers.cjs')

const FAKE_USER = {
  id: 'u-testadmin', username: 'testadmin', nickname: '', bio: '',
  avatar_data: '', banner_data: '',
  has_api_key: false, has_embedding_key: false, embedding_region: 'cn',
  base_url: '', model: '', is_admin: false,
}
const USAGE = {
  total_calls: 42, total_prompt_tokens: 1500, total_completion_tokens: 300,
  by_action: { chat: { calls: 40, prompt_tokens: 1000, completion_tokens: 200 } },
  by_model: {},
}
const MAIN_AUTHOR = {
  author: { id: 'u-testadmin', username: 'testadmin', nickname: '', bio: '资深测试员' },
  cards: [], texts: [], followers_count: 128, following_count: 7,
  is_following: false, follows_me: false,
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
  await page.route('**/api/settings/config', (r) => r.fulfill({ json: { summary_threshold: 50 } }))
  await page.route('**/api/auth/me', (r) => r.fulfill({ json: FAKE_USER }))
  await page.route('**/api/auth/usage', (r) => r.fulfill({ json: USAGE }))
}

const isSerif = (f) => /Songti|SimSun|STSong/i.test(f || '')
const toggleSerif = (page, on) => page.evaluate((v) => {
  document.documentElement.classList.toggle('serif-display', v)
}, on)

async function runDesktop(fail) {
  const { browser, page, errors } = await openApp({ width: 1280, height: 900 })
  await mockRoutes(page)
  await login(page, { settleMs: 1200 })
  await goToView(page, 'settings', { viaHome: true })
  await page.waitForSelector('.settings-panel', { timeout: 10000 })
  await page.waitForSelector('.settings-logout-btn', { timeout: 10000 })

  // ---- SettingsPanel 入口 ----
  const hub = await page.evaluate(() => {
    const q = (sel) => document.querySelector(sel)
    const el = q('.settings-panel')
    const r = el.getBoundingClientRect()
    return {
      entries: document.querySelectorAll('.entry-list-item').length,
      logout: !!q('.settings-logout-btn'),
      maxWidth: el ? getComputedStyle(el).maxWidth : null,
      width: Math.round(r.width),
      left: Math.round(r.left),
      overflowY: el ? getComputedStyle(el).overflowY : null,
    }
  })
  console.log('HUB', JSON.stringify(hub))
  if (hub.entries < 5) fail.push('设置入口应 ≥5 项: ' + hub.entries)
  if (!hub.logout) fail.push('缺少退出登录按钮')
  if (hub.maxWidth !== '600px') fail.push('settings-panel maxWidth 应 600px: ' + hub.maxWidth)
  if (hub.width > 601) fail.push('设置面板宽度应 ≤600: ' + hub.width)
  if (hub.left < 50) fail.push('设置面板应居中（非贴边）: left=' + hub.left)
  if (hub.overflowY !== 'auto') fail.push('settings-panel 应可滚动: ' + hub.overflowY)

  // 点击「API 配置」入口 → ApiConfigPanel
  await page.locator('.entry-list-item', { hasText: 'API 配置' }).click()
  await page.waitForSelector('.api-config-alert', { timeout: 10000 })
  await page.waitForSelector('.provider-card', { timeout: 10000 })
  await page.waitForTimeout(300)

  // ---- ApiConfigPanel 卡片语言 ----
  const s = await page.evaluate(() => {
    const q = (sel) => document.querySelector(sel)
    const secs = [...document.querySelectorAll('.settings-section')].map(el => ({
      radius: getComputedStyle(el).borderRadius,
      bg: getComputedStyle(el).backgroundColor,
      shadow: getComputedStyle(el).boxShadow,
      padding: getComputedStyle(el).padding,
    }))
    const active = document.querySelector('.provider-card.active')
    const act = active ? getComputedStyle(active) : null
    const alert = q('.api-config-alert') ? getComputedStyle(q('.api-config-alert')) : null
    const title = q('.settings-section-title') ? getComputedStyle(q('.settings-section-title')).fontFamily : null
    return {
      secCount: secs.length,
      secRadius: secs.map(x => x.radius).join('|'),
      secShadow: secs.every(x => x.shadow && x.shadow !== 'none'),
      secPadding: secs.map(x => x.padding).join('|'),
      providerCount: document.querySelectorAll('.provider-card').length,
      activeCount: document.querySelectorAll('.provider-card.active').length,
      activeRadius: act ? act.borderRadius : null,
      activeBorder: act ? act.borderColor : null,
      activeShadow: act ? act.boxShadow : null,
      titleFont: title,
      alertRadius: alert ? alert.borderRadius : null,
      alertColor: alert ? alert.color : null,
      inputs: document.querySelectorAll('.settings-input').length,
      usageItems: document.querySelectorAll('.usage-stat-item').length,
      usageVal: q('.usage-stat-value')?.textContent.trim() || null,
      usageRadius: q('.usage-stat-item') ? getComputedStyle(q('.usage-stat-item')).borderRadius : null,
      overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    }
  })
  console.log('APICONFIG', JSON.stringify(s))
  if (s.secCount !== 6) fail.push('settings-section 应 6 张卡片: ' + s.secCount)
  if (s.secRadius.split('|').some((r) => r !== '26px' && r !== 'var(--radius-xl)')) fail.push('section 卡片圆角应为 26px: ' + s.secRadius)
  if (!s.secShadow) fail.push('section 卡片应有 shadow')
  if (s.secPadding.split('|').some((p) => p !== '20px')) fail.push('section 卡片 padding 应为 20px: ' + s.secPadding)
  if (s.providerCount !== 4) fail.push('provider-card 应 4 张: ' + s.providerCount)
  if (s.activeCount !== 2) fail.push('激活 provider-card 应 2 张: ' + s.activeCount)
  if (s.activeRadius !== '14px' && s.activeRadius !== 'var(--radius-md)') fail.push('provider-card 圆角应为 14px: ' + s.activeRadius)
  if (s.activeShadow === 'none' || !s.activeShadow) fail.push('激活 provider-card 应有内描边')
  if (isSerif(s.titleFont)) fail.push('section 标题默认不应是宋体: ' + s.titleFont)
  if (s.alertRadius !== '18px' && s.alertRadius !== 'var(--radius-lg)') fail.push('alert 圆角应为 18px: ' + s.alertRadius)
  if (s.alertColor !== 'rgb(239, 68, 68)' && s.alertColor !== '#ef4444') fail.push('alert 颜色应为 danger: ' + s.alertColor)
  if (s.usageItems !== 3) fail.push('用量统计应 3 格: ' + s.usageItems)
  if (s.usageVal !== '42') fail.push('用量第一格应为 42: ' + s.usageVal)
  if (s.usageRadius !== '14px' && s.usageRadius !== 'var(--radius-md)') fail.push('usage-stat-item 圆角应为 14px: ' + s.usageRadius)
  if (s.overflowX) fail.push('桌面横向溢出')

  // 宋体开关：section 标题 + 用量数字联动
  await toggleSerif(page, true)
  const sfTitle = await cs(page, '.settings-section-title', ['fontFamily'])
  const sfUsage = await cs(page, '.usage-stat-value', ['fontFamily'])
  await toggleSerif(page, false)
  if (sfTitle && !isSerif(sfTitle.fontFamily)) fail.push('serif 未作用于 section 标题: ' + sfTitle.fontFamily)
  if (sfUsage && !isSerif(sfUsage.fontFamily)) fail.push('serif 未作用于用量数字: ' + sfUsage.fontFamily)

  await browser.close()
  return errors
}

async function runMobile(fail) {
  const { browser, page, errors } = await openApp({ width: 390, height: 844 })
  await mockRoutes(page)
  await login(page, { settleMs: 1200 })
  await goToView(page, 'apiConfig', { viaHome: true })
  await page.waitForSelector('.settings-section', { timeout: 10000 })
  await page.waitForTimeout(300)

  const m = await page.evaluate(() => {
    const el = document.querySelector('.settings-panel')
    const r = el.getBoundingClientRect()
    return {
      width: Math.round(r.width),
      secCount: document.querySelectorAll('.settings-section').length,
      providerCount: document.querySelectorAll('.provider-card').length,
      overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    }
  })
  console.log('MOBILE', JSON.stringify(m))
  if (m.width > 391) fail.push('移动面板宽度应 ≈390: ' + m.width)
  if (m.secCount !== 6) fail.push('移动 section 应 6 张: ' + m.secCount)
  if (m.providerCount !== 4) fail.push('移动 provider-card 应 4 张: ' + m.providerCount)
  if (m.overflowX) fail.push('移动横向溢出')

  await browser.close()
  return errors
}

;(async () => {
  const fail = []
  const errs = []
  const d = await runDesktop(fail)
  const m = await runMobile(fail)
  errs.push(...d, ...m)
  console.log('pageErrors:', JSON.stringify(errs))
  if (errs.length) fail.push('存在 pageErrors: ' + JSON.stringify(errs))
  if (fail.length) {
    console.log('\nFAILURES:')
    fail.forEach(f => console.log('  ✗ ' + f))
    process.exit(1)
  }
  console.log('\nALL PASS ✓')
})().catch(e => { console.error(e); process.exit(1) })
