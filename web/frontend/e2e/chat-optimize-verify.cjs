// 沉浸式对话页优化验证 — 仅 testadmin
// 断言：无 pageerrors；头像 12px；气泡 12px；消息间距；token 化弹窗/角色栏；字号/历史/更多菜单/返回/发送键/撤回/TTS hover
// 用法: node e2e/chat-optimize-verify.cjs
const { openApp, login, seedChat, cs } = require('./helpers.cjs')

const TEXT_ID = 'cd124e88e923' // 失联三十一天
const CARD_ID = 'd50aa3eae638' // 吴庚霖

const now = Date.now()
const iso = (ms) => new Date(ms).toISOString()
const inject = [
  { role: 'user', content: '你好，还记得我吗？', _cid: 'u-seed-1', id: 'u-seed-1', timestamp: iso(now - 6 * 60 * 1000) },
  { role: 'char', content: '嗯，我在听。你慢慢说。', _cid: 'a-seed-1', id: 'a-seed-1', timestamp: iso(now - 5 * 60 * 1000) },
  { role: 'user', content: '最近过得怎么样？', _cid: 'u-seed-2', id: 'u-seed-2', timestamp: iso(now - 60 * 1000) },
]
const affinity = { mood_emoji: '😊', mood: '温暖', inner_voice: '我在想一些事……', stage_emoji: '🌱', stage: '初识', affinity: 50, trust: 30, guard: 70 }

