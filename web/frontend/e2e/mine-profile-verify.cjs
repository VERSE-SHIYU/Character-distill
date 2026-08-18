// 切片⑤ 探针：MinePage 个人中心（hero 光晕 + display 名字 + @id mono 行 + stat 卡片网格 + 编辑图标→ProfilePage）
// + ProfilePage 个人资料（卡片语言 + 统计栏 + 3 格 + 密码表单展开）+ 宋体开关联动 + 无溢出
// 用法: node e2e/mine-profile-verify.cjs
const { openApp, login, goToView, cs } = require('./helpers.cjs')

const MAIN_AUTHOR = {
  author: { id: 'u-testadmin', username: 'testadmin', nickname: '', bio: '资深测试员' },
  cards: [
    { id: 'c1', name: '沈若言', text_id: 't1', text_title: '深夜电台.txt', visibility: 'public', avatar_data: '', created_at: '2026-08-01T10:00:00', likes: 12, chat_count: 3, card_json: JSON.stringify({ name: '沈若言', identity: '深夜电台 · 主播', personality_traits: ['温柔', '倾听者'], speaking_style: {}, values: [], key_memories: [], background: '', relationships: [] }) },
    { id: 'c2', name: '林知夏', text_id: 't2', text_title: '山海手记.txt', visibility: 'private', avatar_data: '', created_at: '2026-08-02T10:00:00', likes: 0, chat_count: 1, card_json: JSON.stringify({ name: '林知夏', identity: '古风 · 侠女', personality_traits: ['洒脱'], speaking_style: {}, values: [], key_memories: [], background: '', relationships: [] }) },
    { id: 'c3', name: '顾之遥', text_id: 't1', text_title: '深夜电台.txt', visibility: 'public', avatar_data: '', created_at: '2026-08-03T10:00:00', likes: 5, chat_count: 9, card_json: JSON.stringify({ name: '顾之遥', identity: '城市 · 观测者', personality_traits: ['冷静'], speaking_style: {}, values: [], key_memories: [], background: '', relationships: [] }) },
  ],
  texts: [
    { id: 't1', title: '深夜电台', filename: '深夜电台.txt', cover_data: '' },
    { id: 't2', title: '山海手记', filename: '山海手记.txt', cover_data: '' },
  ],
  followers_count: 128,
  following_count: 7,
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
  // 作者数据：按 URL 分支，避免子路径（/followers 等）被主端点吃掉
  await page.route('**/api/market/author**', (r) => {
    const url = r.request().url()
    if (url.includes('/followers')) return r.fulfill({ json: { followers: [] } })
    if (url.includes('/following')) return r.fulfill({ json: { following: [] } })
    if (url.includes('/posts')) return r.fulfill({ json: { posts: [] } })
    return r.fulfill({ json: MAIN_AUTHOR })
  })
}

const isSerif = (f) => /Songti|SimSun|STSong/i.test(f || '')
const serifFamily = (page, sel) => page.evaluate((s) => {
  const el = document.querySelector(s)
  return el ? getComputedStyle(el).fontFamily : null
}, sel)
const toggleSerif = (page, on) => page.evaluate((v) => {
  document.documentElement.classList.toggle('serif-display', v)
}, on)

async function enterMine(page) {
  await goToView(page, 'mine', { viaHome: true })
  await page.waitForSelector('.mine-profile-section', { timeout: 10000 })
  await page.waitForSelector('.mine-stat-card', { timeout: 10000 })
}

