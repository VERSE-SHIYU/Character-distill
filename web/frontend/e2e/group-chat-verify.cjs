// 切片⑧ 探针：GroupChatPage 群聊列表 + 进群 + 成员/历史面板
// 断言：群列表 2 项（组名 display 字体）、进群标题/成员数/消息、成员名/历史发言人/角色卡名走 font-display
// （宋体开关联动）、历史面板 tab 切换、移动端列表↔聊天、无溢出
// 用法: node e2e/group-chat-verify.cjs
const { openApp, login, goToView, cs } = require('./helpers.cjs')

const FAKE_USER = {
  id: 'u-testadmin', username: 'testadmin', nickname: '', bio: '',
  avatar_data: '', banner_data: '', has_api_key: true, has_embedding_key: false,
  embedding_region: 'cn', base_url: 'https://api.deepseek.com', model: 'deepseek-v4-pro', is_admin: false,
}
const CARDS = {
  c1: { id: 'c1', name: '沈若言', card_json: JSON.stringify({ name: '沈若言', identity: '深夜电台 · 主播', personality_traits: ['温柔', '倾听者'] }), avatar_data: '' },
  c2: { id: 'c2', name: '林知夏', card_json: JSON.stringify({ name: '林知夏', identity: '古风 · 侠女', personality_traits: ['洒脱', '重情'] }), avatar_data: '' },
  c3: { id: 'c3', name: '顾之遥', card_json: JSON.stringify({ name: '顾之遥', identity: '城市 · 观测者', personality_traits: ['冷静'] }), avatar_data: '' },
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
  await page.route('**/api/cards/*', (r) => {
    const id = r.request().url().split('/').pop()
    return r.fulfill({ json: CARDS[id] || { id, name: '?', card_json: '{}', avatar_data: '' } })
  })
}

const isSerif = (f) => /Songti|SimSun|STSong/i.test(f || '')
const toggleSerif = (page, on) => page.evaluate((v) => {
  document.documentElement.classList.toggle('serif-display', v)
}, on)

