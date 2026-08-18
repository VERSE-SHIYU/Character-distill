// 角色一对一聊天重设计验证（testadmin）：
//   - character-first 头部：.chat-char-header（名字/身份副标题/在线/⋯）
//   - 常驻心情条 .chat-mood-strip：stage pill + mood + inner_voice（数据来自 /api/chat/affinity）
//   - 状态卡 .char-state-card：内心独白 + 6 段阶段阶梯 + 好感/信任/防御 三条 0-100 进度条
//   - 开场/跨时段角色名 .cbubble-name（i===0 或 showTime）
//   - 移动端单栏 + 无横向溢出
// affinity 端点 mock（testadmin 无 LLM key，真实后端返回默认值；mock 让断言确定）
const { openApp, login, seedChat, shot } = require('./helpers.cjs')

const TEXT_ID = 'cd124e88e923'
const CARD_ID = 'd50aa3eae638'
const OUT = 'chat-redesign'

const AFFINITY_MOCK = {
  affinity: 62, trust: 45, guard: 38,
  mood: '心软', mood_emoji: '🥺', inner_voice: '他居然记得我讨厌香菜。',
  stage: '朋友', stage_emoji: '😄', reason: '',
}

// 注入 3 条消息：m1(i=0→名) / m2(user, 20min gap) / m3(char, 20min gap→名) → .cbubble-name 应出现 2 次
// 用 timestamp（store 归一化字段），组件 showTime 判断读 msg.timestamp
const INJECT = [
  { role: 'char', content: '我回来了。', id: 'm1', timestamp: '2026-08-16T10:00:00' },
  { role: 'user', content: '你去哪儿了？', id: 'm2', timestamp: '2026-08-16T10:20:00' },
  { role: 'char', content: '去山上采药，顺便带了你要的花。', id: 'm3', timestamp: '2026-08-16T10:40:00' },
]

;(async () => {
  const { browser, page, errors: pageErrors } = await openApp({ width: 1280, height: 900 })

  await page.route('**/api/chat/affinity/*', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(AFFINITY_MOCK) }))

  await login(page, { settleMs: 1200 })

  const seed = await seedChat(page, { textId: TEXT_ID, cardId: CARD_ID, inject: INJECT })
  if (!seed.ok) { console.error('seed failed', seed); process.exit(1) }

  await page.waitForSelector('.chat-area', { timeout: 10000 })
  await page.waitForFunction(() => {
    const a = window.__appStore.getState().affinity
    return a && Number(a.affinity) === 62
  }, { timeout: 8000 }).catch(() => {})
  await page.waitForTimeout(300)

  const name = await page.evaluate(() => {
    const el = document.querySelector('.chat-char-header-name')
    return el ? el.textContent.trim() : ''
  })

  const header = await page.evaluate(() => {
    const h = document.querySelector('.chat-char-header')
    const chip = document.querySelector('.chat-header-mood')
    return {
      header: !!h,
      chip: !!chip,
      stage: document.querySelector('.chat-mood-stage')?.textContent.trim() || '',
      word: document.querySelector('.chat-mood-word')?.textContent.trim() || '',
      identity: (document.querySelector('.chat-char-header-identity')?.textContent || '').trim(),
      online: !!document.querySelector('.ai-online'),
      history: !!document.querySelector('.chat-history-toggle'),
      more: !!document.querySelector('.chat-topbar-more-btn'),
      name: document.querySelector('.chat-char-header-name')?.textContent.trim() || '',
    }
  })

  // 场景卡（i=0 开场 + m3 跨时段 → 应出现 2 张；name=角色名，sub=阶段·心情）
  const sceneCount = await page.locator('.chat-area .chat-scene').count()
  const sceneNames = await page.locator('.chat-area .chat-scene-name').allTextContents()
  const sceneSubs = await page.locator('.chat-area .chat-scene-sub').allTextContents()

  // 打开状态卡
  await page.locator('.chat-header-mood').click()
  await page.waitForSelector('.char-state-card', { timeout: 5000 })

  const card = await page.evaluate(() => {
    const segs = document.querySelectorAll('.char-state-seg')
    const bars = document.querySelectorAll('.char-state-bar-row')
    const firstW = bars[0]?.querySelector('.char-state-bar i')?.style?.width || ''
    const r = document.querySelector('.char-state-card')?.getBoundingClientRect()
    const h = document.querySelector('.chat-char-header')?.getBoundingClientRect()
    return {
      headName: document.querySelector('.char-state-head-name')?.textContent.trim() || '',
      caption: document.querySelector('.char-state-head-caption')?.textContent.trim() || '',
      voice: document.querySelector('.char-state-voice')?.textContent.trim() || '',
      voiceItalic: document.querySelector('.char-state-voice') ? getComputedStyle(document.querySelector('.char-state-voice')).fontStyle : null,
      voiceFont: document.querySelector('.char-state-voice') ? getComputedStyle(document.querySelector('.char-state-voice')).fontFamily : null,
      segCount: segs.length,
      segDone: document.querySelectorAll('.char-state-seg.done').length,
      stageName: document.querySelector('.char-state-stage-name')?.textContent.trim() || '',
      stageGoal: document.querySelector('.char-state-stage-goal')?.textContent.trim() || '',
      barCount: bars.length,
      firstBarWidth: firstW,
      popupBelowHeader: r && h ? r.top >= h.bottom - 1 : null,
    }
  })

  await shot(page, 'desktop-state-card.png', OUT)

  // 关闭状态卡，回到列表（避免遮挡后续）
  await page.mouse.click(20, 200)
  await page.waitForTimeout(300)

  const desktop = await page.evaluate(() => {
    const g = window.getComputedStyle(document.querySelector('.chat-desktop'))
    return { display: g.display, columns: g.gridTemplateColumns }
  })

  // 移动端：单栏 + 无横向溢出 + 心情条单行截断样式
  await page.setViewportSize({ width: 390, height: 844 })
  await page.waitForTimeout(600)

  const mobile = await page.evaluate(() => {
    const chip = document.querySelector('.chat-header-mood')
    const hrow = document.querySelector('.chat-char-header-row')
    return {
      chatDesktop: document.querySelectorAll('.chat-desktop').length,
      chatArea: document.querySelectorAll('.chat-area').length,
      header: !!document.querySelector('.chat-char-header'),
      chip: !!chip,
      headerNoOverflow: hrow ? hrow.scrollWidth <= hrow.clientWidth + 1 : null,
      hOverflow: document.documentElement.scrollWidth > window.innerWidth,
    }
  })

  await shot(page, 'mobile.png', OUT)

  console.log(JSON.stringify({ name, header, sceneCount, sceneNames, sceneSubs, card, desktop, mobile, pageErrors }, null, 2))
  await browser.close()
})().catch(e => { console.error('FATAL', e); process.exit(1) })
