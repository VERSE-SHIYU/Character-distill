// 角色市场探针：桌面双栏（分类侧栏 + display 标题 + 搜索）/ 移动 chips 行 + 卡封面剧光光晕 + tag 过滤（真实后端参）
// 用法: node e2e/market-verify.cjs
const { openApp, login, pushView } = require('./helpers.cjs')

const TAGS = ['治愈', '古风', '科幻']

const makeCard = (id, name, identity, traits, tags, avatar_data = '') => ({
  id,
  name,
  text_id: 't1',
  card_json: JSON.stringify({
    name,
    identity,
    personality_traits: traits,
    speaking_style: { tone: '安静' },
    values: ['真诚'],
    key_memories: [],
    background: `背景：${name}`,
    relationships: [],
  }),
  user_id: 'u-other',
  avatar_data,
  forked_from: null,
  likes: 12,
  created_at: '2026-08-01T10:00:00',
  market_description: `${name} 的公开卡`,
  market_tags: tags.join(','),
  author_name: '青梧',
  author_avatar: '',
  text_title: 'demo.txt',
  comment_count: 2,
  is_remote: false,
  origin_region: null,
  liked_by_me: false,
})

const CARDS = [
  makeCard('m1', '沈若言', '深夜电台 · 主播', ['温柔', '倾听者', '慢热'], ['治愈']),
  makeCard('m2', '林知夏', '古风 · 侠女', ['洒脱', '重情'], ['古风']),
]

async function mockRoutes(page, listUrls) {
  await page.route('**/api/history/list*', (r) => r.fulfill({ json: { items: [], total: 0, page: 1, page_size: 4 } }))
  await page.route('**/api/market/featured*', (r) => r.fulfill({ json: [] }))
  await page.route('**/api/distill/cards/standalone', (r) => r.fulfill({ json: [] }))
  await page.route('**/api/distill/cards/by-text/t1', (r) => r.fulfill({ json: [] }))
  await page.route('**/api/market/tags*', (r) => r.fulfill({ json: { tags: TAGS } }))
  await page.route('**/api/market/list*', (r) => {
    listUrls.push(r.request().url())
    r.fulfill({ json: { cards: CARDS, total: CARDS.length, page: 1, page_size: 20 } })
  })
  await page.route('**/api/market/search*', (r) => r.fulfill({ json: { cards: [], total: 0, page: 1, page_size: 20 } }))
  await page.route('**/api/cards/m1/detail', (r) => r.fulfill({ json: { ...CARDS[0], visibility: 'public' } }))
  await page.route('**/api/market/card/m1/book-versions', (r) => r.fulfill({ json: { versions: [] } }))
  await page.route('**/api/market/m1/comments', (r) => r.fulfill({ json: { comments: [] } }))
}

const serifFamily = (page, sel) => page.evaluate((s) => {
  const el = document.querySelector(s)
  return el ? getComputedStyle(el).fontFamily : null
}, sel)

const toggleSerif = (page, on) => page.evaluate((v) => {
  document.documentElement.classList.toggle('serif-display', v)
}, on)

const isSerif = (f) => /Songti|SimSun|STSong/i.test(f || '')

const assertNoSerif = (fail, label, f) => {
  if (isSerif(f)) fail.push(label + ' 默认不应是宋体: ' + f)
}
const assertSerif = (fail, label, f) => {
  if (!isSerif(f)) fail.push(label + ' serif-display 未生效: ' + f)
}

