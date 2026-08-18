// 蒸馏工作台 DistillWorkbench 验证：桌面双栏 + 移动单栏 + 各状态详情
// 用法: node e2e/distill-workbench-verify.cjs
// 只操作 testadmin；蒸馏任务/卡片均用注入的 mock 数据（真实字段形状）
const { openApp, login, pushView, cs, shot } = require('./helpers.cjs')

const CARD_JSON = JSON.stringify({
  name: '阿遥',
  identity: '深夜电台主持人，声线温柔',
  tags: ['电台', '治愈', '深夜'],
  awakening_message: '夜里好，我是阿遥。',
})

// 注入 store 的任务（与后端 /api/distill/task/{id} 返回字段一致）
const TASKS = [
  { id: 't-run-1', textId: 'txt-a', character: '沈若言', status: 'identifying', progress_pct: 45, message: '正在识别角色', current: 12, total: 40 },
  { id: 't-err-1', textId: 'txt-a', character: '江叙', status: 'error', progress_pct: 20, message: '蒸馏失败：LLM 超时', card_id: null },
  { id: 't-done-1', textId: 'txt-a', character: '阿遥', status: 'done', progress_pct: 100, message: '蒸馏完成', card_id: 'card-acc' },
]
const CARDS = [
  { id: 'card-acc', text_id: 'txt-a', name: '阿遥', card_json: CARD_JSON, created_at: '2026-08-15 10:00' },
]

async function seed(page) {
  await page.evaluate(({ TASKS }) => {
    window.__appStore.setState({
      texts: [{ id: 'txt-a', title: '测试文本' }],
      currentTextId: 'txt-a',
      distillTasks: TASKS,
    })
  }, { TASKS })
}

// 关键：/api/text/list 也 mock——HomePage/TextPanel 挂载时会 loadTexts()，
// 不 mock 会被真实后端文本列表顶掉 seed 的 texts，workbench 卡片区就跟着 8 个真实文本各拉 1 张卡
async function mockRoutes(page) {
  await page.route('**/api/text/list', (r) =>
    r.fulfill({ contentType: 'application/json', body: JSON.stringify([{ id: 'txt-a', title: '测试文本' }]) }))
  await page.route('**/api/distill/cards/by-text/**', (r) => {
    const id = r.request().url().split('/').pop()
    r.fulfill({ contentType: 'application/json', body: JSON.stringify(id === 'txt-a' ? CARDS : []) })
  })
  await page.route('**/api/distill/cards/standalone', (r) =>
    r.fulfill({ contentType: 'application/json', body: JSON.stringify([]) }))
}

;(async () => {
  // ═══ 桌面 1280×900：双栏 + 各状态详情 ═══
  {
    const { browser, page, errors } = await openApp({ width: 1280, height: 900 })
    await mockRoutes(page)
    await login(page, { settleMs: 1500 })
    await seed(page)
    await pushView(page, 'distillWorkbench')
    await page.waitForSelector('.dw-shell')
    await page.waitForTimeout(900)

    const shell = await cs(page, '.dw-shell.dw-desktop', ['display', 'gridTemplateColumns'])
    const items = await page.evaluate(() => document.querySelectorAll('.dw-task-item').length)
    const sections = await page.evaluate(() => [...document.querySelectorAll('.dw-section-label')].map((e) => e.textContent.trim()))

    // 默认选中第一个任务（running）→ 详情断言
    const running = await page.evaluate(() => ({
      stepperActive: document.querySelectorAll('.dw-step.is-active').length,
      stepperDone: document.querySelectorAll('.dw-step.is-done').length,
      statCells: document.querySelectorAll('.dw-stat-grid .dw-tstat').length,
      logLines: document.querySelectorAll('.dw-log-line').length,
      logDots: [...document.querySelectorAll('.dw-log-line .dw-dot')].map((d) => d.className),
      cta: [...document.querySelectorAll('.dw-cta-btn')].map((e) => e.textContent.trim()),
      heroBadge: document.querySelector('.dw-hero .dw-badge')?.textContent?.trim() || '',
    }))
    await page.waitForTimeout(1600)
    const pctWidth = await page.evaluate(() => {
      const bar = document.querySelector('.dw-progress-bar')
      return bar ? parseFloat(bar.style.width) : null
    })

    // 点击 error 任务
    await page.locator('.dw-task-item').filter({ hasText: '江叙' }).click()
    await page.waitForTimeout(400)
    const errorView = await page.evaluate(() => ({
      cta: [...document.querySelectorAll('.dw-cta-btn')].map((e) => e.textContent.trim()),
      errBanner: !!document.querySelector('.dw-error-banner'),
      errStepError: document.querySelectorAll('.dw-step.is-error').length,
    }))

    // 点击已验收卡片
    await page.locator('.dw-task-item').filter({ hasText: '已验收' }).click()
    await page.waitForTimeout(400)
    const cardView = await page.evaluate(() => ({
      badge: document.querySelector('.dw-hero .dw-badge')?.textContent?.trim() || '',
      cta: [...document.querySelectorAll('.dw-cta-btn')].map((e) => e.textContent.trim()),
      identity: document.querySelector('.dw-card-identity')?.textContent || '',
      tagCount: document.querySelectorAll('.dw-card-tag').length,
    }))

    await shot(page, 'dw-desktop-running.png', 'screenshots/distill-workbench')
    console.log(JSON.stringify({ shell, items, sections, running, pctWidth, errorView, cardView, errors }, null, 2))
    await browser.close()
  }

  // ═══ 移动 390×844：单栏列表 → 详情 → 返回 ═══
  {
    const { browser, page, errors } = await openApp({ width: 390, height: 844 })
    await mockRoutes(page)
    await login(page, { settleMs: 1500 })
    await seed(page)
    await pushView(page, 'distillWorkbench')
    await page.waitForSelector('.dw-shell')
    await page.waitForTimeout(600)

    const mShell = await cs(page, '.dw-shell.dw-mobile', ['display'])
    const mItems = await page.evaluate(() => document.querySelectorAll('.dw-task-item').length)
    const mListFirst = await page.evaluate(() => !!document.querySelector('.dw-task-panel') && !document.querySelector('.dw-detail-scroll'))

    // 点击第一个任务 → 详情 + 返回按钮
    await page.locator('.dw-task-item').first().click()
    await page.waitForTimeout(500)
    const mDetail = await page.evaluate(() => !!document.querySelector('.dw-detail-scroll'))
    const mBackBtn = await page.evaluate(() => !!document.querySelector('.dw-back-btn-mobile'))

    // 返回 → 回列表
    await page.locator('.dw-back-btn-mobile').click()
    await page.waitForTimeout(400)
    const mListAgain = await page.evaluate(() => !!document.querySelector('.dw-task-panel') && !document.querySelector('.dw-detail-scroll'))

    await shot(page, 'dw-mobile-list.png', 'screenshots/distill-workbench')
    console.log(JSON.stringify({ mShell, mItems, mListFirst, mDetail, mBackBtn, mListAgain, errors }, null, 2))
    await browser.close()
  }
})().catch((e) => { console.error(e); process.exit(1) })
