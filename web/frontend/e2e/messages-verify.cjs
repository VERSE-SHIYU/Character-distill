// 切片⑦ 探针：MessagesPage 私信列表 + PrivateMessageChat 聊天
// 断言：会话列表 2 项、conv 名字/聊天标题/空态标题走 font-display（宋体开关联动）、
// 在线状态用 --success 色点、点击会话进聊天（消息气泡）、移动端列表↔聊天切换、空态渲染、无溢出
// 用法: node e2e/messages-verify.cjs
const { openApp, login, goToView, cs } = require('./helpers.cjs')

const FAKE_USER = {
  id: 'u-testadmin', username: 'testadmin', nickname: '', bio: '',
  avatar_data: '', banner_data: '',
  has_api_key: true, has_embedding_key: false, embedding_region: 'cn',
  base_url: 'https://api.deepseek.com', model: 'deepseek-v4-pro', is_admin: false,
}
const OTHER = { id: 'u-testuser', username: 'testuser', nickname: '山野闲人' }

const CONVS = [
  { other_id: OTHER.id, username: OTHER.username, nickname: OTHER.nickname, avatar_data: '',
    last_message: '那本《山海手记》你看了吗？', last_time: '2026-08-16T10:20:00', unread: 2 },
  { other_id: 'u-another', username: 'another_writer', nickname: '暮色旅人', avatar_data: '',
    last_message: '谢谢你的收藏', last_time: '2026-08-15T22:10:00', unread: 0 },
]

async function mockRoutes(page, convs) {
  const msgs = [
    { id: 'm1', sender_id: OTHER.id, content: '你好，最近在写什么新角色？', created_at: '2026-08-16T10:10:00' },
    { id: 'm2', sender_id: OTHER.id, content: '那本《山海手记》你看了吗？', created_at: '2026-08-16T10:20:00' },
  ]
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
  await page.route('**/api/market/author**', (r) => {
    const url = r.request().url()
    if (url.includes('/followers')) return r.fulfill({ json: { followers: [] } })
    if (url.includes('/following')) return r.fulfill({ json: { following: [] } })
    if (url.includes('/posts')) return r.fulfill({ json: { posts: [] } })
    return r.fulfill({ json: { author: { id: OTHER.id, username: OTHER.username, nickname: OTHER.nickname, avatar_data: '' }, cards: [], texts: [] } })
  })
  await page.route('**/api/settings/config', (r) => r.fulfill({ json: { summary_threshold: 50 } }))
  await page.route('**/api/auth/me', (r) => r.fulfill({ json: FAKE_USER }))

  // ---- DM endpoints ----
  await page.route('**/api/messages/conversations', (r) => r.fulfill({ json: { conversations: convs } }))
  await page.route('**/api/messages/read**', (r) => r.fulfill({ json: { ok: true } }))
  await page.route('**/api/messages/with**', (r) => {
    const url = r.request().url()
    if (url.includes('/reactions')) return r.fulfill({ json: { reactions: {} } })
    return r.fulfill({ json: { messages: msgs } })
  })
}

const isSerif = (f) => /Songti|SimSun|STSong/i.test(f || '')
const toggleSerif = (page, on) => page.evaluate((v) => {
  document.documentElement.classList.toggle('serif-display', v)
}, on)

async function runDesktop(fail) {
  const { browser, page, errors } = await openApp({ width: 1280, height: 900 })
  await mockRoutes(page, CONVS)
  await login(page, { settleMs: 1200 })
  await goToView(page, 'messages', { viaHome: true })
  await page.waitForSelector('.messages-conv-item', { timeout: 10000 })
  await page.waitForTimeout(200)

  // 会话列表
  const list = await page.evaluate(() => {
    const q = (sel) => document.querySelector(sel)
    const layout = q('.messages-layout')
    const ls = layout ? getComputedStyle(layout) : null
    const items = [...document.querySelectorAll('.messages-conv-item')].map(el => ({
      name: el.querySelector('.messages-conv-name')?.textContent.trim(),
      badge: el.querySelector('.messages-conv-badge')?.textContent.trim() || null,
    }))
    return {
      convCount: items.length,
      items,
      layoutRadius: ls ? ls.borderRadius : null,
      layoutShadow: ls ? ls.boxShadow : null,
      nameFont: q('.messages-conv-name') ? getComputedStyle(q('.messages-conv-name')).fontFamily : null,
    }
  })
  console.log('LIST', JSON.stringify(list))
  if (list.convCount !== 2) fail.push('会话应 2 项: ' + list.convCount)
  if (list.items[0].name !== '山野闲人') fail.push('第一会话名应山野闲人: ' + JSON.stringify(list.items[0]))
  if (list.items[0].badge !== '2') fail.push('第一会话未读数应 2: ' + list.items[0].badge)
  if (list.layoutRadius !== '18px' && list.layoutRadius !== 'var(--radius-lg)') fail.push('messages 布局圆角应为 18px: ' + list.layoutRadius)
  if (isSerif(list.nameFont)) fail.push('conv 名字默认不应是宋体: ' + list.nameFont)

  // 点击第一会话 → 聊天
  await page.locator('.messages-conv-item').first().click()
  await page.waitForSelector('.dm', { timeout: 10000 })
  await page.waitForSelector('.dm-row', { timeout: 10000 })
  await page.waitForTimeout(200)

  const chat = await page.evaluate(() => {
    const q = (sel) => document.querySelector(sel)
    const title = q('.dm-peer-name')
    const status = q('.dm-peer-status')
    const dot = q('.dm-peer-status .dot')
    return {
      titleText: title?.textContent.trim() || null,
      titleFont: title ? getComputedStyle(title).fontFamily : null,
      statusText: status?.textContent.trim() || null,
      statusColor: status ? getComputedStyle(status).color : null,
      dotBg: dot ? getComputedStyle(dot).backgroundColor : null,
      rows: document.querySelectorAll('.dm-row').length,
      bubbles: document.querySelectorAll('.dm-bubble').length,
      emptyChat: !!q('.dm-empty'),
      overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    }
  })
  console.log('CHAT', JSON.stringify(chat))
  if (chat.titleText !== '山野闲人') fail.push('聊天标题应山野闲人: ' + chat.titleText)
  if (isSerif(chat.titleFont)) fail.push('聊天标题默认不应是宋体: ' + chat.titleFont)
  if (!chat.statusText || !chat.statusText.includes('当前在线')) fail.push('应显示当前在线: ' + chat.statusText)
  if (chat.statusColor !== 'rgb(34, 197, 94)') fail.push('在线状态应用 success 色: ' + chat.statusColor)
  if (chat.dotBg !== 'rgb(34, 197, 94)') fail.push('在线色点应用 success 色: ' + chat.dotBg)
  if (chat.rows < 2) fail.push('消息气泡行应 ≥2: ' + chat.rows)
  if (chat.bubbles < 2) fail.push('dm-bubble 应 ≥2: ' + chat.bubbles)
  if (chat.overflowX) fail.push('桌面横向溢出')

  // 宋体开关：conv 名字 + 聊天标题联动
  await toggleSerif(page, true)
  const sfName = await cs(page, '.messages-conv-name', ['fontFamily'])
  const sfTitle = await cs(page, '.dm-peer-name', ['fontFamily'])
  await toggleSerif(page, false)
  if (sfName && !isSerif(sfName.fontFamily)) fail.push('serif 未作用于 conv 名字: ' + sfName.fontFamily)
  if (sfTitle && !isSerif(sfTitle.fontFamily)) fail.push('serif 未作用于聊天标题: ' + sfTitle.fontFamily)

  await browser.close()
  return errors
}

