// 市场卡片详情页头部操作按钮一致性探针
// 断言：复制/编辑/删除 全部走 Icon 库（有 vertical-align:middle、统一 16px）、无内联手写 svg、删除按钮有 danger 类
// 用法: node e2e/market-detail-header-icons.cjs
const { openApp, login, pushView } = require('./helpers.cjs')

const makeCard = (userId) => ({
  id: 'm1',
  name: '沈若言',
  text_id: 't1',
  card_json: JSON.stringify({
    name: '沈若言',
    identity: '深夜电台 · 主播',
    personality_traits: ['温柔', '倾听者', '慢热'],
    speaking_style: { tone: '安静' },
    values: ['真诚'],
    key_memories: [],
    background: '背景：沈若言',
    relationships: [],
  }),
  user_id: userId,
  avatar_data: '',
  forked_from: null,
  likes: 12,
  created_at: '2026-08-01T10:00:00',
  market_description: '沈若言 的公开卡',
  market_tags: '治愈',
  author_name: '青梧',
  author_avatar: '',
  text_title: 'demo.txt',
  comment_count: 2,
  is_remote: false,
  origin_region: null,
  liked_by_me: false,
})

;(async () => {
  const fail = []
  const { browser, page, errors } = await openApp({ width: 1280, height: 900 })
  await login(page, { settleMs: 1200 })

  const myId = await page.evaluate(() => window.__appStore?.getState()?.authUser?.id || null)
  const card = makeCard(myId)

  await page.route('**/api/history/list*', (r) => r.fulfill({ json: { items: [], total: 0, page: 1, page_size: 4 } }))
  await page.route('**/api/market/featured*', (r) => r.fulfill({ json: [] }))
  await page.route('**/api/distill/cards/standalone', (r) => r.fulfill({ json: [] }))
  await page.route('**/api/distill/cards/by-text/t1', (r) => r.fulfill({ json: [] }))
  await page.route('**/api/market/tags*', (r) => r.fulfill({ json: { tags: ['治愈'] } }))
  await page.route('**/api/market/list*', (r) => r.fulfill({ json: { cards: [card], total: 1, page: 1, page_size: 20 } }))
  await page.route('**/api/market/search*', (r) => r.fulfill({ json: { cards: [], total: 0, page: 1, page_size: 20 } }))
  await page.route('**/api/cards/m1/detail', (r) => r.fulfill({ json: { ...card, visibility: 'public' } }))
  await page.route('**/api/market/card/m1/book-versions', (r) => r.fulfill({ json: { versions: [] } }))
  await page.route('**/api/market/m1/comments', (r) => r.fulfill({ json: { comments: [] } }))

  await pushView(page, 'market')
  await page.waitForSelector('.market-card-v2', { timeout: 10000 })
  await page.locator('.market-card-v2-name').first().click()
  await page.waitForSelector('.market-detail-name', { timeout: 10000 })

  const info = await page.evaluate(() => {
    const btns = Array.from(document.querySelectorAll('.page-header-actions .btn-icon'))
    return btns.map((b) => {
      const svg = b.querySelector('svg')
      const st = svg ? getComputedStyle(svg) : null
      const bs = getComputedStyle(b)
      const r = b.getBoundingClientRect()
      return {
        title: b.title,
        cls: b.className,
        svgCount: b.querySelectorAll('svg').length,
        rawSvgPaths: svg ? svg.querySelectorAll('path, polyline, line, circle, rect, polygon').length : 0,
        width: svg?.getAttribute('width'),
        verticalAlign: st?.verticalAlign || null,
        btnW: Math.round(r.width),
        btnH: Math.round(r.height),
        btnBorder: bs.borderTopWidth,
        btnBg: bs.backgroundColor,
        radius: bs.borderRadius,
      }
    })
  })

  console.log('AUTH_ID', myId)
  console.log('BTNS', JSON.stringify(info, null, 2))

  if (info.length !== 3) fail.push('应有 3 个头部操作按钮(复制/编辑/删除): ' + info.length + ' → ' + info.map((b) => b.title).join(','))
  const titles = info.map((b) => b.title)
  if (!titles.includes('复制链接')) fail.push('缺 复制链接 按钮')
  if (!titles.includes('编辑')) fail.push('缺 编辑 按钮')
  if (!titles.includes('删除')) fail.push('缺 删除 按钮')

  for (const b of info) {
    if (b.svgCount !== 1) fail.push(b.title + ' 应渲染 1 个 svg, 实际 ' + b.svgCount)
    if (b.width !== '16') fail.push(b.title + ' 图标尺寸应 16px, 实际 ' + b.width)
    if (b.verticalAlign !== 'middle') fail.push(b.title + ' 图标应走 Icon 库(vertical-align:middle), 实际 ' + b.verticalAlign)
    if (b.rawSvgPaths === 0) fail.push(b.title + ' 图标无图形路径')
    if (b.btnW !== 32 || b.btnH !== 32) fail.push(b.title + ' 按钮应 32x32, 实际 ' + b.btnW + 'x' + b.btnH)
    if (b.btnBorder !== '0px') fail.push(b.title + ' 按钮应无边框, 实际 ' + b.btnBorder)
    if (b.btnBg !== 'rgba(0, 0, 0, 0)') fail.push(b.title + ' 应应用 .btn-icon 透明背景, 实际 ' + b.btnBg)
    if (!b.radius.includes('8')) fail.push(b.title + ' 按钮圆角应 8px, 实际 ' + b.radius)
  }

  const del = info.find((b) => b.title === '删除')
  if (del && !del.cls.includes('danger')) fail.push('删除按钮应有 danger 类: ' + del.cls)

  // PageHeader 返回箭头：应走 Icon 库（vertical-align:middle + 统一 stroke 2）
  const back = await page.evaluate(() => {
    const btn = document.querySelector('.page-header-back')
    if (!btn) return null
    const svg = btn.querySelector('svg')
    if (!svg) return { hasSvg: false }
    const st = getComputedStyle(svg)
    return { hasSvg: true, width: svg.getAttribute('width'), verticalAlign: st.verticalAlign, strokeWidth: svg.getAttribute('stroke-width') }
  })
  console.log('BACK', JSON.stringify(back))
  if (!back || !back.hasSvg) fail.push('PageHeader 应有返回按钮')
  else {
    if (back.width !== '20') fail.push('返回箭头尺寸应 20px: ' + back.width)
    if (back.verticalAlign !== 'middle') fail.push('返回箭头应走 Icon 库(vertical-align:middle)')
    if (back.strokeWidth !== '2') fail.push('返回箭头 strokeWidth 应统一为 2: ' + back.strokeWidth)
  }

  // @选择器里的 💡 tip 应换成 Lightbulb 库图标
  await page.locator('.market-detail-at-btn').click()
  await page.waitForSelector('.market-detail-at-tip', { timeout: 5000 })
  const tip = await page.evaluate(() => {
    const el = document.querySelector('.market-detail-at-tip')
    return {
      text: el.textContent,
      hasSvg: !!el.querySelector('svg'),
      hasLightbulbEmoji: el.textContent.includes('💡'),
    }
  })
  console.log('TIP', JSON.stringify(tip))
  if (tip.hasLightbulbEmoji) fail.push('@tip 仍含 💡 emoji')
  if (!tip.hasSvg) fail.push('@tip 应有 Lightbulb 库图标')

  if (errors.length) fail.push('pageErrors: ' + JSON.stringify(errors))

  const { shot } = require('./helpers.cjs')
  await shot(page, 'market-detail-owner-header.png', 'screenshots')
  await browser.close()

  console.log(fail.length ? '✗ FAIL\n - ' + fail.join('\n - ') : '✓ PASS')
  process.exit(fail.length ? 1 : 0)
})().catch((e) => { console.error(e); process.exit(1) })
