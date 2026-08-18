// 验证 ② emoji→Icon / ③ cursor:pointer / ④ 11px→12px 三项修复在真实浏览器生效
// 用法: node e2e/icons-verify.cjs
const { openApp, login, pushView } = require('./helpers.cjs')

;(async () => {
  const { browser, page, errors } = await openApp({ width: 390, height: 844 })
  await login(page, { settleMs: 2000 })

  // ③ antd TabBar 项手型（此前 antd 无指针，新增 [role='tab'] 全局规则）
  const tabCursor = await page.evaluate(() => {
    const tab = document.querySelector('[role="tab"]')
    return tab ? getComputedStyle(tab).cursor : 'no-tab-found'
  })

  // ② 扫描当前 DOM 是否残留文本符号（功能性 UI 应清零；emoji 选择器未打开不含这些字符）
  const scanSymbols = () => page.evaluate(() => {
    const hits = []
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
    let n
    while ((n = walker.nextNode())) {
      const t = n.nodeValue
      if (t && /[✕✎♡📖🤝🛡✓]/.test(t)) hits.push(t.trim().slice(0, 40))
    }
    return hits
  })
  const homeSymbols = await scanSymbols()

  // 串行切换各主视图，捕获渲染期错误 + 残留符号（覆盖 ② 涉及全部组件）
  const views = ['text', 'groupChat', 'mine', 'market', 'character', 'marketCardDetail', 'messages', 'voice', 'author', 'profile', 'settings', 'history', 'trash', 'legal', 'distillWorkbench']
  const perView = {}
  for (const v of views) {
    await pushView(page, v)
    await page.waitForTimeout(800)
    perView[v] = await scanSymbols()
  }
  await pushView(page, 'home')
  await page.waitForTimeout(900)

  // ④ 全 DOM 内联 fontSize 11 应为 0
  const fontSize11 = await page.evaluate(() => {
    let n = 0
    for (const el of document.querySelectorAll('[style]')) {
      const s = el.getAttribute('style') || ''
      if (/\bfont-size:\s*11px/.test(s)) n++
    }
    return n
  })

  // 图标系统：确认新增图标真的打进 bundle（远端 .js 源码级，无需触发 UI）
  const svgInBundle = await page.evaluate(() => {
    return window.__E2E // placeholder
  })

  console.log(JSON.stringify({ tabCursor, homeSymbols, perView, fontSize11, errors }, null, 2))
  await browser.close()
})().catch(e => { console.error(e); process.exit(1) })
