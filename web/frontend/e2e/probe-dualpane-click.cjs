const { openApp, login, seedChat } = require('./helpers.cjs')
const TEXT_ID = 'cd124e88e923', CARD_ID = 'd50aa3eae638'
;(async () => {
  const { browser, page } = await openApp({ width: 1280, height: 900 })
  await login(page, { settleMs: 1500 })
  const seed = await seedChat(page, { textId: TEXT_ID, cardId: CARD_ID })
  console.log('seeded', seed)
  await page.waitForSelector('.chat-desktop')
  // list items with ids + active
  const list = await page.evaluate(() => {
    const ids = [...document.querySelectorAll('.tl-panel .tl-item')].map((el,i) => ({ i, id: el.getAttribute('data-id'), active: el.classList.contains('is-active') }))
    return ids
  })
  console.log('items', list)
  // click index 1
  await page.locator('.tl-panel .tl-item').nth(1).click()
  await page.waitForTimeout(2500)
  const after = await page.evaluate(() => {
    const s = window.__appStore.getState()
    return { sessionId: s.sessionId, resumeLoading: s.resumeLoading, error: s.error, msgCount: (s.messages||[]).length, activeIdx: [...document.querySelectorAll('.tl-item')].findIndex(el=>el.classList.contains('is-active')) }
  })
  console.log('after click', after)
  await browser.close()
})().catch(e=>{console.error(e);process.exit(1)})
