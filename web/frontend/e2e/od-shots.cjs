// 给「剧光」概念稿截图：手机视口 390x844，全页滚动截图
const { chromium } = require('@playwright/test')
const pages = ['index', 'marketplace', 'character-card', 'chat', 'group-chat', 'dm', 'distill', 'profile', 'settings', 'login', 'design-system']

;(async () => {
  const browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 })
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 120)))
  for (const name of pages) {
    await page.goto(`http://127.0.0.1:8090/${name}.html`, { waitUntil: 'networkidle' }).catch((e) => { errors.push(`${name}:goto ${e.message.slice(0,80)}`); return })
    await page.waitForTimeout(400)
    await page.screenshot({ path: `e2e/od-log/shot-${name}.png`, fullPage: true })
    console.log(`shot ${name}`)
  }
  console.log('ERRORS', JSON.stringify(errors))
  await browser.close()
})().catch((e) => { console.error(e); process.exit(1) })
