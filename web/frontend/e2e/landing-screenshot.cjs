// 入口页截图：登录页（未登录态）+ 首页（testadmin 登录后）
// 输出 e2e/screenshots/landing-login.png / landing-home.png
const { openApp, gotoLogin, login, shot } = require('./helpers.cjs')

const OUT = 'landing'

;(async () => {
  const { browser, page, errors: pageErrors } = await openApp({ width: 1280, height: 900 })

  // 未登录 → 登录页
  await gotoLogin(page)
  await page.waitForTimeout(500)
  await shot(page, 'landing-login.png', OUT)

  // testadmin 登录 → 首页
  await login(page, { settleMs: 1800, shellSelector: '.mobile-tabbar, [class*="shell"], .home-page' })
  await shot(page, 'landing-home.png', OUT)

  // 移动端首页
  await page.setViewportSize({ width: 390, height: 844 })
  await page.waitForTimeout(700)
  await shot(page, 'landing-home-mobile.png', OUT)

  console.log(JSON.stringify({ pageErrors }, null, 2))
  await browser.close()
})().catch(e => { console.error('FATAL', e); process.exit(1) })
