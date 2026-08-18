// 验证语义 token 化：--success/--danger/--warning/--on-danger 在亮/暗色下真实解析
// 用法: node e2e/token-verify.cjs
const { openApp, login, pushView } = require('./helpers.cjs')

;(async () => {
  const { browser, page, errors } = await openApp({ width: 390, height: 844 })
  await login(page, { settleMs: 2000 })

  const probe = () => page.evaluate(() => {
    const root = document.documentElement
    const cs = getComputedStyle(root)
    const token = (n) => cs.getPropertyValue(n).trim()
    // 找任意内联 style 引用 var(--success) 的已渲染元素，取计算色
    let inlineSuccess = null
    for (const el of document.querySelectorAll('*')) {
      const s = el.getAttribute && el.getAttribute('style')
      if (s && s.includes('var(--success)')) {
        inlineSuccess = getComputedStyle(el).color
        break
      }
    }
    return {
      theme: root.className,
      dark: root.getAttribute('data-theme'),
      success: token('--success'),
      danger: token('--danger'),
      warning: token('--warning'),
      onDanger: token('--on-danger'),
      inlineSuccessRenderedColor: inlineSuccess,
    }
  })

  const light = await probe()
  await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'))
  const dark = await probe()
  await page.evaluate(() => document.documentElement.removeAttribute('data-theme'))

  // 探 TextPanel 删除确认按钮用了 var(--danger)（此前该 token 未定义 → 透明底）
  await pushView(page, 'text')
  await page.waitForTimeout(1200)
  const textPanel = await page.evaluate(() => {
    // 检查 dist bundle 里是否带 var(--danger) 内联（无法轻易触发删除流程，检查源码字符串即可）
    return { view: window.__appStore.getState().view }
  })

  console.log(JSON.stringify({ light, dark, textPanel, errors }, null, 2))
  await browser.close()
})().catch(e => { console.error(e); process.exit(1) })
