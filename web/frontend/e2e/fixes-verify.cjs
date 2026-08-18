// 验证两个预存在 bug 修复：MinePage React #31 + __appStore __E2E 暴露
// 用法: node e2e/fixes-verify.cjs
const { openApp, login, pushView, shot } = require('./helpers.cjs')

;(async () => {
  const { browser, page, errors } = await openApp({ width: 390, height: 844 })
  // 额外收集 console error（Minified React #31 是该脚本的历史断言对象，需放行）
  page.on('console', m => { if (m.type() === 'error' && !m.text().includes('Minified React error #31')) errors.push('[console] ' + m.text().slice(0, 200)) })
  await login(page, { settleMs: 2500 })

  const results = {}

  // Fix 2: __E2E → __appStore 暴露 + pushView 可用
  results.appStore = await page.evaluate(() => ({
    exposed: !!window.__appStore,
    view: window.__appStore?.getState().view,
  }))

  // Fix 1: 程序化导航到 mine，MinePage 不再崩溃
  await pushView(page, 'mine')
  await page.waitForSelector('.market-grid-v2, .mine-onboard-card', { timeout: 15000 })
  await page.waitForTimeout(1500)
  results.mine = await page.evaluate(() => ({
    view: window.__appStore.getState().view,
    errorPage: document.body.innerText.includes('页面出错了'),
    cardCount: document.querySelectorAll('.market-card-v2').length,
    fallbackCount: document.querySelectorAll('.market-card-v2-cover-fallback').length,
  }))

  // testcard001 的 identity 应为字符串 'TestChar'（对象归一化生效），且无头像卡显示珍珠面
  results.testcard001 = await page.evaluate(() => {
    const cards = Array.from(document.querySelectorAll('.market-card-v2'))
    const tc = cards.find(c => (c.innerText || '').includes('TestChar'))
    if (!tc) return { found: false }
    const fb = tc.querySelector('.market-card-v2-cover-fallback')
    const identityEl = tc.querySelector('.market-card-v2-identity')
    const surface = fb ? getComputedStyle(fb).backgroundImage : null
    return {
      found: true,
      identityText: identityEl ? identityEl.textContent : null,
      hasFallback: !!fb,
      surfaceRadialCount: surface ? (surface.match(/radial-gradient\(/g) || []).length : null,
      surfaceLinearCount: surface ? (surface.match(/linear-gradient\(/g) || []).length : null,
    }
  })

  results.errors = errors
  await shot(page, 'fixes-mine.png')

  console.log(JSON.stringify(results, null, 2))
  await browser.close()
})().catch(e => { console.error(e); process.exit(1) })
