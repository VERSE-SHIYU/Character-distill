// e2e/split-layout-verify.cjs — 双栏「收起列表」+ 对话头部让位 浏览器验收（V1 收起展开｜V2 空状态兜底｜V3 头部让位｜V4 群聊改名｜V5 手机端标签｜V6 列表宽度｜V7 长列表可滚动）
// 状态机语义（aria、记忆、移动端、键盘）由 SplitLayout.test.jsx 覆盖，这里只测 jsdom 测不到的布局。
// 用法: TEST_PASSWORD=e2e-mock node e2e/split-layout-verify.cjs
//       TEST_PASSWORD=e2e-mock node e2e/split-layout-verify.cjs V1 V3   （只跑指定项，变异核验时用）
// 基线：docs/specs/split-list-collapse.md。登录与数据全走 page.route mock，不依赖真实账号；
// TEST_PASSWORD 只是 helpers 的存在性闸门，占位值即可。
const { openApp, login, shot } = require('./helpers.cjs')

const PAGES = ['chat', 'dm', 'group']
const KEY = (id) => `split_list_collapsed:${id}`
const WIDTHS = [769, 800, 900, 950, 990, 1024, 1280]
const ME = { id: 'u1', username: 'shiyu', nickname: '诗雨', role: 'user' }
const LONG_GROUP = '东纶宿舍夜聊群（失联三十一天）'

const CONVS = { conversations: [{ other_id: 'u2', username: 'wang', nickname: '汪东城的创作者', last_message: '在吗', last_time: '2026-09-28T08:00:00Z', unread: 2, avatar_data: '' }] }
const DM_MSGS = { messages: [{ id: 1, sender_id: 'u2', receiver_id: 'u1', content: '你好', created_at: '2026-09-28T08:00:00Z' }] }
const GROUPS = { groups: [{ id: 'g1', name: LONG_GROUP, card_ids: ['c1', 'c2', 'c3', 'c4', 'c5'], user_persona_type: 'user' }] }

// 只拦服务端接口（pathname 以 /api/ 开头）；不能用 '**/api/**' 通配 —— 开发服务器的
// /src/api/client.js 也会被它吃掉，页面就白屏了。
const apiPath = (u) => new URL(u).pathname
async function mockRoutes(page) {
  const table = [
    [/^\/api\/auth\/login$/, { access_token: 'e', refresh_token: 'e', user: ME }],
    [/^\/api\/auth\/me$/, ME],
    [/^\/api\/history\/list/, { items: [], total: 0 }],
    [/^\/api\/market\/location$/, { country: '澳大利亚', region: '' }],
    [/^\/api\/auth\/user\/[^/]+\/online$/, { online: true, last_active_at: '' }],
    [/^\/api\/messages\/conversations$/, CONVS],
    [/^\/api\/messages\/with\//, DM_MSGS],
    [/^\/api\/group\/list$/, GROUPS],
    [/^\/api\/group\/g1\/history$/, { messages: [] }],
    [/^\/api\/text\/list/, []],
    [/^\/api\/market\/featured/, []],
    [/^\/api\/distill\/cards\//, []],
  ]
  await page.route((u) => apiPath(u.toString()).startsWith('/api/'), (r) => {
    const p = apiPath(r.request().url())
    const hit = table.find(([re]) => re.test(p))
    return r.fulfill({ json: hit ? hit[1] : {} })
  })
}

// 进入某页并打开一个对话。返回后头部已渲染。
async function openPage(page, which) {
  if (which === 'chat') {
    await page.evaluate(() => window.__appStore.setState({
      currentCard: { id: 'c1', name: '汪东城', text_id: 1, user_id: 'u9' }, sessionId: 's1',
      sessionList: [{ id: 's1', card_id: 'c1', character_name: '汪东城', last_message: '你好', updated_at: '2026-09-28T08:00:00Z' }],
      messages: [{ role: 'assistant', content: '你好', timestamp: '2026-09-28T08:00:00Z' }],
      currentView: 'chat',
    }))
    await page.waitForSelector('.chat-desktop .dm-header', { timeout: 15000 })
  } else if (which === 'dm') {
    await page.evaluate(() => window.__appStore.setState({ currentView: 'messages', currentCard: { id: 'c1', user_id: 'u2', name: '汪东城' } }))
    await page.waitForSelector('.messages-conv-item', { timeout: 15000 })
    await page.locator('.messages-conv-item').first().click()
    await page.waitForSelector('.messages-chat-area .dm-header', { timeout: 15000 })
  } else {
    await page.evaluate(() => window.__appStore.setState({ currentView: 'groupChat' }))
    await page.waitForSelector('.messages-conv-item', { timeout: 15000 })
    await page.locator('.messages-conv-item').first().click()
    await page.waitForSelector('.private-chat-header', { timeout: 15000 })
  }
  await page.waitForTimeout(500)
}

// 进入某页但不打开对话（空状态）。chat 页没有该状态：桌面双栏只在有会话时渲染。
async function openPageEmpty(page, which) {
  const view = which === 'dm' ? 'messages' : 'groupChat'
  await page.evaluate((v) => window.__appStore.setState({ currentView: v }), view)
  await page.waitForSelector('.messages-conv-item', { state: 'attached', timeout: 15000 })
  await page.waitForTimeout(400)
}

// .split-list 是 SplitLayout 渲染的 display:contents 包装层，自身无盒子；宽度一律量它的子节点
const SEL = {
  chat: { layout: '.chat-desktop', main: '.conv-panel', header: '.chat-desktop .dm-header' },
  dm: { layout: '.messages-layout.split-layout', main: '.messages-chat-area', header: '.messages-chat-area .dm-header' },
  group: { layout: '.messages-layout.split-layout', main: '.messages-chat-area', header: '.private-chat-header' },
}
const STORE_ID = { chat: 'chat', dm: 'dm', group: 'group' }

const rect = (page, sel) => page.evaluate((s) => {
  const el = document.querySelector(s)
  if (!el) return null
  const b = el.getBoundingClientRect()
  return { l: Math.round(b.left), r: Math.round(b.right), t: Math.round(b.top), w: Math.round(b.width), h: Math.round(b.height) }
}, sel)

// 先切到中立视图让双栏卸载，再只清本功能的键（不能 localStorage.clear()：会连登录态一起清掉）
async function freshPage(browserPage, viewport) {
  await browserPage.setViewportSize(viewport)
  await browserPage.evaluate(() => window.__appStore.setState({ currentView: 'home' }))
  await browserPage.waitForTimeout(150)
  await browserPage.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith('split_list_collapsed:')).forEach((k) => localStorage.removeItem(k)))
}