const runDesktop = async (fail) => {
  const listUrls = []
  const { browser, page, errors } = await openApp({ width: 1280, height: 900 })
  await mockRoutes(page, listUrls)
  await login(page, { settleMs: 1200 })
  await pushView(page, 'market')
  await page.waitForSelector('.mkt-cat', { timeout: 10000 })
  await page.waitForSelector('.market-card-v2', { timeout: 10000 })

  const s = await page.evaluate(() => {
    const r = (sel) => {
      const el = document.querySelector(sel)
      if (!el) return null
      const b = el.getBoundingClientRect()
      return { top: b.top, bottom: b.bottom, left: b.left, right: b.right }
    }
    const layout = document.querySelector('.market-layout')
    const title = document.querySelector('.mkt-head-title')
    return {
      gridCols: layout ? getComputedStyle(layout).gridTemplateColumns : null,
      sideDisplay: getComputedStyle(document.querySelector('.market-side')).display,
      sideRect: r('.market-side'),
      mainRect: r('.market-main'),
      catCount: document.querySelectorAll('.mkt-cat').length,
      headDisplay: getComputedStyle(document.querySelector('.mkt-head')).display,
      titleSize: title ? parseFloat(getComputedStyle(title).fontSize) : null,
      titleWeight: title ? getComputedStyle(title).fontWeight : null,
      chipsDisplay: getComputedStyle(document.querySelector('.market-chips')).display,
      toolbarSearchDisplay: getComputedStyle(document.querySelector('.market-toolbar-search')).display,
      sortCount: document.querySelectorAll('.market-sort-btn').length,
      sortActiveBg: getComputedStyle(document.querySelector('.market-sort-btn.active')).backgroundColor,
      sortActiveColor: getComputedStyle(document.querySelector('.market-sort-btn.active')).color,
      coverGlows: document.querySelectorAll('.market-card-v2-cover .stage-glow').length,
      cardCount: document.querySelectorAll('.market-card-v2').length,
      headRect: r('.mkt-head'),
      toolbarRect: r('.market-toolbar'),
      firstCardRect: r('.market-card-v2'),
      overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    }
  })
  console.log('DESKTOP', JSON.stringify(s))
  if (!s.gridCols) fail.push('缺 .market-layout grid')
  else {
    const first = parseFloat(s.gridCols.split(' ')[0])
    if (Math.abs(first - 200) > 3) fail.push('侧栏列宽应 200px: ' + s.gridCols)
  }
  if (s.sideDisplay === 'none') fail.push('桌面侧栏不可见')
  if (s.sideRect && s.mainRect && s.sideRect.right > s.mainRect.left + 1) fail.push('侧栏与主区重叠')
  if (s.catCount !== TAGS.length + 1) fail.push('分类应 ' + (TAGS.length + 1) + ' 个: ' + s.catCount)
  if (s.headDisplay === 'none') fail.push('桌面 mkt-head 不可见')
  if (s.titleSize < 28) fail.push('mkt-head 标题字号 <28: ' + s.titleSize)
  if (s.titleWeight !== '700') fail.push('mkt-head 标题 weight 应 700: ' + s.titleWeight)
  if (s.chipsDisplay !== 'none') fail.push('桌面应隐藏 chips')
  if (s.toolbarSearchDisplay !== 'none') fail.push('桌面应隐藏 toolbar 搜索')
  if (s.sortCount !== 2) fail.push('排序 tab 应 2 个: ' + s.sortCount)
  if (s.sortActiveBg === 'rgba(0, 0, 0, 0)' || !s.sortActiveBg) fail.push('sort active 应 accent 填充: ' + s.sortActiveBg)
  if (s.sortActiveColor !== 'rgb(255, 255, 255)') fail.push('sort active 文字应为 on-accent 白: ' + s.sortActiveColor)
  if (s.coverGlows !== 2) fail.push('卡封面应有 2 个 stage-glow: ' + s.coverGlows)
  if (s.cardCount !== 2) fail.push('卡片应 2 张: ' + s.cardCount)
  if (s.headRect && s.toolbarRect && s.headRect.bottom > s.toolbarRect.top + 1) fail.push('mkt-head 压住 toolbar: ' + s.headRect.bottom + '>' + s.toolbarRect.top)
  if (!s.firstCardRect || s.firstCardRect.height <= 0) fail.push('首卡不可见')
  if (s.overflowX) fail.push('桌面横向溢出')

  // 宋体开关联动：mkt-head-title + market-card-v2-name
  assertNoSerif(fail, 'mkt-head-title', await serifFamily(page, '.mkt-head-title'))
  assertNoSerif(fail, 'market-card-v2-name', await serifFamily(page, '.market-card-v2-name'))
  await toggleSerif(page, true)
  await page.waitForTimeout(80)
  assertSerif(fail, 'mkt-head-title', await serifFamily(page, '.mkt-head-title'))
  assertSerif(fail, 'market-card-v2-name', await serifFamily(page, '.market-card-v2-name'))
  await toggleSerif(page, false)
  await page.waitForTimeout(80)

  // 点击分类「治愈」→ 重新拉取带 tag 参数
  const cats = page.locator('.mkt-cat')
  await cats.nth(1).click()
  await page.waitForTimeout(400)
  const activeCat = await page.evaluate(() => document.querySelector('.mkt-cat.is-active')?.textContent?.trim() || null)
  if (activeCat !== '治愈') fail.push('点击后 is-active 分类应为 治愈: ' + activeCat)
  const hit = listUrls.some((u) => decodeURIComponent(u).includes('tag=治愈'))
  if (!hit) fail.push('list 请求未带 tag=治愈: ' + JSON.stringify(listUrls.slice(-2)))

  // 桌面进详情：hero 光晕 + display 名 + trait 胶囊 + 不压 author bar
  await page.locator('.market-card-v2-name').first().click()
  await page.waitForSelector('.market-detail-name', { timeout: 10000 })
  const d = await page.evaluate(() => {
    const r = (sel) => {
      const el = document.querySelector(sel)
      if (!el) return null
      const b = el.getBoundingClientRect()
      return { top: b.top, bottom: b.bottom }
    }
    return {
      glow: !!document.querySelector('.market-detail-hero .stage-glow'),
      traitCount: document.querySelectorAll('.pill-trait').length,
      traitDots: document.querySelectorAll('.pill-trait .pill-trait-dot').length,
      heroRect: r('.market-detail-hero'),
      authorRect: r('.market-detail-author-bar'),
      overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    }
  })
  console.log('DETAIL', JSON.stringify(d))
  if (!d.glow) fail.push('详情 hero 缺 stage-glow')
  if (d.traitCount !== 3) fail.push('详情 trait 胶囊应 3 个: ' + d.traitCount)
  if (d.traitDots !== 3) fail.push('详情 trait 圆点应 3 个: ' + d.traitDots)
  if (d.heroRect && d.authorRect && d.heroRect.bottom > d.authorRect.top + 1) fail.push('详情 hero 压住 author bar')
  if (d.overflowX) fail.push('详情横向溢出')
  assertNoSerif(fail, 'market-detail-name', await serifFamily(page, '.market-detail-name'))
  await toggleSerif(page, true)
  await page.waitForTimeout(80)
  assertSerif(fail, 'market-detail-name', await serifFamily(page, '.market-detail-name'))
  await toggleSerif(page, false)

  if (errors.length) fail.push('桌面 pageErrors: ' + JSON.stringify(errors))
  await browser.close()
}

