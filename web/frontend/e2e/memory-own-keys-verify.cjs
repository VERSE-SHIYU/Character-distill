// 长期记忆「只用用户自己的 key」的界面验收 — 仅 testadmin（docs/specs/user-own-keys.md）
//
// 判据不写死「testadmin 配没配 key」：先截下面板打开时 /api/memory/list 的真实回包，
// 再断言界面与它一致 ——
//   configured: false → 有 .memory-unconfigured 引导、没有「+ 添加记忆」、没有「暂无记忆，…自动记录」
//   configured: true  → 没有引导、有「+ 添加记忆」
// 两种状态都要跑一遍（在设置页清 / 配 key 之间各跑一次），截图留给人看美观。
//
// 用法: TEST_PASSWORD=... node e2e/memory-own-keys-verify.cjs
//       可选 TEXT_ID / CARD_ID 覆盖默认的 testadmin 语料与卡（默认值与 chat-optimize-verify 同一套）
const { openApp, login, seedChat, shot } = require('./helpers.cjs')

const TEXT_ID = process.env.TEXT_ID || 'cd124e88e923'
const CARD_ID = process.env.CARD_ID || 'd50aa3eae638'

;(async () => {
  const { browser, page, errors } = await openApp({ width: 1280, height: 900 })
  await login(page, { settleMs: 2000 })

  const R = {}
  R.seed = await seedChat(page, { textId: TEXT_ID, cardId: CARD_ID, textWait: 1000, startWait: 2500, archiveWait: 2000 })
  if (!R.seed.ok) {
    console.log(JSON.stringify(R, null, 2))
    await browser.close()
    process.exit(1)
  }
  await page.waitForTimeout(1000)

  // 打开记忆面板，同时截下列表回包
  const listResp = page.waitForResponse((r) => r.url().includes(`/api/memory/list/${CARD_ID}`))
  await page.click('[data-more-trigger]')
  await page.locator('.chat-more-item', { hasText: '角色记忆' }).click()
  const body = await (await listResp).json()
  await page.waitForSelector('.memory-panel')
  await page.waitForTimeout(500)

  R.api = { enabled: body.enabled, configured: body.configured, count: (body.memories || []).length }
  R.ui = {
    hint: await page.locator('.memory-unconfigured').count() > 0,
    addTrigger: await page.locator('.memory-add-trigger').count() > 0,
    autoRecordEmpty: (await page.locator('.memory-panel').textContent() || '').includes('暂无记忆'),
  }
  R.pageErrors = errors

  const fails = []
  if (typeof body.configured !== 'boolean') fails.push('列表回包里没有 configured 字段（后端没部署到这一版？）')
  if (body.configured === false) {
    if (!R.ui.hint) fails.push('没配齐 key，却没有引导文案')
    if (R.ui.addTrigger) fails.push('没配齐 key，却显示了「+ 添加记忆」（点了只会 409）')
    if (R.ui.autoRecordEmpty) fails.push('没配齐 key，却显示「暂无记忆，聊天中的重要信息会自动记录」')
  } else if (body.configured === true) {
    if (R.ui.hint) fails.push('已配齐 key，却显示了引导文案')
    if (!R.ui.addTrigger) fails.push('已配齐 key，却没有「+ 添加记忆」')
  }
  if (errors.length) fails.push(`页面报错 ${errors.length} 条`)

  R.shot = `e2e/screenshots/memory-own-keys-${body.configured ? "configured" : "unconfigured"}.png`
  await shot(page, R.shot.split("/").pop())
  R.fails = fails
  console.log(JSON.stringify(R, null, 2))
  await browser.close()
  process.exit(fails.length ? 1 : 0)
})()