async function runDesktop(fail) {
  const { browser, page, errors } = await openApp({ width: 1280, height: 900 })
  await mockRoutes(page)
  await login(page, { settleMs: 1200 })
  await enterMine(page)

  const s = await page.evaluate(() => {
    const q = (sel) => document.querySelector(sel)
    const rect = (sel) => {
      const el = q(sel)
      if (!el) return null
      const b = el.getBoundingClientRect()
      return { top: b.top, bottom: b.bottom, left: b.left, right: b.right }
    }
    const cards = [...document.querySelectorAll('.mine-stat-card')].map(el => ({
      num: el.querySelector('b')?.textContent,
      label: el.querySelector('span')?.textContent,
    }))
    return {
      glowCount: document.querySelectorAll('.mine-profile-section > .stage-glow').length,
      name: q('.mine-profile-name')?.textContent.trim(),
      idText: q('.mine-id')?.textContent.trim(),
      idMono: q('.mine-id') ? getComputedStyle(q('.mine-id')).fontFamily : null,
      statCards: cards,
      statGridCols: q('.mine-stat-grid') ? getComputedStyle(q('.mine-stat-grid')).gridTemplateColumns : null,
      statNumFont: q('.mine-stat-card b') ? getComputedStyle(q('.mine-stat-card b')).fontFamily : null,
      tabCount: document.querySelectorAll('.mine-tab').length,
      editIcon: !!q('.mine-edit-icon'),
      online: q('.mine-online')?.textContent.trim() || null,
      bannerRect: rect('.mine-banner'),
      statRect: rect('.mine-stat-grid'),
      tabRect: rect('.mine-tab-bar'),
      overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    }
  })
  console.log('DESKTOP', JSON.stringify(s))
  if (s.glowCount < 1) fail.push('profile-section 缺 stage-glow')
  if (!s.name || !s.name.includes('testadmin')) fail.push('名字未显示: ' + s.name)
  if (s.idText !== '@testadmin') fail.push('@id 行内容错: ' + s.idText)
  if (!/mono|JetBrains|Fira|Consolas/i.test(s.idMono || '')) fail.push('@id 行不是 mono 字体: ' + s.idMono)
  if (isSerif(s.statNumFont)) fail.push('stat 数字默认不应是宋体: ' + s.statNumFont)
  if (!s.statCards || s.statCards.length !== 4) fail.push('stat 卡应 4 张: ' + JSON.stringify(s.statCards))
  else {
    const labels = s.statCards.map(c => c.label).join(',')
    if (labels !== '角色,书籍,粉丝,关注') fail.push('stat 标签错: ' + labels)
    const nums = s.statCards.map(c => c.num).join(',')
    if (nums !== '3,2,128,7') fail.push('stat 数字错: ' + nums)
  }
  if (s.tabCount !== 5) fail.push('tab 应 5 个: ' + s.tabCount)
  if (!s.editIcon) fail.push('isMe 应显示编辑图标')
  if (s.online !== '在线') fail.push('在线状态未显示: ' + s.online)
  if (s.bannerRect && s.statRect && s.tabRect && s.statRect.top < s.bannerRect.bottom - 2) fail.push('stat 与 banner 重叠')
  if (s.overflowX) fail.push('桌面横向溢出')

  // 宋体开关：名字 + stat 数字联动
  await toggleSerif(page, true)
  const sf = await serifFamily(page, '.mine-profile-name')
  const sfNum = await serifFamily(page, '.mine-stat-card b')
  await toggleSerif(page, false)
  if (!isSerif(sf)) fail.push('serif 未作用于 mine-profile-name: ' + sf)
  if (!isSerif(sfNum)) fail.push('serif 未作用于 stat 数字: ' + sfNum)

  // 点击 stat 卡切 tab：点「书籍」→ 书籍内容
  await page.click('.mine-stat-card:nth-child(2)')
  await page.waitForTimeout(300)
  const bookTab = await page.evaluate(() => {
    const active = document.querySelector('.mine-tab.active')
    return { label: active?.textContent.trim() || null, books: document.querySelectorAll('.mine-book-cover-card').length }
  })
  console.log('BOOK TAB', JSON.stringify(bookTab))
  if (bookTab.label !== '书籍') fail.push('点书籍 stat 未切到书籍 tab: ' + bookTab.label)
  if (bookTab.books !== 2) fail.push('书籍 tab 应 2 本: ' + bookTab.books)

  // 编辑图标 → ProfilePage
  await page.click('.mine-edit-icon')
  await page.waitForSelector('.profile-page', { timeout: 10000 })
  await page.waitForSelector('.profile-grid-3 .profile-grid-item', { timeout: 10000 })
  await page.waitForTimeout(300)
  const p = await page.evaluate(() => {
    const cards = document.querySelectorAll('.profile-card')
    const radius = (sel) => {
      const el = document.querySelector(sel)
      return el ? getComputedStyle(el).borderRadius : null
    }
    return {
      view: document.querySelector('.profile-page') ? 'profile' : null,
      cardCount: cards.length,
      statCount: document.querySelectorAll('.profile-stat-item').length,
      grid3Count: document.querySelectorAll('.profile-grid-3 .profile-grid-item').length,
      cardRadius: radius('.profile-card'),
      statRadius: radius('.profile-stat-item'),
      titleFont: (() => { const t = document.querySelector('.profile-section-title'); return t ? getComputedStyle(t).fontFamily : null })(),
    }
  })
  console.log('PROFILE', JSON.stringify(p))
  if (p.view !== 'profile') fail.push('编辑图标未进 profile 视图')
  if (p.cardCount < 3) fail.push('profile 卡片应 ≥3: ' + p.cardCount)
  if (p.statCount !== 4) fail.push('profile 统计项应 4: ' + p.statCount)
  if (p.grid3Count !== 3) fail.push('3 格快捷项应 3: ' + p.grid3Count)
  if (p.cardRadius !== '26px' && p.cardRadius !== 'var(--radius-xl)') fail.push('profile-card 圆角应为 26px: ' + p.cardRadius)
  if (p.statRadius !== '18px' && p.statRadius !== 'var(--radius-lg)') fail.push('profile-stat-item 圆角应为 18px: ' + p.statRadius)

  // 展开修改密码表单
  await page.click('.profile-grid-3 .profile-grid-item:nth-child(2)')
  await page.waitForTimeout(300)
  const pw = await page.evaluate(() => ({
    formOpen: !!document.querySelector('.profile-password-form'),
    fields: document.querySelectorAll('.profile-password-form .profile-input').length,
  }))
  console.log('PASSWORD', JSON.stringify(pw))
  if (!pw.formOpen) fail.push('修改密码表单未展开')
  if (pw.fields !== 3) fail.push('密码表单应 3 个输入框: ' + pw.fields)

  await browser.close()
  return errors
}

async function runMobile(fail) {
  const { browser, page, errors } = await openApp({ width: 390, height: 844 })
  await mockRoutes(page)
  await login(page, { settleMs: 1200 })
  await enterMine(page)

  const s = await page.evaluate(() => {
    const q = (sel) => document.querySelector(sel)
    return {
      statCount: document.querySelectorAll('.mine-stat-card').length,
      statCols: q('.mine-stat-grid') ? getComputedStyle(q('.mine-stat-grid')).gridTemplateColumns : null,
      editIcon: !!q('.mine-edit-icon'),
      nameSize: q('.mine-profile-name') ? parseFloat(getComputedStyle(q('.mine-profile-name')).fontSize) : null,
      bannerH: q('.mine-banner') ? q('.mine-banner').getBoundingClientRect().height : null,
      overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    }
  })
  console.log('MOBILE', JSON.stringify(s))
  if (s.statCount !== 4) fail.push('移动 stat 卡应 4 张: ' + s.statCount)
  if (!s.editIcon) fail.push('移动应显示编辑图标')
  if (s.overflowX) fail.push('移动横向溢出')
  if (s.bannerH && s.bannerH > 115) fail.push('移动 banner 应 ≤110: ' + s.bannerH)

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
