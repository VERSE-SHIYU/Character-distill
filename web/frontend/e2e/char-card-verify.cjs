// 角色卡肖像卡探针：stage-glow hero + --font-display 角色名（宋体开关联动）+ 性格特征列表
// （.trait-item，高度随文字长度自适应，圆点为 ::before）
// 用法: node e2e/char-card-verify.cjs
const { openApp, login, pushView, shot } = require('./helpers.cjs')

// 60+ 字，用来证明 trait 高度随内容变高（旧样式固定 30px 会截断）
const LONG_TRAIT = '这是一个超过六十个字的性格特征描述，用来验证渲染高度能够随着文字长度自适应增长，而不是被固定高度截断掉；它应当自动换行并完整显示，不丢失任何文字。'

const CARD = {
  id: 'c1',
  text_id: 't1',
  name: '沈若言',
  created_at: '2026-08-01T10:00:00',
  card_json: JSON.stringify({
    name: '沈若言',
    identity: '深夜电台 · 主播',
    personality_traits: ['温柔', '倾听者', '慢热'],
    speaking_style: { tone: '安静 · 少打断', sentence_pattern: '短句居多', catchphrases: ['慢慢来', '我在听'] },
    values: ['真诚', '耐心'],
    key_memories: ['入行第十年，仍记得第一位来电者'],
    background: '深夜电台的主播，声音低沉温暖。',
    first_message: '夜已深，还没睡吗？',
    relationships: [{ target: '林晚', relation: '多年听众', attitude: '感激' }],
  }),
}

async function seed(page) {
  await page.route('**/api/history/list*', (r) => r.fulfill({ json: { items: [], total: 0, page: 1, page_size: 4 } }))
  await page.route('**/api/market/featured*', (r) => r.fulfill({ json: [] }))
  await page.route('**/api/distill/cards/by-text/t1', (r) => r.fulfill({ json: [CARD] }))
  await page.route('**/api/distill/cards/standalone', (r) => r.fulfill({ json: [] }))
  await page.evaluate((card) => {
    window.__appStore.setState({
      currentTextId: 't1',
      texts: [{ id: 't1', filename: 'demo.txt' }],
      cards: [card],
      currentCard: { ...card, ...JSON.parse(card.card_json), text_id: 't1' },
      identifiedChars: [],
    })
  }, CARD)
  await pushView(page, 'character')
  await page.waitForSelector('.card-hero', { timeout: 10000 })
}

const probe = (s) => ({
  heroRadius: parseFloat(s.heroRadius ?? 0),
  heroFlexDir: s.heroFlexDir,
  glow: s.glow,
  heroBottom: s.heroRect?.bottom,
  nameFontWeight: s.nameFontWeight,
  nameFontSize: s.nameFontSize,
  nameFontFamily: s.nameFontFamily,
  identityLs: s.identityLs ?? 0,
  avatarWidth: s.avatarWidth ?? 0,
  traitCount: s.traitCount,
  traitDots: s.traitDots,
  traitOverflow: !!s.traitOverflow,
  firstSectionTop: s.firstSectionTop,
  footerVisible: s.footerVisible,
  overflowX: s.overflowX,
})