const runMobile = async (fail) => {
  const listUrls = []
  const { browser, page, errors } = await openApp({ width: 390, height: 844 })
  await mockRoutes(page, listUrls)
  await login(page, { settleMs: 1200 })
  await pushView(page, 'market')
  await page.waitForSelector('.market-chip', { timeout: 10000 })
  await page.waitForSelector('.market-card-v2', { timeout: 10000 })

  const m = await page.evaluate(() => {
    const r = (sel) => {
      const el = document.querySelector(sel)
      if (!el) return null
      const b = el.getBoundingClientRect()
      return { top: b.top, bottom: b.bottom, width: b.width }
    }
    return {
      sideDisplay: getComputedStyle(document.querySelector('.market-side')).display,
      headDisplay: getComputedStyle(document.querySelector('.mkt-head')).display,
      chipsDisplay: getComputedStyle(document.querySelector('.market-chips')).display,
      chipCount: document.querySelectorAll('.market-chip').length,
      activeChip: document.querySelector('.market-chip.is-active')?.textContent?.trim() || null,
      toolbarSearchDisplay: getComputedStyle(document.querySelector('.market-toolbar-search')).display,
      cardCount: document.querySelectorAll('.market-card-v2').length,
      coverGlows: document.querySelectorAll('.market-card-v2-cover .stage-glow').length,
      firstCardRect: r('.market-card-v2'),
      overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    }
  })
  console.log('MOBILE', JSON.stringify(m))
  if (m.sideDisplay !== 'none') fail.push('移动应隐藏侧栏')
  if (m.headDisplay !== 'none') fail.push('移动应隐藏 mkt-head')
  if (m.chipsDisplay === 'none') fail.push('移动应显示 chips')
  if (m.chipCount !== TAGS.length + 1) fail.push('移动 chips 应 ' + (TAGS.length + 1) + ' 个: ' + m.chipCount)
  if (m.activeChip !== '全部') fail.push('初始 is-active chip 应为 全部: ' + m.activeChip)
  if (m.toolbarSearchDisplay === 'none') fail.push('移动应显示 toolbar 搜索')
  if (m.cardCount !== 2) fail.push('移动卡片应 2 张: ' + m.cardCount)
  if (m.coverGlows !== 2) fail.push('移动卡封面应有 2 个 stage-glow: ' + m.coverGlows)
  if (!m.firstCardRect || m.firstCardRect.width <= 100) fail.push('移动卡过窄: ' + m.firstCardRect?.width)
  if (m.overflowX) fail.push('移动横向溢出')

  const chip = page.locator('.market-chip', { hasText: '治愈' })
  await chip.click()
  await page.waitForTimeout(400)
  const activeChip2 = await page.evaluate(() => document.querySelector('.market-chip.is-active')?.textContent?.trim() || null)
  if (activeChip2 !== '治愈') fail.push('点击后 is-active chip 应为 治愈: ' + activeChip2)
  const hit = listUrls.some((u) => decodeURIComponent(u).includes('tag=治愈'))
  if (!hit) fail.push('移动 list 请求未带 tag=治愈')

  if (errors.length) fail.push('移动 pageErrors: ' + JSON.stringify(errors))
  await browser.close()
}

;(async () => {
  const fail = []
  await runDesktop(fail)
  await runMobile(fail)
  console.log(fail.length ? '✗ FAIL\n - ' + fail.join('\n - ') : '✓ PASS')
  process.exit(fail.length ? 1 : 0)
})().catch((e) => { console.error(e); process.exit(1) })
