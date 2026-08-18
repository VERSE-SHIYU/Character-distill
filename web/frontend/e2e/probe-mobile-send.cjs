const { openApp, login, seedChat } = require('./helpers.cjs')
const TEXT_ID = 'cd124e88e923', CARD_ID = 'd50aa3eae638'
;(async () => {
  const { browser, page } = await openApp({ width: 390, height: 844 })
  await login(page, { settleMs: 1500 })
  await seedChat(page, { textId: TEXT_ID, cardId: CARD_ID, inject: [{ role: 'char', content: '你好呀', _cid: 'a1', id: 'a1', timestamp: new Date().toISOString() }] })
  await page.waitForSelector('.composer-bar')
  const before = await page.evaluate(() => {
    const bar = document.querySelector('.composer-bar')
    const btn = document.querySelector('.chat-send-btn')
    const barRect = bar.getBoundingClientRect()
    const btnStyle = btn ? window.getComputedStyle(btn) : null
    return {
      viewport: [innerWidth, innerHeight],
      barRect: { y: Math.round(barRect.y), h: Math.round(barRect.height), bottom: Math.round(barRect.bottom) },
      barDisplay: window.getComputedStyle(bar).display,
      isMobileLayout: window.__appStore.getState().currentView,
      hasDesktop: !!document.querySelector('.chat-desktop'),
      sendDisplay: btnStyle ? btnStyle.display : 'NO_BTN',
      sendRect: btn ? (r => ({w: Math.round(r.width), h: Math.round(r.height)}))(btn.getBoundingClientRect()) : null,
    }
  })
  // 输入文字后
  await page.locator('.chat-textarea').fill('你好')
  await page.waitForTimeout(300)
  const after = await page.evaluate(() => {
    const btn = document.querySelector('.chat-send-btn')
    const bar = document.querySelector('.composer-bar')
    const rect = btn.getBoundingClientRect()
    const barRect = bar.getBoundingClientRect()
    return {
      isEmptied: bar.classList.contains('is-empty'),
      sendDisplay: window.getComputedStyle(btn).display,
      sendRect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height), bottom: Math.round(rect.bottom) },
      barBottom: Math.round(barRect.bottom), viewportH: innerHeight,
      sendVisibleInViewport: rect.bottom <= innerHeight && rect.top >= 0 && rect.width > 0,
    }
  })
  console.log('BEFORE', JSON.stringify(before, null, 2))
  console.log('AFTER_TYPING', JSON.stringify(after, null, 2))
  await browser.close()
})().catch(e=>{console.error('FATAL', e); process.exit(1)})