const runDesktop = async (fail) => {
  const { browser, page, errors } = await openApp({ width: 1280, height: 900 })
  await login(page, { settleMs: 1200 })
  await seed(page)
  const s = await page.evaluate(() => {
    const hero = document.querySelector('.card-hero')
    const name = document.querySelector('.card-name')
    const identity = document.querySelector('.card-identity')
    const avatar = document.querySelector('.card-avatar-btn')
    const traits = [...document.querySelectorAll('.trait-list > .trait-item')]
    const sections = [...document.querySelectorAll('.card-section')]
    const footer = document.querySelector('.card-footer')
    const rect = (el) => el ? (() => { const r = el.getBoundingClientRect(); return { top: r.top, bottom: r.bottom } })() : null
    // 圆点是 ::before 伪元素，querySelector 查不到；把 --accent 经探针解析成 rgb 再与伪元素背景比
    const probeEl = document.createElement('span')
    probeEl.style.color = 'var(--accent)'
    document.body.appendChild(probeEl)
    const accent = getComputedStyle(probeEl).color
    probeEl.remove()
    return {
      heroRadius: hero ? getComputedStyle(hero).borderRadius : null,
      heroFlexDir: hero ? getComputedStyle(hero).flexDirection : null,
      glow: !!document.querySelector('.card-hero .stage-glow'),
      heroRect: rect(hero),
      nameFontWeight: name ? getComputedStyle(name).fontWeight : null,
      nameFontSize: name ? parseFloat(getComputedStyle(name).fontSize) : null,
      nameFontFamily: name ? getComputedStyle(name).fontFamily : null,
      identityLs: identity ? parseFloat(getComputedStyle(identity).letterSpacing) : null,
      avatarWidth: avatar ? avatar.getBoundingClientRect().width : null,
      traitCount: traits.length,
      traitDots: traits.filter((t) => {
        const b = getComputedStyle(t, '::before')
        return b.width === '6px' && b.backgroundColor === accent
      }).length,
      traitOverflow: traits.some((t) => t.scrollHeight > t.clientHeight + 1),
      firstSectionTop: sections[0] ? rect(sections[0]).top : null,
      footerVisible: footer ? footer.getBoundingClientRect().height > 0 : false,
      overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    }
  })
  const p = probe(s)
  console.log('DESKTOP', JSON.stringify(p))
  if (p.heroFlexDir !== 'column') fail.push('hero 应为列布局')
  if (p.heroRadius < 24) fail.push('hero 圆角 <24')
  if (!p.glow) fail.push('hero 缺 stage-glow')
  if (p.nameFontWeight !== '600') fail.push('角色名 weight 应 600: ' + p.nameFontWeight)
  if (p.nameFontSize < 22) fail.push('角色名 fontSize <22: ' + p.nameFontSize)
  if (p.identityLs < 0.8) fail.push('identity letter-spacing <0.8px: ' + p.identityLs)
  if (p.avatarWidth < 80) fail.push('头像 <80px: ' + p.avatarWidth)
  if (p.traitCount !== 3) fail.push('trait 条目应 3 个: ' + p.traitCount)
  if (p.traitDots !== 3) fail.push('trait 圆点应 3 个（6px + --accent）: ' + p.traitDots)
  if (p.traitOverflow) fail.push('trait 条目内容溢出容器（scrollHeight > clientHeight）')
  if (p.firstSectionTop == null || p.firstSectionTop < p.heroBottom - 1) fail.push('hero 与下方区块重叠: ' + p.firstSectionTop + ' < ' + p.heroBottom)
  if (!p.footerVisible) fail.push('footer 不可见')
  if (p.overflowX) fail.push('无横向溢出')
  await shot(page, 'char-card-desktop.png')

  // 自适应：注入一条 60+ 字长特征，高度必须高于短特征，且不溢出
  const longTrait = await page.evaluate((text) => {
    const list = document.querySelector('.trait-list')
    const li = document.createElement('li')
    li.className = 'trait-item'
    li.textContent = text
    list.appendChild(li)
    return {
      short: list.querySelector('.trait-item').getBoundingClientRect().height,
      long: li.getBoundingClientRect().height,
      overflow: li.scrollHeight > li.clientHeight + 1,
    }
  }, LONG_TRAIT)
  console.log('LONG-TRAIT', JSON.stringify(longTrait))
  if (!(longTrait.long > longTrait.short + 1)) fail.push('长特征未随内容变高: ' + longTrait.long + ' <= ' + longTrait.short)
  if (longTrait.overflow) fail.push('长特征溢出容器')

  // 宋体开关联动：html.serif-display 后 .card-name 切宋体
  await page.evaluate(() => document.documentElement.classList.add('serif-display'))
  await page.waitForTimeout(80)
  const serifFamily = await page.evaluate(() => {
    const n = document.querySelector('.card-name')
    return n ? getComputedStyle(n).fontFamily : null
  })
  await page.evaluate(() => document.documentElement.classList.remove('serif-display'))
  if (!/Songti|SimSun|STSong/i.test(serifFamily || '')) fail.push('serif-display 未作用于 .card-name: ' + serifFamily)
  else console.log('SERIF-LINKED card-name →', serifFamily)

  if (errors.length) fail.push('桌面 pageErrors: ' + JSON.stringify(errors))
  await browser.close()
}