const results = []
const record = (id, ok, detail) => {
  results.push({ id, ok, detail }); console.log(`${ok ? '✅' : '❌'} ${id} ${detail}`)
  // 变异核验时设 BAIL=1：第一个失败就退出，省得等其余项跑完
  if (!ok && process.env.BAIL) process.exit(1)
}
const want = process.argv.slice(2)
const run = (id) => want.length === 0 || want.includes(id)

;(async () => {
  const { browser, page, errors } = await openApp({ width: 1280, height: 800 })
  await mockRoutes(page)
  await login(page)

  // ── V1 收起/展开的几何（三页）──
  if (run('V1')) for (const which of PAGES) {
    await freshPage(page, { width: 1280, height: 800 }); await openPage(page, which)
    const s = SEL[which]
    const listBefore = await rect(page, `${s.layout} > .split-list > *`)
    if (!listBefore) { record(`V1-${which}-收起`, false, '列表根节点缺少 split-list 类，找不到列表'); continue }
    const mainBefore = await rect(page, s.main)
    const tBefore = await rect(page, `${s.header} .pane-toggle`)
    await page.locator(`${s.header} .pane-toggle`).click(); await page.waitForTimeout(250)
    const listHidden = await page.evaluate((sel) => { const e = document.querySelector(sel); return e ? getComputedStyle(e).display === 'none' : 'no-split-list-class' }, `${s.layout} > .split-list`)
    const mainAfter = await rect(page, s.main)
    const tAfter = await rect(page, `${s.header} .pane-toggle`)
    const grew = mainAfter.w - mainBefore.w
    record(`V1-${which}-收起`, listHidden === true && grew >= listBefore.w, `列表隐藏=${listHidden} 对话区变宽 ${grew}px（列表原宽 ${listBefore.w}px）`)
    // 按钮相对对话区左缘的位置在两种状态下一致 ±1px（不「跳位」）
    const dBefore = tBefore.l - mainBefore.l, dAfter = tAfter.l - mainAfter.l
    record(`V1-${which}-按钮不跳位`, Math.abs(dBefore - dAfter) <= 1 && Math.abs(tBefore.t - tAfter.t) <= 1, `相对对话区左缘 ${dBefore}→${dAfter}px，纵向 ${tBefore.t}→${tAfter.t}`)
    await page.locator(`${s.header} .pane-toggle`).click(); await page.waitForTimeout(250)
    const listBack = await rect(page, `${s.layout} > .split-list > *`)
    record(`V1-${which}-展开`, listBack && listBack.w === listBefore.w, `列表宽 ${listBack && listBack.w}px（原 ${listBefore.w}px）`)
    await shot(page, `split-${which}-expanded.png`)
    await page.locator(`${s.header} .pane-toggle`).click(); await page.waitForTimeout(250)
    await shot(page, `split-${which}-collapsed.png`)
  }

  // ── V2 空状态兜底：存了「收起」但没有打开的对话，列表必须可见 ──
  if (run('V2')) for (const which of ['dm', 'group']) {
    await freshPage(page, { width: 1280, height: 800 })
    await page.evaluate((k) => localStorage.setItem(k, '1'), KEY(STORE_ID[which]))
    await page.reload(); await page.waitForSelector('[class*="shell"]', { timeout: 15000 }); await page.waitForTimeout(800)
    await openPageEmpty(page, which)
    const r = await page.evaluate(() => {
      const l = document.querySelector('.messages-layout.split-layout')
      const list = l && l.querySelector(':scope > .split-list > *')
      return { st: l && l.dataset.listCollapsed, w: list ? list.getBoundingClientRect().width : -1 }
    })
    record(`V2-${which}`, r.st === 'false' && r.w > 0, `data-list-collapsed=${r.st} 列表宽=${Math.round(r.w)}px`)
  }

  // ── V3 头部让位：宽度扫描 ──
  if (run('V3')) for (const which of PAGES) {
    await freshPage(page, { width: 1280, height: 800 }); await openPage(page, which)
    const series = []
    for (const w of WIDTHS) {
      await page.setViewportSize({ width: w, height: 800 }); await page.waitForTimeout(250)
      series.push({ w, ...(await page.evaluate(([sel, kind]) => {
        const h = document.querySelector(sel)
        const vis = (el) => !!el && getComputedStyle(el).display !== 'none' && el.getBoundingClientRect().width > 0
        const hr = h.getBoundingClientRect()
        const o = { overflow: h.scrollWidth > h.clientWidth + 1 }
        if (kind === 'dm') {
          const nm = h.querySelector('.dm-peer-name')
          o.nameCut = nm.scrollWidth > nm.clientWidth + 1
          o.tag = vis(h.querySelector('.dm-peer-tag'))
          o.actionsIn = h.querySelector('.dm-header-actions').getBoundingClientRect().right <= hr.right + 1
        } else {
          const left = h.querySelector('.group-header-left')
          const title = h.querySelector('.private-chat-title')
          o.tailClipped = left.scrollWidth > left.clientWidth + 1
          o.titleW = Math.round(title.getBoundingClientRect().width)
          o.stack = vis(h.querySelector('.group-avatar-stack'))
          o.count = vis(h.querySelector('.group-header-count'))
          o.myAvatar = vis(h.querySelector('.group-header-my-avatar'))
          o.actionsIn = h.querySelector('.group-header-right').getBoundingClientRect().right <= hr.right + 1
        }
        o.toggle = vis(h.querySelector('.pane-toggle'))
        return o
      }, [SEL[which].header, which === 'group' ? 'group' : 'dm'])) })
    }
    const bad = series.filter((r) => r.overflow || !r.actionsIn || !r.toggle || r.nameCut || r.tailClipped || (r.titleW !== undefined && r.titleW < 75))
    // 单调性：越宽，次要信息只会出现、不会消失
    const mono = (k) => series.every((r, i) => i === 0 || !(series[i - 1][k] && !r[k]))
    const keys = which === 'group' ? ['stack', 'count'] : which === 'chat' ? ['tag'] : []
    const monoBad = keys.filter((k) => !mono(k))
    record(`V3-${which}`, bad.length === 0 && monoBad.length === 0,
      `违规宽度=[${bad.map((r) => r.w)}] 非单调=[${monoBad}] ` + series.map((r) => `${r.w}:${which === 'group' ? `${r.count ? 'C' : '-'}${r.stack ? 'S' : '-'}` : r.tag ? 'T' : '-'}`).join(' '))
    if (which === 'group') {
      const first = series[0], last = series[series.length - 1]
      record('V3-group-两端', !first.count && !first.stack && last.count && last.stack, `769: count=${first.count} stack=${first.stack}；1280: count=${last.count} stack=${last.stack}`)
    }
    if (which === 'chat') {
      const first = series[0], last = series[series.length - 1]
      record('V3-chat-两端', !first.tag && last.tag, `769: 地区标签=${first.tag}；1280: 地区标签=${last.tag}`)
    }
  }

  // ── V4 群聊：点开关不进改名；点群名仍进改名 ──
  if (run('V4')) {
    await freshPage(page, { width: 1280, height: 800 }); await openPage(page, 'group')
    await page.locator('.private-chat-header .pane-toggle').click(); await page.waitForTimeout(200)
    const afterToggle = await page.locator('.private-chat-title-input').count()
    if (afterToggle !== 0) {
      record('V4', false, `点开关后出现了改名框=${afterToggle}（开关点击冒泡到了群名）`)
    } else {
      await page.locator('.private-chat-header .pane-toggle').click(); await page.waitForTimeout(200)
      await page.locator('.private-chat-header .private-chat-title').click(); await page.waitForTimeout(200)
      const afterTitle = await page.locator('.private-chat-title-input').count()
      record('V4', afterTitle === 1, `点开关后改名框=${afterToggle}；点群名后改名框=${afterTitle}`)
    }
  }

  // ── V5 手机（390×844）：让位规则只在桌面布局生效，地区标签仍显示 ──
  if (run('V5')) {
    await freshPage(page, { width: 390, height: 844 })
    await page.evaluate(() => window.__appStore.setState({
      currentCard: { id: 'c1', name: '汪东城', text_id: 1, user_id: 'u9' }, sessionId: 's1',
      messages: [{ role: 'assistant', content: '你好', timestamp: '2026-09-28T08:00:00Z' }], currentView: 'chat' }))
    await page.waitForSelector('.dm-header .dm-peer-tag', { state: 'attached', timeout: 15000 })
    const d = await page.evaluate(() => getComputedStyle(document.querySelector('.dm-peer-tag')).display)
    record('V5', d !== 'none', `手机端 .dm-peer-tag display=${d}`)
  }

  // ── V6 展开时列表宽度保持原设计（角色对话 320px，C4 网格 minmax(300,320) 在桌面恒取 320；私信/群聊 280px），窄宽两端都测 ──
  if (run('V6')) for (const which of PAGES) for (const w of [769, 1280]) {
    await freshPage(page, { width: w, height: 800 }); await openPage(page, which)
    const r = await rect(page, `${SEL[which].layout} > .split-list > *`)
    const want = which === 'chat' ? 320 : 280
    record(`V6-${which}@${w}`, !!r && r.w === want, `列表宽 ${r && r.w}px（应为 ${want}px）`)
  }

  // ── V7 长列表（规模表：三个列表都无上限）：列表高度不超出双栏容器，且自身可滚动 ──
  if (run('V7')) {
    const N = 40
    const convs = { conversations: Array.from({ length: N }, (_, i) => ({ other_id: `u${i + 2}`, username: `p${i}`, nickname: `对话${i}`, last_message: '在吗', last_time: '2026-09-28T08:00:00Z', unread: 0, avatar_data: '' })) }
    const groups = { groups: Array.from({ length: N }, (_, i) => ({ id: i === 0 ? 'g1' : `g${i + 1}`, name: i === 0 ? LONG_GROUP : `群${i}`, card_ids: ['c1', 'c2'], user_persona_type: 'user' })) }
    const sessions = Array.from({ length: N }, (_, i) => ({ id: i === 0 ? 's1' : `s${i + 1}`, card_id: 'c1', character_name: `角色${i}`, last_message: '你好', updated_at: '2026-09-28T08:00:00Z' }))
    // 后注册的路由优先匹配：只覆盖这两个列表接口
    await page.route((u) => apiPath(u.toString()) === '/api/messages/conversations', (r) => r.fulfill({ json: convs }))
    await page.route((u) => apiPath(u.toString()) === '/api/group/list', (r) => r.fulfill({ json: groups }))
    for (const which of PAGES) {
      await freshPage(page, { width: 1280, height: 800 }); await openPage(page, which)
      if (which === 'chat') await page.evaluate((list) => window.__appStore.setState({ sessionList: list }), sessions)
      await page.waitForTimeout(300)
      const r = await page.evaluate((sel) => {
        const layout = document.querySelector(sel)
        const list = layout.querySelector(':scope > .split-list > *')
        // 可滚动的是列表本身或其内部的 .tl-list（角色对话）
        const sc = list.querySelector('.tl-list') || list
        const lb = layout.getBoundingClientRect(), b = list.getBoundingClientRect()
        return { overflowOut: Math.round(b.bottom - lb.bottom), scrolls: sc.scrollHeight > sc.clientHeight + 1 }
      }, SEL[which].layout)
      record(`V7-${which}`, r.overflowOut <= 1 && r.scrolls, `列表超出容器 ${r.overflowOut}px，可滚动=${r.scrolls}`)
    }
  }

  if (errors.length) console.log('pageerror:', errors)
  await browser.close()
  const failed = results.filter((r) => !r.ok)
  console.log(`\n共 ${results.length} 项，失败 ${failed.length} 项，pageerror ${errors.length} 条`)
  process.exit(failed.length || errors.length ? 1 : 0)
})().catch((e) => { console.error('脚本异常:', e); process.exit(2) })