async function runEmpty(fail) {
  const { browser, page, errors } = await openApp({ width: 1280, height: 900 })
  await mockRoutes(page, [])
  await login(page, { settleMs: 1200 })
  await goToView(page, 'messages', { viaHome: true })
  await page.waitForSelector('.messages-empty-state', { timeout: 10000 })

  const s = await page.evaluate(() => {
    const q = (sel) => document.querySelector(sel)
    return {
      title: q('.messages-empty-title')?.textContent.trim() || null,
      desc: q('.messages-empty-desc')?.textContent.trim() || null,
      icon: !!q('.messages-empty-icon'),
      overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    }
  })
  console.log('EMPTY', JSON.stringify(s))
  if (s.title !== '暂无私信') fail.push('空态标题应暂无私信: ' + s.title)
  if (!s.icon) fail.push('空态应有无图标')
  if (s.overflowX) fail.push('空态横向溢出')

  await toggleSerif(page, true)
  const sf = await cs(page, '.messages-empty-title', ['fontFamily'])
  await toggleSerif(page, false)
  if (sf && !isSerif(sf.fontFamily)) fail.push('serif 未作用于空态标题: ' + sf.fontFamily)

  await browser.close()
  return errors
}

async function runMobile(fail) {
  const { browser, page, errors } = await openApp({ width: 390, height: 844 })
  await mockRoutes(page, CONVS)
  await login(page, { settleMs: 1200 })
  await goToView(page, 'messages', { viaHome: true })
  await page.waitForSelector('.messages-conv-item', { timeout: 10000 })

  const listShown = await page.evaluate(() => {
    const sidebar = document.querySelector('.messages-sidebar')
    return { sideDisplay: sidebar ? getComputedStyle(sidebar).display : null }
  })
  console.log('MOBILE-LIST', JSON.stringify(listShown))
  if (listShown.sideDisplay === 'none') fail.push('移动端列表应显示')

  await page.locator('.messages-conv-item').first().click()
  await page.waitForSelector('.dm', { timeout: 10000 })
  await page.waitForTimeout(200)
  const chatShown = await page.evaluate(() => {
    const sidebar = document.querySelector('.messages-sidebar')
    const chat = document.querySelector('.messages-chat-area')
    return {
      sideDisplay: sidebar ? getComputedStyle(sidebar).display : null,
      chatDisplay: chat ? getComputedStyle(chat).display : null,
      backHeader: !!document.querySelector('.page-header-sticky'),
      overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    }
  })
  console.log('MOBILE-CHAT', JSON.stringify(chatShown))
  if (chatShown.sideDisplay !== 'none') fail.push('移动聊天态应隐藏列表: ' + chatShown.sideDisplay)
  if (chatShown.chatDisplay === 'none') fail.push('移动聊天态应显示聊天区')
  if (!chatShown.backHeader) fail.push('移动聊天应有返回头部')
  if (chatShown.overflowX) fail.push('移动横向溢出')

  await browser.close()
  return errors
}

;(async () => {
  const fail = []
  const errs = []
  const d = await runDesktop(fail)
  const e = await runEmpty(fail)
  const m = await runMobile(fail)
  errs.push(...d, ...e, ...m)
  console.log('pageErrors:', JSON.stringify(errs))
  if (errs.length) fail.push('存在 pageErrors: ' + JSON.stringify(errs))
  if (fail.length) {
    console.log('\nFAILURES:')
    fail.forEach(f => console.log('  ✗ ' + f))
    process.exit(1)
  }
  console.log('\nALL PASS ✓')
})().catch(e => { console.error(e); process.exit(1) })