const runMobile = async (fail) => {
  const { browser, page, errors } = await openApp({ width: 390, height: 844 })
  await login(page, { settleMs: 1200 })
  await seed(page)
  const m = await page.evaluate(() => {
    const hero = document.querySelector('.card-hero')
    const name = document.querySelector('.card-name')
    const avatar = document.querySelector('.card-avatar-btn')
    const sections = [...document.querySelectorAll('.card-section')]
    const hr = hero.getBoundingClientRect()
    const sr = sections[0]?.getBoundingClientRect()
    return {
      flexDir: hero ? getComputedStyle(hero).flexDirection : null,
      radius: hero ? parseFloat(getComputedStyle(hero).borderRadius) : null,
      glow: !!document.querySelector('.card-hero .stage-glow'),
      avatarWidth: avatar ? avatar.getBoundingClientRect().width : null,
      nameVisible: name ? name.getBoundingClientRect().height > 0 : false,
      noOverlap: sr ? sr.top >= hr.bottom - 1 : true,
      overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    }
  })
  console.log('MOBILE', JSON.stringify(m))
  if (m.flexDir !== 'column') fail.push('移动 hero 应为列布局')
  if (m.radius < 24) fail.push('移动 hero 圆角 <24')
  if (!m.glow) fail.push('移动 hero 缺 stage-glow')
  if (m.avatarWidth < 80) fail.push('移动头像 <80px: ' + m.avatarWidth)
  if (!m.nameVisible) fail.push('移动角色名不可见')
  if (!m.noOverlap) fail.push('移动 hero 与区块重叠')
  if (m.overflowX) fail.push('移动横向溢出')
  if (errors.length) fail.push('移动 pageErrors: ' + JSON.stringify(errors))
  await browser.close()
}

// 角色管理页没选文本（空态）：页头、返回、蒸馏工作台入口都在，空态内容可见，返回能离开
async function runEmptyState(fail) {
  const { browser, page, errors } = await openApp({ width: 1280, height: 800 })
  await login(page, { settleMs: 1200 })
  for (const w of [1280, 390]) {
    try {
    await page.setViewportSize({ width: w, height: w === 390 ? 844 : 800 })
    await page.evaluate(() => { const st = window.__appStore; st.getState().setView('home'); st.setState({ currentTextId: null, currentCard: null, texts: [] }) })
    await page.waitForTimeout(200)
    await pushView(page, 'character')
    await page.waitForSelector('.char-panel, .shell-placeholder', { timeout: 10000 })
    const e = await page.evaluate(() => {
      const q = (s) => document.querySelector(s)
      const inner = q('.shell-placeholder-inner')
      const r = inner ? inner.getBoundingClientRect() : null
      return {
        title: q('.char-panel .page-header-title')?.textContent || null,
        back: !!q('.char-panel .page-header-back'),
        entry: q('.char-panel .page-header .dw-entry-btn')?.textContent || null,
        emptyVisible: !!r && r.width > 0 && r.top < innerHeight,
      }
    })
    if (e.back) await page.locator('.char-panel .page-header-back').click()
    await page.waitForTimeout(400)
    e.leftPage = await page.evaluate(() => window.__appStore.getState().currentView !== 'character')
    console.log('EMPTY', w, JSON.stringify(e))
    if (e.title !== '角色管理') fail.push(`空态缺标题（宽 ${w}）`)
    if (!e.back) fail.push(`空态缺返回（宽 ${w}）`)
    if (e.entry !== '蒸馏工作台') fail.push(`空态缺蒸馏工作台入口（宽 ${w}）`)
    if (!e.emptyVisible) fail.push(`空态内容不可见（宽 ${w}）`)
    if (!e.leftPage) fail.push(`空态点返回没有离开（宽 ${w}）`)
    } catch (err) {
      fail.push(`空态检查异常（宽 ${w}）: ${String(err).split('\n')[0]}`)
    }
  }
  if (errors.length) fail.push('空态 pageErrors: ' + JSON.stringify(errors))
  await browser.close()
}

;(async () => {
  const fail = []
  await runDesktop(fail)
  await runMobile(fail)
  await runEmptyState(fail)
  console.log(fail.length ? '✗ FAIL\n - ' + fail.join('\n - ') : '✓ PASS')
  process.exit(fail.length ? 1 : 0)
})().catch((e) => { console.error(e); process.exit(1) })
