// 宋体标题开关探针：切宋体 → 回切黑体 → 刷新持久化。同一会话内完成，避免刷新后登录竞态
// 用法: node e2e/font-toggle-verify.cjs
const { openApp, login } = require('./helpers.cjs')

async function openThemePopup(page) {
  // 折叠态先固定展开（isVisible = open || pinned），动作按钮才会渲染
  await page.evaluate(() => {
    const pin = document.querySelector('.sidebar-collapse-btn[aria-label="固定侧边栏"]')
    if (pin) pin.click()
  })
  await page.waitForTimeout(350)
  const clicked = await page.evaluate(() => {
    const byTitle = document.querySelector('button[title="切换主题"]')
    const byText = [...document.querySelectorAll('.sidebar-action-btn')].find((b) => b.textContent.includes('换肤'))
    const btn = byTitle || byText
    if (btn) btn.click()
    return { clicked: !!btn }
  })
  if (!clicked.clicked) throw new Error('换肤按钮未找到')
  await page.waitForSelector('.theme-popup', { timeout: 5000 })
}

async function clickFontBtn(page, label) {
  await page.evaluate((l) => {
    const btns = [...document.querySelectorAll('.theme-popup-fontbtn')]
    const b = btns.find((x) => x.textContent.includes(l))
    if (b) b.click()
  }, label)
  await page.waitForTimeout(250)
}

const state = (page) => page.evaluate(() => {
  const t = document.querySelector('.home-hero-title')
  const ff = t ? getComputedStyle(t).fontFamily : null
  return {
    serifClass: document.documentElement.classList.contains('serif-display'),
    heroFont: ff,
    stored: localStorage.getItem('charsim-font'),
    active: (document.querySelector('.theme-popup-fontbtn.active')?.textContent || '').trim(),
  }
})

;(async () => {
  const { browser, page, errors } = await openApp({ width: 1280, height: 900 })
  await page.route('**/api/history/list*', (r) => r.fulfill({ json: { items: [], total: 0, page: 1, page_size: 4 } }))
  await page.route('**/api/market/featured*', (r) => r.fulfill({ json: [] }))
  await login(page)
  await page.waitForSelector('.home-hero', { timeout: 15000 })

  const base = await state(page)
  console.log('BASE', JSON.stringify(base, null, 2))

  await openThemePopup(page)
  await clickFontBtn(page, '宋体')
  const serif = await state(page)
  console.log('SERIF', JSON.stringify(serif, null, 2))

  await clickFontBtn(page, '黑体')
  const sans = await state(page)
  console.log('SANS', JSON.stringify(sans, null, 2))

  await clickFontBtn(page, '宋体')
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.waitForFunction(() => document.documentElement.classList.contains('serif-display'), { timeout: 10000 })
  const persisted = await state(page)
  console.log('PERSISTED', JSON.stringify(persisted, null, 2))

  const fail = []
  if (base.serifClass) fail.push('初始不应有 serif-display')
  if (base.heroFont && /Songti|SimSun|STSong/i.test(base.heroFont)) fail.push('初始 hero 不应是宋体: ' + base.heroFont)
  if (!serif.serifClass) fail.push('切宋体后 serif-display 未加')
  if (serif.stored !== 'serif') fail.push('localStorage 应为 serif: ' + serif.stored)
  if (serif.heroFont && !/Songti|SimSun|STSong/i.test(serif.heroFont)) fail.push('切宋体后 hero 字体不对: ' + serif.heroFont)
  if (sans.serifClass) fail.push('回切黑体后 serif-display 仍在')
  if (sans.stored !== 'sans') fail.push('回切后 localStorage 应为 sans: ' + sans.stored)
  if (sans.heroFont && /Songti|SimSun|STSong/i.test(sans.heroFont)) fail.push('回切后 hero 仍宋体: ' + sans.heroFont)
  if (!persisted.serifClass) fail.push('刷新后 serif-display 丢失')
  if (errors.length) fail.push('pageErrors: ' + JSON.stringify(errors))
  console.log(fail.length ? '✗ FAIL\n - ' + fail.join('\n - ') : '✓ PASS')
  await browser.close()
})().catch((e) => { console.error(e); process.exit(1) })