async function runDesktop(fail) {
  const { browser, page, errors } = await openApp({ width: 1280, height: 900 })
  await mockRoutes(page)
  await login(page, { settleMs: 1200 })
  await goToView(page, 'groupChat', { viaHome: true })
  await page.waitForSelector('.messages-conv-item', { timeout: 10000 })
  await page.waitForTimeout(300)

  // 群列表
  const list = await page.evaluate(() => {
    const items = [...document.querySelectorAll('.messages-conv-item')].map(el => ({
      name: el.querySelector('.messages-conv-name')?.textContent.trim(),
      preview: el.querySelector('.messages-conv-preview')?.textContent.trim(),
      avatars: el.querySelectorAll('.group-avatar-stack .avatar, .group-avatar-stack img, .group-avatar-stack > *').length,
    }))
    return { convCount: items.length, items }
  })
  console.log('LIST', JSON.stringify(list))
  if (list.convCount !== 2) fail.push('群列表应 2 项: ' + list.convCount)
  if (list.items[0].name !== '深夜剧组') fail.push('第一群名应深夜剧组: ' + list.items[0].name)
  if (list.items[0].preview !== '沈若言、林知夏') fail.push('群预览应角色名: ' + list.items[0].preview)

  // 进群
  await page.locator('.messages-conv-item').first().click()
  await page.waitForSelector('.private-chat-body', { timeout: 10000 })
  await page.waitForSelector('.messages-row', { timeout: 10000 })
  await page.waitForTimeout(300)

  const chat = await page.evaluate(() => {
    const q = (sel) => document.querySelector(sel)
    return {
      title: q('.private-chat-title')?.textContent.trim() || null,
      count: q('.group-header-count')?.textContent.trim() || null,
      rows: document.querySelectorAll('.messages-row').length,
      activeConv: document.querySelectorAll('.messages-conv-item.active').length,
      emptyChat: !!q('.messages-empty-chat'),
      overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    }
  })
  console.log('CHAT', JSON.stringify(chat))
  if (chat.title !== '深夜剧组') fail.push('群聊标题应深夜剧组: ' + chat.title)
  if (!chat.count || !chat.count.includes('2 个角色')) fail.push('群成员数应 2: ' + chat.count)
  if (chat.rows < 2) fail.push('消息行应 ≥2: ' + chat.rows)
  if (chat.activeConv !== 1) fail.push('应有 1 个激活会话: ' + chat.activeConv)
  if (chat.emptyChat) fail.push('进群后不应显示空态')
  if (chat.overflowX) fail.push('桌面横向溢出')

  // 打开历史面板 → 历史 tab（默认）
  await page.locator('.chat-topbar-btn[title="历史记录"]').click()
  await page.waitForSelector('.group-right-tab-bar', { timeout: 10000 })
  await page.waitForSelector('.group-history-item', { timeout: 10000 })
  const hist = await page.evaluate(() => ({
    speakers: [...document.querySelectorAll('.group-history-item-speaker')].map(e => e.textContent.trim()),
    itemCount: document.querySelectorAll('.group-history-item').length,
  }))
  console.log('HISTORY', JSON.stringify(hist))
  if (hist.itemCount !== 2) fail.push('历史应 2 条: ' + hist.itemCount)
  if (hist.speakers.join(',') !== '沈若言,林知夏') fail.push('历史发言人错: ' + hist.speakers.join(','))

  // 切到成员 tab
  await page.locator('.group-right-tab', { hasText: '成员' }).click()
  await page.waitForSelector('.group-member-item', { timeout: 10000 })
  const members = await page.evaluate(() => ({
    names: [...document.querySelectorAll('.group-member-name')].map(e => e.textContent.trim()),
    identities: [...document.querySelectorAll('.group-member-identity')].map(e => e.textContent.trim()),
  }))
  console.log('MEMBERS', JSON.stringify(members))
  if (members.names.join(',') !== '沈若言,林知夏') fail.push('成员名错: ' + members.names.join(','))

  // 宋体开关：群名/成员名/历史发言人联动
  await toggleSerif(page, true)
  const sfConv = await cs(page, '.messages-conv-name', ['fontFamily'])
  const sfMember = await cs(page, '.group-member-name', ['fontFamily'])
  const sfHistory = await cs(page, '.group-history-item-speaker', ['fontFamily'])
  const sfCharInfo = await cs(page, '.group-char-info-name', ['fontFamily'])
  await toggleSerif(page, false)
  if (sfConv && !isSerif(sfConv.fontFamily)) fail.push('serif 未作用于群名: ' + sfConv.fontFamily)
  if (sfMember && !isSerif(sfMember.fontFamily)) fail.push('serif 未作用于成员名: ' + sfMember.fontFamily)
  if (sfHistory && !isSerif(sfHistory.fontFamily)) fail.push('serif 未作用于历史发言人: ' + sfHistory.fontFamily)
  if (sfCharInfo && !isSerif(sfCharInfo.fontFamily)) fail.push('serif 未作用于角色卡名: ' + sfCharInfo.fontFamily)

  await browser.close()
  return errors
}

async function runMobile(fail) {
  const { browser, page, errors } = await openApp({ width: 390, height: 844 })
  await mockRoutes(page)
  await login(page, { settleMs: 1200 })
  await goToView(page, 'groupChat', { viaHome: true })
  await page.waitForSelector('.messages-conv-item', { timeout: 10000 })

  const listShown = await page.evaluate(() => {
    const sidebar = document.querySelector('.messages-sidebar')
    return { sideDisplay: sidebar ? getComputedStyle(sidebar).display : null }
  })
  if (listShown.sideDisplay === 'none') fail.push('移动端群列表应显示')

  await page.locator('.messages-conv-item').first().click()
  await page.waitForSelector('.private-chat-body', { timeout: 10000 })
  await page.waitForTimeout(200)
  const chatShown = await page.evaluate(() => {
    const sidebar = document.querySelector('.messages-sidebar')
    return {
      sideDisplay: sidebar ? getComputedStyle(sidebar).display : null,
      backHeader: !!document.querySelector('.page-header-sticky, .group-chat-main .page-header'),
      overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    }
  })
  console.log('MOBILE', JSON.stringify(chatShown))
  if (chatShown.sideDisplay !== 'none') fail.push('移动进群应隐藏列表: ' + chatShown.sideDisplay)
  if (!chatShown.backHeader) fail.push('移动进群应有返回头部')
  if (chatShown.overflowX) fail.push('移动横向溢出')

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