;(async () => {
  const { browser, page, errors } = await openApp({ width: 1280, height: 900 })
  await login(page, { settleMs: 2000 })

  // ── 用 store 走真实流程开一个会话（testadmin 自己的角色 吴庚霖）──
  const seed = await seedChat(page, { textId: TEXT_ID, cardId: CARD_ID, inject, affinity, textWait: 1000, startWait: 2000, archiveWait: 2000 })
  // 补回原脚本的诊断字段（值经参数序列化传进页面，别在函数体里引用 Node 变量）
  const seedExtras = await page.evaluate(({ CARD_ID }) => {
    const s = window.__appStore.getState()
    return { cardName: (s.cards || []).find(c => c.id === CARD_ID)?.name || null, error: s.error, archiveOpen: s.archiveModalOpen, msgLen: (s.messages || []).length }
  }, { CARD_ID })
  Object.assign(seed, seedExtras)

  const R = {}
  R.seed = seed
  if (!seed.ok) {
    console.log(JSON.stringify(R, null, 2))
    await browser.close()
    process.exit(1)
  }
  await page.waitForTimeout(1500)

  // ── 基础渲染 ──
  R.chatView = await page.locator('.chat-area').count() > 0
  R.topbar = await page.locator('.chat-char-header').count() > 0
  R.charName = (await page.locator('.chat-char-header-name').first().textContent() || '').trim()
  R.msgCount = await page.locator('.chat-msg').count()
  R.revokeCount = await page.locator('.chat-revoke-btn').count()
  R.ttsCount = await page.locator('.tts-play-btn').count()

  // ── 样式断言 ──
  R.avatarRadius = await cs(page, '.chat-topbar-avatar-btn > div', ['borderRadius'])
  R.bubbleLeftRadius = await cs(page, '.chat-msg-char .cbubble--left', ['borderRadius'])
  R.bubbleFont = await cs(page, '.chat-msg-char .cbubble', ['fontSize'])
  R.messagesGap = await cs(page, '.chat-messages', ['rowGap', 'columnGap', 'gap', 'padding'])
  R.msgPadding = await cs(page, '.chat-msg', ['padding'])
  R.roleBarBorder = await cs(page, '.user-role-bar', ['borderBottomColor'])

  // 面板可读性校验：inner-voice 弹窗背景 == 不透明 --bg-page（2026-08-16 修半透明玻璃透字）
  R.popupBgMatch = await page.evaluate(() => {
    const probe = document.createElement('div')
    probe.style.background = 'var(--bg-page)'
    document.body.appendChild(probe)
    const expected = getComputedStyle(probe).backgroundColor
    probe.remove()
    return { expected, actual: null }
  })

  // ── 交互：心语状态卡（触发 = 头部状态 chip）──
  const moodBtn = page.locator('.chat-header-mood')
  if (await moodBtn.count() > 0) {
    await moodBtn.first().click()
    await page.waitForSelector('.inner-voice-popup', { timeout: 3000 })
    const actual = await page.evaluate(() => getComputedStyle(document.querySelector('.inner-voice-popup')).backgroundColor)
    R.popupBgMatch.actual = actual
    R.popupBgMatch.match = actual === R.popupBgMatch.expected
    await page.keyboard.press('Escape')
    await page.waitForTimeout(200)
  } else {
    R.popupBgMatch.match = 'no-mood-btn'
  }

  // ── 交互：更多菜单（8 项）──
  await page.locator('.chat-topbar-more-btn').click()
  await page.waitForSelector('.chat-more-menu', { timeout: 3000 })
  R.moreMenuCount = await page.locator('.chat-more-menu .chat-more-item').count()
  R.moreMenuLabels = (await page.locator('.chat-more-menu .chat-more-item').allTextContents()).map(t => t.trim())

  // ── 交互：字号 + / -（菜单此时仍开；点击项会关菜单，需重开）──
  const openMore = async () => {
    await page.locator('.chat-topbar-more-btn').click()
    await page.waitForSelector('.chat-more-menu', { timeout: 3000 })
  }
  const fontPlus = page.locator('.chat-more-menu .chat-more-item', { hasText: '放大字号' })
  await fontPlus.click()
  await page.waitForTimeout(300)
  R.hasTextLg = await page.locator('.chat-area.has-text-lg').count() > 0
  await openMore()
  await page.locator('.chat-more-menu .chat-more-item', { hasText: '缩小字号' }).click()
  await page.waitForTimeout(300)
  await openMore()
  await page.locator('.chat-more-menu .chat-more-item', { hasText: '缩小字号' }).click()
  await page.waitForTimeout(300)
  R.hasTextSm = await page.locator('.chat-area.has-text-sm').count() > 0
  R.hasTextLgAfterShrink = await page.locator('.chat-area.has-text-lg').count() > 0
  await page.keyboard.press('Escape') // 关菜单
  await page.waitForTimeout(200)

  // ── 交互：历史面板开合 ──
  await page.locator('.chat-history-toggle').click()
  await page.waitForTimeout(500)
  R.historyPanelOpen = await page.locator('.history-sidebar-content').count() > 0
  await page.locator('.chat-history-toggle').click()
  await page.waitForTimeout(400)
  R.historyPanelClosed = await page.locator('.history-sidebar-content').count() === 0

  // ── 交互：发送键 enable/disable ──
  R.sendDisabledEmpty = !(await page.locator('.chat-send-btn').isEnabled())
  await page.fill('.chat-textarea', '这是一条测试输入')
  await page.waitForTimeout(150)
  R.sendEnabledTyping = await page.locator('.chat-send-btn').isEnabled()
  await page.fill('.chat-textarea', '')
  await page.waitForTimeout(150)
  R.sendDisabledCleared = !(await page.locator('.chat-send-btn').isEnabled())

  // ── 交互：hover 撤回 / TTS（revoke 只渲染在最后一条用户消息上）──
  R.revokeOpacityBaseline = await page.evaluate(() => getComputedStyle(document.querySelector('.chat-revoke-btn')).opacity)
  await page.locator('.chat-msg-user:has(.chat-revoke-btn)').hover()
  await page.waitForTimeout(300)
  R.revokeOpacityOnHover = await page.evaluate(() => getComputedStyle(document.querySelector('.chat-revoke-btn')).opacity)
  R.ttsOpacityBaseline = await page.evaluate(() => getComputedStyle(document.querySelector('.tts-play-btn')).opacity)
  await page.locator('.chat-msg-char:has(.tts-play-btn)').hover()
  await page.waitForTimeout(300)
  R.ttsOpacityOnHover = await page.evaluate(() => getComputedStyle(document.querySelector('.tts-play-btn')).opacity)

  // ── 移动端间距 ──
  await page.setViewportSize({ width: 390, height: 844 })
  await page.waitForTimeout(400)
  R.mobileMessagesPadding = await cs(page, '.chat-messages', ['padding'])

  // ── 返回键导航 ──
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.waitForTimeout(300)
  const viewBefore = await page.evaluate(() => window.__appStore.getState().currentView)
  await page.locator('.chat-topbar-back').click()
  await page.waitForTimeout(500)
  const viewAfter = await page.evaluate(() => window.__appStore.getState().currentView)
  R.backNav = { viewBefore, viewAfter, popped: viewBefore !== viewAfter }

  R.pageErrors = errors
  console.log(JSON.stringify(R, null, 2))
  await browser.close()
})().catch(e => { console.error(e); process.exit(1) })
