// 桌面双栏验证（testadmin）：
//   - .chat-desktop grid 双栏 / 会话列表 > 0 / conv-panel 含 chat-area
//   - 点击会话项 → resumeSession（mock 响应）→ sessionId 切换 + is-active 移动
//   - 移动端单栏（无 .chat-desktop）
// resume 端点 mock：testadmin 无 LLM API Key，原版 resume 也会 503，这里拦截以验证前端切换逻辑
const { openApp, login, seedChat, shot } = require('./helpers.cjs')

const TEXT_ID = 'cd124e88e923'
const CARD_ID = 'd50aa3eae638'
const OUT = 'dualpane'

;(async () => {
  const { browser, page, errors: pageErrors } = await openApp({ width: 1280, height: 900 })

  // mock resume：返回与后端一致的结构
  await page.route('**/api/history/*/resume', (route) => {
    const sid = route.request().url().match(/history\/([^/]+)\/resume/)?.[1] || 'mock'
    const now = new Date().toISOString()
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        session: {
          id: sid,
          card_id: CARD_ID,
          character_name: '吴庚霖',
          text_id: TEXT_ID,
          user_role: '我自己',
          avatar_data: null,
        },
        messages: [
          { role: 'user', content: `（mock 会话 ${sid.slice(0, 6)}）还记得我吗？`, id: `m-u-${sid}`, created_at: now },
          { role: 'char', content: '记得。你说过的每句话我都收着了。', id: `m-c-${sid}`, created_at: now },
        ],
        reunion_greeting_id: null,
      }),
    })
  })

  await login(page, { settleMs: 1500 })

  // seed：进入 chat view
  const seed = await seedChat(page, { textId: TEXT_ID, cardId: CARD_ID })
  if (!seed.ok) { console.error('seed failed', seed); process.exit(1) }

  await page.waitForSelector('.chat-desktop', { timeout: 10000 })

  const desktop = await page.evaluate(() => {
    const g = window.getComputedStyle(document.querySelector('.chat-desktop'))
    return { display: g.display, columns: g.gridTemplateColumns, height: g.height }
  })
  const tlCount = await page.locator('.tl-panel .tl-item').count()
  const convExists = await page.locator('.conv-panel .chat-area').count()
  const tlHeight = await page.locator('.tl-panel').evaluate(el => Math.round(el.getBoundingClientRect().height))
  const convHeight = await page.locator('.conv-panel').evaluate(el => Math.round(el.getBoundingClientRect().height))

  await page.waitForTimeout(400)
  await shot(page, 'desktop.png', OUT)

  // 点击非活跃会话 → resumeSession(mock) → sessionId 切换 + is-active 移动
  let clickResult = { clicked: false, reason: 'not-enough-items' }
  const itemCount = await page.locator('.tl-panel .tl-item').count()
  if (itemCount > 1) {
    const activeIdx = await page.evaluate(() =>
      [...document.querySelectorAll('.tl-panel .tl-item')].findIndex(el => el.classList.contains('is-active')))
    const target = activeIdx === 0 ? 1 : 0
    const before = await page.evaluate(() => window.__appStore.getState().sessionId)
    await page.locator('.tl-panel .tl-item').nth(target).click()
    await page.waitForTimeout(1800)
    const after = await page.evaluate(() => {
      const s = window.__appStore.getState()
      return {
        sessionId: s.sessionId,
        msgCount: (s.messages || []).length,
        error: s.error || null,
        activeIdx: [...document.querySelectorAll('.tl-panel .tl-item')].findIndex(el => el.classList.contains('is-active')),
      }
    })
    clickResult = { clicked: true, before, ...after, changed: before !== after.sessionId }
    await shot(page, 'desktop-switched.png', OUT)
  }

  // 移动端：单栏
  await page.setViewportSize({ width: 390, height: 844 })
  await page.waitForTimeout(600)
  const mobileChatDesktop = await page.locator('.chat-desktop').count()
  const mobileChatArea = await page.locator('.chat-area').count()

  console.log(JSON.stringify({ desktop, tlCount, convExists, tlHeight, convHeight, clickResult, mobileChatDesktop, mobileChatArea, pageErrors }, null, 2))
  await browser.close()
})().catch(e => { console.error('FATAL', e); process.exit(1) })
