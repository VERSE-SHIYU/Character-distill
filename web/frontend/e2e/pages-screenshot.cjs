// 批量页面截图（testadmin）：市场 / 我的 / 群聊 / 创作 / 设置
// 输出 e2e/screenshots/pages-{view}.png（桌面 1280 + 移动 390）
const { openApp, login, goToView, shot } = require('./helpers.cjs')

const OUT = 'pages'
const VIEWS = ['market', 'mine', 'groupChat', 'text', 'character', 'settings']

;(async () => {
  const { browser, page, errors: pageErrors } = await openApp({ width: 1280, height: 900 })
  await login(page, { settleMs: 1200 })

  const results = {}
  for (const view of VIEWS) {
    await goToView(page, view, { viaHome: true, delay: 60 })
    await page.waitForTimeout(1400)
    await shot(page, `pages-${view}.png`, OUT)
    const body = await page.evaluate(() => ({
      h: document.querySelector('.main-panel, .panel, .creation-panel, .group-chat-layout')?.className || '',
      title: document.querySelector('.panel-title, .page-title, .group-header-title, .mine-title')?.textContent?.trim() || '',
    }))
    results[view] = body
  }

  // 移动端抽查：我的 + 创作
  await page.setViewportSize({ width: 390, height: 844 })
  await page.evaluate(() => { window.__appStore.getState().setView('home') })
  await page.waitForTimeout(600)
  await page.evaluate(() => { window.__appStore.getState().pushView('mine') })
  await page.waitForTimeout(1000)
  await shot(page, 'pages-mine-mobile.png', OUT)

  console.log(JSON.stringify({ results, pageErrors }, null, 2))
  await browser.close()
})().catch(e => { console.error('FATAL', e); process.exit(1) })
