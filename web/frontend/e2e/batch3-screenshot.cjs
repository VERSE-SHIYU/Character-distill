// Batch3 页面截图（testadmin）：首页 / 历史 / 回收站 / 语音 / 阅读
// 输出 e2e/screenshots/b3-{view}.png
const { openApp, login, goToView, shot } = require('./helpers.cjs')

const OUT = 'b3'
const VIEWS = ['home', 'history', 'trash', 'voice']

;(async () => {
  const { browser, page, errors: pageErrors } = await openApp({ width: 1280, height: 900 })
  await login(page, { settleMs: 1400 })

  const results = {}
  for (const view of VIEWS) {
    await goToView(page, view, { viaHome: true })
    await page.waitForTimeout(1500)
    await shot(page, `b3-${view}.png`, OUT)
    const title = await page.evaluate(() =>
      document.querySelector('.panel-title, .page-title, .group-header-title, .mine-title, .login-title')?.textContent?.trim() || '')
    results[view] = { title }
  }

  // 移动端首页
  await page.setViewportSize({ width: 390, height: 844 })
  await page.evaluate(() => { window.__appStore.getState().setView('home') })
  await page.waitForTimeout(900)
  await shot(page, 'b3-home-mobile.png', OUT)

  console.log(JSON.stringify({ results, pageErrors }, null, 2))
  await browser.close()
})().catch(e => { console.error('FATAL', e); process.exit(1) })
