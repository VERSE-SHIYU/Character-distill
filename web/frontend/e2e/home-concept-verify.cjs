// HomePage 切片① 探针：hero 问候 + 统一区块头 + 统计条 + 无溢出 + 无报错
// 用法: node e2e/home-concept-verify.cjs
// mock: /api/history/list + /api/market/featured（保证最近对话/编辑推荐渲染）；其余走真实后端
const { openApp, login, cs } = require('./helpers.cjs')

const HISTORY_ITEM = {
  id: 's1', character_name: '沈若言', card_id: 'c1', text_id: 'txt-a', text_title: '长夜灯火',
  last_message: '你还记得我们第一次见面吗？', last_message_at: Date.now() - 120000, updated_at: Date.now() - 120000,
  user_role: 'assistant',
}
const FEATURED_CARD = {
  id: 'f1', card_id: 'c1', name: '沈若言', avatar_data: null,
  card_json: JSON.stringify({ name: '沈若言', identity: '长夜书局的守夜人' }),
}

async function mount(page, w) {
  await page.route('**/api/history/list*', (r) => r.fulfill({ json: { items: [HISTORY_ITEM], total: 1, page: 1, page_size: 4 } }))
  await page.route('**/api/market/featured*', (r) => r.fulfill({ json: [FEATURED_CARD] }))
  await login(page)
  await page.waitForSelector('.home-hero', { timeout: 15000 })
  await page.waitForSelector('.home-recent-item', { timeout: 15000 })
  await page.waitForTimeout(400)
}

async function probe(page) {
  return page.evaluate(() => {
    const rect = (sel) => {
      const el = document.querySelector(sel)
      if (!el) return null
      const b = el.getBoundingClientRect()
      return { top: Math.round(b.top), bottom: Math.round(b.bottom), left: Math.round(b.left), right: Math.round(b.right), h: Math.round(b.height), w: Math.round(b.width) }
    }
    const cs = (el) => el ? getComputedStyle(el) : null
    const heroTitle = document.querySelector('.home-hero-title')
    const secTitle = document.querySelector('.home-section-title')
    const kicker = document.querySelector('.home-hero-kicker')
    const heads = [...document.querySelectorAll('.home-section-head')]
    const heroPrimary = document.querySelector('.home-hero-actions .btn-primary')
    return {
      iw: window.innerWidth,
      hero: rect('.home-hero'),
      heroTitle: heroTitle && {
        text: heroTitle.textContent.trim(),
        fs: parseFloat(cs(heroTitle).fontSize),
        ls: cs(heroTitle).letterSpacing,
        visible: heroTitle.getBoundingClientRect().height > 0,
      },
      kicker: kicker && { text: kicker.textContent.trim(), fs: parseFloat(cs(kicker).fontSize) },
      stats: {
        items: document.querySelectorAll('.home-stats-bar .home-stats-item').length,
        nums: [...document.querySelectorAll('.home-stats-num')].map((n) => n.textContent.trim()),
        bar: rect('.home-stats-bar'),
      },
      sectionHeads: {
        count: heads.length,
        ticks: document.querySelectorAll('.home-section-head .home-section-tick').length,
        titles: heads.map((h) => h.querySelector('.home-section-title')?.textContent.trim()),
      },
      secTitleFs: secTitle && parseFloat(cs(secTitle).fontSize),
      recent: {
        items: document.querySelectorAll('.home-recent-item').length,
        name: document.querySelector('.home-recent-name')?.textContent.trim() || null,
        source: document.querySelector('.home-recent-source')?.textContent.trim() || null,
        time: document.querySelector('.home-recent-time')?.textContent.trim() || null,
      },
      featured: document.querySelectorAll('.home-featured-card').length,
      heroPrimary: heroPrimary && heroPrimary.textContent.trim(),
      overlap: (() => {
        const h = rect('.home-hero'); const s = rect('.home-stats-bar')
        if (!h || !s) return null
        return { heroBottom: h.bottom, statsTop: s.top, overlaps: h.bottom > s.top + 1 }
      })(),
    }
  })
}

;(async () => {
  for (const vp of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
    const { width: w, height: h } = vp
    const { browser, page, errors } = await openApp({ width: w, height: h })
    await mount(page, w)
    const d = await probe(page)
    console.log(`\n═══ ${w}×${h} ═══\n` + JSON.stringify(d, null, 2))
    const fail = []
    if (!d.heroTitle?.visible) fail.push('hero 标题不可见')
    if (d.heroTitle && d.heroTitle.fs < 26) fail.push(`hero 标题字号 ${d.heroTitle.fs}px < 26`)
    if (d.kicker?.text !== '与角色 · 隔幕对谈') fail.push('kicker 文案不对: ' + d.kicker?.text)
    if (d.stats.items !== 3) fail.push(`统计项 ${d.stats.items} != 3`)
    if (d.sectionHeads.count < 4) fail.push(`区块头 ${d.sectionHeads.count} < 4`)
    if (d.sectionHeads.ticks !== d.sectionHeads.count) fail.push(`tick ${d.sectionHeads.ticks} != 区块头 ${d.sectionHeads.count}`)
    if (d.secTitleFs && d.secTitleFs < 16) fail.push(`区块标题字号 ${d.secTitleFs} < 16`)
    if (w === 1280 && d.heroPrimary !== '继续上次对话') fail.push('hero 主动作应为 继续上次对话: ' + d.heroPrimary)
    if (d.heroPrimary && d.heroPrimary.length > 10) fail.push('hero 主动作文字过长: ' + d.heroPrimary)
    if (d.hero && d.hero.right > d.iw + 1) fail.push('hero 右溢出')
    if (d.hero && d.hero.left < -1) fail.push('hero 左溢出')
    if (d.overlap?.overlaps) fail.push(`hero 与统计条重叠 heroBottom=${d.overlap.heroBottom} statsTop=${d.overlap.statsTop}`)
    if (errors.length) fail.push('pageErrors: ' + JSON.stringify(errors))
    console.log(fail.length ? '✗ FAIL\n - ' + fail.join('\n - ') : '✓ PASS')
    await browser.close()
  }
})().catch((e) => { console.error(e); process.exit(1) })
