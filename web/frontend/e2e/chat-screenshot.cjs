// 用 testadmin 进入沉浸式对话页并截图（桌面 + 移动端）
const { openApp, login, seedChat, shot } = require('./helpers.cjs')

const TEXT_ID = 'cd124e88e923'
const CARD_ID = 'd50aa3eae638'
const OUT = 'chat-optimize'

const now = Date.now()
const iso = (ms) => new Date(ms).toISOString()
const inject = [
  { role: 'user', content: '你好，还记得我吗？', _cid: 'u-seed-1', id: 'u-seed-1', timestamp: iso(now - 6 * 60 * 1000) },
  { role: 'char', content: '嗯，我在听。你慢慢说。深夜能有人一起醒着，是件难得的事。', _cid: 'a-seed-1', id: 'a-seed-1', timestamp: iso(now - 5 * 60 * 1000) },
  { role: 'user', content: '最近过得怎么样？', _cid: 'u-seed-2', id: 'u-seed-2', timestamp: iso(now - 60 * 1000) },
  { role: 'char', content: '还行。剧场又排了新戏，你哪天有空来后台坐坐，给你留了把椅子。', _cid: 'a-seed-2', id: 'a-seed-2', timestamp: iso(now - 30 * 1000) },
]
const affinity = { mood_emoji: '😊', mood: '温暖', inner_voice: '我在想一些事……', stage_emoji: '🌱', stage: '初识', affinity: 50, trust: 30, guard: 70 }

;(async () => {
  const { browser, page } = await openApp({ width: 1280, height: 900 })
  await login(page, { settleMs: 2000 })

  const seed = await seedChat(page, { textId: TEXT_ID, cardId: CARD_ID, inject, affinity, textWait: 1000, startWait: 2000, archiveWait: 2000, injectWait: 700 })
  if (!seed.ok) { console.error('seed failed', seed); process.exit(1) }
  await page.waitForTimeout(1200)

  await shot(page, 'desktop.png', OUT)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.waitForTimeout(500)
  await shot(page, 'mobile.png', OUT)
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.waitForTimeout(300)

  // 心语弹窗状态截图
  const moodBtn = page.locator('.chat-topbar-mood-btn')
  if (await moodBtn.count() > 0) {
    await moodBtn.first().click()
    await page.waitForSelector('.inner-voice-popup', { timeout: 3000 })
    await page.waitForTimeout(400)
    await shot(page, 'popup.png', OUT)
    await page.keyboard.press('Escape')
  }

  console.log('OK', seed, '\n  e2e/' + OUT + '/desktop.png\n  e2e/' + OUT + '/mobile.png\n  e2e/' + OUT + '/popup.png')
  await browser.close()
})().catch(e => { console.error('FATAL', e); process.exit(1) })
