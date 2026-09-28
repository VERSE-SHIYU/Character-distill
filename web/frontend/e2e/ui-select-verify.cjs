// e2e/ui-select-verify.cjs — ui-select（@radix-ui/react-select 薄封装）浏览器验收
// 用法: TEST_PASSWORD=e2e-mock node e2e/ui-select-verify.cjs
//       TEST_PASSWORD=e2e-mock node e2e/ui-select-verify.cjs V1 V4   （只跑指定项，变异核验时用）
// 基线：docs/specs/ui-select-radix.md 的 S4（含「补充·环境」）。本 worktree 的栈是空库，
// 没有 testadmin 可登录，所以登录也走 mock；TEST_PASSWORD 只是 helpers 的存在性闸门，
// 占位值即可。
const { openApp, login, goToView, shot } = require('./helpers.cjs')

const THEMES = ['aurora', 'milktea', 'ocean', 'sakura', 'midnight', 'galaxy']
const DESKTOP = { width: 1280, height: 800 }
const MOBILE = { width: 390, height: 844 }

// 登录与被登录用户同一份：用户管理页里自己那一行只显示文字，id 相等即视为自己
const ADMIN = { id: 'admin1', username: 'testadmin', role: 'admin' }
const OTHER = {
  id: 'u2', username: 'u2', nickname: '他人乙', role: 'user',
  is_disabled: false, online: false, last_active_at: '', created_at: '',
}
// 「文本01」…「文本30」
const TEXTS = Array.from({ length: 30 }, (_, i) => ({
  id: i + 1,
  filename: `文本${String(i + 1).padStart(2, '0')}`,
}))

async function mockRoutes(page, reqs) {
  // 兜底先注册，具体路由后注册（Playwright 后注册的优先匹配）：未列出的 /api 一律 200 {}，
  // 这样空库 + 无真实账号也能把应用拉起来。
  await page.route('**/api/**', (r) => r.fulfill({ json: {} }))
  await page.route('**/api/auth/login', (r) =>
    r.fulfill({ json: { access_token: 'e2e', refresh_token: 'e2e', user: ADMIN } }))
  await page.route('**/api/auth/me', (r) => r.fulfill({ json: ADMIN }))
  await page.route('**/api/history/list*', (r) => {
    reqs.push(r.request().url())
    return r.fulfill({ json: { items: [], total: 0 } })
  })
  await page.route('**/api/admin/users/federated', (r) =>
    r.fulfill({ json: { local: [ADMIN, OTHER], peer: [], peer_unreachable: false } }))
  await page.route('**/api/text/list*', (r) => r.fulfill({ json: TEXTS }))
  await page.route('**/api/text/reading-progress/all', (r) => r.fulfill({ json: [] }))
  // 启动期的最小假数据（与 settings-verify.cjs 同款）
  await page.route('**/api/announcement/active', (r) => r.fulfill({ json: {} }))
  await page.route('**/api/market/featured*', (r) => r.fulfill({ json: [] }))
  await page.route('**/api/distill/cards/standalone', (r) => r.fulfill({ json: [] }))
  await page.route('**/api/distill/cards/by-text**', (r) => r.fulfill({ json: [] }))
  await page.route('**/api/auth/banner', (r) => r.fulfill({ json: { banner_data: '' } }))
  await page.route('**/api/auth/avatar', (r) => r.fulfill({ json: { avatar_data: '' } }))
  await page.route('**/api/auth/presence-visibility', (r) => r.fulfill({ json: { presence_visibility: 'all' } }))
  await page.route('**/api/auth/user/*/online', (r) => r.fulfill({ json: { online: true, last_active_at: '' } }))
  await page.route('**/api/market/my/following', (r) => r.fulfill({ json: { following: [] } }))
  await page.route('**/api/settings/config', (r) => r.fulfill({ json: { summary_threshold: 50 } }))
}

// 主题由 <html> 类名切换（src/utils/theme.js:23 就是这么做的）
const setTheme = (page, key) => page.evaluate((k) => {
  document.documentElement.className = `theme-${k}`
  try { localStorage.setItem('charsim-theme', k) } catch { /* ignore */ }
}, key)

const TEXT_TRIGGER = '[role="combobox"][aria-label="文本筛选"]'
const ROLE_TRIGGER = '[role="combobox"][aria-label="角色筛选"]'
const ADMIN_TRIGGER = '.admin-table [role="combobox"][aria-label="角色"]'

async function openMenu(page, trigger) {
  await trigger.focus()
  await page.keyboard.press('Enter')
  await page.waitForSelector('.ui-select-menu', { state: 'visible', timeout: 5000 })
  await page.waitForTimeout(150) // 等 popper 落位（--radix-* 变量与 data-side 就位）
}

async function gotoAdminUsers(page) {
  await goToView(page, 'admin', { viaHome: true })
  await page.waitForSelector('.admin-nav-item', { timeout: 10000 })
  await page.locator('.admin-nav-item', { hasText: '用户管理' }).click()
  await page.waitForSelector('.admin-table', { timeout: 10000 })
  const trig = page.locator(ADMIN_TRIGGER)
  await trig.first().waitFor({ timeout: 10000 })
  return trig.first()
}

async function gotoHistory(page) {
  await goToView(page, 'history', { viaHome: true })
  await page.waitForSelector('.history-toolbar', { timeout: 10000 })
}

// ══ V1：弹层几何 + 遮挡 ══
async function measureV1(page) {
  return page.evaluate(() => {
    const menu = document.querySelector('.ui-select-menu')
    const trig = document.querySelector('[role="combobox"][data-state="open"]')
    if (!menu || !trig) return { error: `menu=${!!menu} trigger=${!!trig}` }
    const mr = menu.getBoundingClientRect()
    const tr = trig.getBoundingClientRect()
    const cx = Math.round(mr.left + mr.width / 2)
    const cy = Math.round(mr.top + mr.height / 2)
    const hit = document.elementFromPoint(cx, cy)
    const inMenu = hit ? !!hit.closest('.ui-select-menu') : false
    return {
      triggerBottom: Math.round(tr.bottom), menuTop: Math.round(mr.top),
      triggerWidth: Math.round(tr.width), menuWidth: Math.round(mr.width),
      menuTop2: Math.round(mr.top), menuLeft: Math.round(mr.left),
      menuBottom: Math.round(mr.bottom), menuRight: Math.round(mr.right),
      viewport: { w: window.innerWidth, h: window.innerHeight },
      centerHit: hit ? (inMenu ? 'inside-menu' : `${hit.tagName}.${hit.className}`) : 'null',
    }
  })
}

// ══ V3：配色（临时元素取 --accent / --accent-soft 的计算值比对）══
async function measureV3(page) {
  return page.evaluate(() => {
    const probe = document.createElement('div')
    probe.style.cssText = 'position:absolute;left:-9999px;width:1px;height:1px'
    document.body.appendChild(probe)
    probe.style.color = 'var(--accent)'
    probe.style.background = 'var(--accent-soft)'
    const pc = getComputedStyle(probe)
    const accent = pc.color
    const accentSoft = pc.backgroundColor
    probe.remove()
    const checked = document.querySelector('.ui-select-option[data-state="checked"]')
    const hi = document.querySelector('.ui-select-option[data-highlighted]')
    // size="sm"（30px）的是用户管理表格里的角色下拉（AdminPanel.jsx:552-554），
    // 历史页那两个筛选框是 md（40px，由 V2 覆盖）
    const roleTrig = document.querySelector('.admin-table [role="combobox"][aria-label="角色"]')
    return {
      accent, accentSoft,
      checkedColor: checked ? getComputedStyle(checked).color : null,
      checkedText: checked ? checked.textContent.trim() : null,
      highlightedBg: hi ? getComputedStyle(hi).backgroundColor : null,
      highlightedText: hi ? hi.textContent.trim() : null,
      roleTriggerHeight: roleTrig ? getComputedStyle(roleTrig).height : null,
    }
  })
}

// ══ V4：长列表滚动 ══
async function measureV4(page) {
  return page.evaluate(async () => {
    const menu = document.querySelector('.ui-select-menu')
    const vp = menu.querySelector('[data-radix-select-viewport]')
    const menuHeight = Math.round(menu.getBoundingClientRect().height)
    const scrollHeight = vp.scrollHeight
    const clientHeight = vp.clientHeight
    vp.scrollTop = vp.scrollHeight
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))
    const opts = [...menu.querySelectorAll('.ui-select-option')]
    const last = opts[opts.length - 1]
    const lr = last.getBoundingClientRect()
    const vr = vp.getBoundingClientRect()
    return {
      menuHeight, scrollHeight, clientHeight,
      lastText: last.textContent.trim(),
      lastVisible: lr.top >= vr.top - 1 && lr.bottom <= vr.bottom + 1,
      scrolledBy: Math.round(vp.scrollTop),
      optionCount: opts.length,
    }
  })
}

// ══ 汇总 ══
const fail = []
const lines = []
const shots = []
const rec = (s) => { lines.push(s); console.log('  ' + s) }
const chk = (ok, msg) => { if (!ok) fail.push(msg); return ok ? 'PASS' : 'FAIL' }
const inViewport = (m) =>
  m.menuTop2 >= 0 && m.menuLeft >= 0 && m.menuBottom <= m.viewport.h && m.menuRight <= m.viewport.w
// 单个阶段抛错只记一笔，不让它吃掉后面阶段的实测值
const step = async (label, fn) => {
  try { await fn() } catch (e) { fail.push(`${label} 抛错: ${e.message}`); rec(`${label}: FAIL 抛错 ${e.message}`) }
}

async function runV1V3V4(page, theme) {
  let trig
  try {
    trig = await gotoAdminUsers(page)
  } catch (e) {
    rec(`V1 ${theme}: FAIL 进入用户管理页失败 — ${e.message}`)
    fail.push(`V1 ${theme} 进入用户管理页失败`)
    return
  }
  await setTheme(page, theme)
  await openMenu(page, trig)
  const v1 = await measureV1(page)
  await shot(page, `ui-select-${theme}-V1.png`)
  shots.push(`web/frontend/e2e/screenshots/ui-select-${theme}-V1.png`)
  if (v1.error) {
    rec(`V1 ${theme}: FAIL 探针取不到元素 — ${v1.error}`)
    fail.push(`V1 ${theme} 探针取不到元素`)
  } else {
    const a = chk(v1.menuTop >= v1.triggerBottom, `V1 ${theme}① 弹层上沿 ${v1.menuTop} 未 ≥ 触发器下沿 ${v1.triggerBottom}`)
    const b = chk(inViewport(v1), `V1 ${theme}② 弹层超出视口 ${JSON.stringify(v1.viewport)}`)
    const c = chk(v1.centerHit === 'inside-menu', `V1 ${theme}③ 弹层中心命中的是 ${v1.centerHit}`)
    const d = chk(v1.menuWidth >= v1.triggerWidth, `V1 ${theme}④ 弹层宽 ${v1.menuWidth} < 触发器宽 ${v1.triggerWidth}`)
    rec(`V1 ${theme}: ${[a, b, c, d].join('/')} — 上沿${v1.menuTop}≥下沿${v1.triggerBottom}；` +
        `视口内=${inViewport(v1)}；中心命中=${v1.centerHit}；宽${v1.menuWidth}≥${v1.triggerWidth}`)
  }
  await page.keyboard.press('ArrowDown')
  await page.waitForTimeout(80)
  const v3 = await measureV3(page)
  const p = chk(v3.checkedColor && v3.checkedColor === v3.accent,
    `V3 ${theme} 选中项文字色 ${v3.checkedColor} ≠ --accent ${v3.accent}`)
  const q = chk(v3.highlightedBg && v3.highlightedBg === v3.accentSoft,
    `V3 ${theme} 高亮项背景色 ${v3.highlightedBg} ≠ --accent-soft ${v3.accentSoft}`)
  const r = chk(v3.roleTriggerHeight === '30px',
    `V3 ${theme} 角色下拉触发器高 ${v3.roleTriggerHeight} ≠ 30px`)
  rec(`V3 ${theme}: ${[p, q, r].join('/')} — checked「${v3.checkedText}」${v3.checkedColor} vs accent ${v3.accent}；` +
      `highlighted「${v3.highlightedText}」${v3.highlightedBg} vs soft ${v3.accentSoft}；sm 高=${v3.roleTriggerHeight}`)
  await page.keyboard.press('Escape')
  if (!accents[theme]) accents[theme] = v3.accent

  try {
    await gotoHistory(page)
  } catch (e) {
    rec(`V4 ${theme}: FAIL 进入历史页失败 — ${e.message}`)
    fail.push(`V4 ${theme} 进入历史页失败`)
    return
  }
  await openMenu(page, page.locator(TEXT_TRIGGER))
  const v4 = await measureV4(page)
  await shot(page, `ui-select-${theme}-V4.png`)
  shots.push(`web/frontend/e2e/screenshots/ui-select-${theme}-V4.png`)
  const e = chk(v4.menuHeight <= 280, `V4 ${theme} 弹层高 ${v4.menuHeight} > 280`)
  const f = chk(v4.scrollHeight > v4.clientHeight, `V4 ${theme} Viewport 不可滚 ${v4.scrollHeight} ≤ ${v4.clientHeight}`)
  const g = chk(v4.lastText === '文本30' && v4.lastVisible,
    `V4 ${theme} 滚到底后「${v4.lastText}」可见=${v4.lastVisible}`)
  rec(`V4 ${theme}: ${[e, f, g].join('/')} — 弹层高${v4.menuHeight}≤280；scroll ${v4.scrollHeight}>${v4.clientHeight}；` +
      `滚${v4.scrolledBy}px 后「${v4.lastText}」可见=${v4.lastVisible}`)
  await page.keyboard.press('Escape')
}

// ══ V2：历史页两个筛选框尺寸与未选文案 ══
async function runV2(page, label) {
  await gotoHistory(page)
  const m = await page.evaluate(([a, b]) => {
    const q = (s) => document.querySelector(s)
    const t = q(a), c = q(b)
    if (!t || !c) return { error: `text=${!!t} role=${!!c}` }
    const tr = t.getBoundingClientRect(), cr = c.getBoundingClientRect()
    return {
      text: { h: Math.round(tr.height), w: Math.round(tr.width), label: t.textContent.trim() },
      role: { h: Math.round(cr.height), w: Math.round(cr.width), label: c.textContent.trim() },
    }
  }, [TEXT_TRIGGER, ROLE_TRIGGER])
  if (m.error) {
    rec(`V2 ${label}: FAIL 探针取不到元素 — ${m.error}`)
    fail.push(`V2 ${label} 探针取不到元素`)
    return
  }
  const r = []
  for (const [k, want] of [['text', '全部文本'], ['role', '全部角色']]) {
    const s = m[k]
    r.push(chk(s.h === 40, `V2 ${label} ${k} 高 ${s.h} ≠ 40px`))
    r.push(chk(s.w >= 140, `V2 ${label} ${k} 宽 ${s.w} < 140px`))
    r.push(chk(s.label === want, `V2 ${label} ${k} 未选文案「${s.label}」≠「${want}」`))
  }
  rec(`V2 ${label}: ${r.join('/')} — 文本 ${m.text.w}×${m.text.h}「${m.text.label}」；` +
      `角色 ${m.role.w}×${m.role.h}「${m.role.label}」`)
}

// ══ V5：键盘 键入跳转 + 选中 ══
async function runV5(page, reqs) {
  await gotoHistory(page)
  await page.waitForTimeout(400) // 等首次 list 请求落地，便于取「最后一次」
  const trig = page.locator(TEXT_TRIGGER)
  await trig.focus()
  await page.keyboard.press('Enter')
  await page.waitForSelector('.ui-select-menu', { state: 'visible', timeout: 5000 })
  await page.keyboard.press('ArrowDown')
  await page.keyboard.type('文')
  await page.waitForTimeout(350)
  await page.keyboard.press('Enter')
  await page.waitForTimeout(700)
  const last = reqs[reqs.length - 1] || ''
  const closed = await page.evaluate(() => !document.querySelector('[role="listbox"]'))
  const label = await trig.textContent()
  const a = chk(/[?&]text_id=/.test(last), `V5 最后一次 /api/history/list 未带 text_id — ${last}`)
  const b = chk(closed, 'V5 选中后下拉未关闭')
  rec(`V5: ${[a, b].join('/')} — 末次请求 ${last.replace(/^.*\/api/, '/api')}；已关闭=${closed}；触发器文案「${label.trim()}」`)
}

// ══ V6：Esc 关闭 + 焦点归位 ══
async function runV6(page) {
  await gotoHistory(page)
  const trig = page.locator(TEXT_TRIGGER)
  await openMenu(page, trig)
  await page.keyboard.press('Escape')
  await page.waitForTimeout(300)
  const m = await page.evaluate(() => ({
    menuGone: !document.querySelector('.ui-select-menu'),
    focusedLabel: document.activeElement ? document.activeElement.getAttribute('aria-label') : null,
    focusedState: document.activeElement ? document.activeElement.getAttribute('data-state') : null,
  }))
  const a = chk(m.menuGone, 'V6 Esc 后弹层仍在')
  const b = chk(m.focusedLabel === '文本筛选', `V6 Esc 后焦点在 ${m.focusedLabel}（应为「文本筛选」触发器）`)
  rec(`V6: ${[a, b].join('/')} — 弹层消失=${m.menuGone}；焦点 aria-label=${m.focusedLabel} data-state=${m.focusedState}`)
}

const accents = {}

;(async () => {
  const only = process.argv.slice(2).filter((a) => !a.startsWith('-'))
  const want = (id) => only.length === 0 || only.includes(id)

  const reqs = []
  const errs = []
  const { browser, page, errors } = await openApp(DESKTOP)
  try {
    await mockRoutes(page, reqs)
    await login(page, { settleMs: 1500 })

    if (want('V1') || want('V3') || want('V4')) {
      console.log('\n[V1/V3/V4] 6 个主题')
      for (const t of THEMES) await step(`V1/V3/V4 ${t}`, () => runV1V3V4(page, t))
    }
    if (want('V2')) {
      console.log('\n[V2] 历史页筛选框')
      await step('V2 desktop', async () => { await setTheme(page, 'aurora'); await runV2(page, 'desktop 1280×800') })
    }
    if (want('V5')) {
      console.log('\n[V5] 键盘键入跳转')
      await step('V5', async () => { await setTheme(page, 'aurora'); await runV5(page, reqs) })
    }
    if (want('V6')) {
      console.log('\n[V6] Esc 关闭')
      await step('V6', async () => { await setTheme(page, 'aurora'); await runV6(page) })
    }
  } catch (e) {
    fail.push(`未捕获异常: ${e.message}`)
    console.error(e)
  } finally {
    errs.push(...errors)
    await browser.close()
  }

  if (want('V2')) {
    const m = await openApp(MOBILE)
    try {
      await mockRoutes(m.page, [])
      await login(m.page, { settleMs: 1500 })
      await setTheme(m.page, 'aurora')
      console.log('\n[V2] 移动视口')
      await step('V2 mobile', () => runV2(m.page, 'mobile 390×844'))
    } catch (e) {
      fail.push(`V2 mobile 未捕获异常: ${e.message}`)
      console.error(e)
    } finally {
      errs.push(...m.errors)
      await m.browser.close()
    }
  }

  // 仪器自检：6 个主题的 --accent 各不相同。主题类没生效时 6 个值会全等，
  // 那时 V3 的比对会「自己跟自己相等」而假绿，所以这条必须过。
  const keys = Object.keys(accents)
  if (keys.length && only.length === 0) {
    const uniq = new Set(keys.map((k) => accents[k]))
    chk(uniq.size === keys.length,
      `主题切换仪器自检：${keys.length} 个主题只解析出 ${uniq.size} 个 --accent（应 6 个）`)
    rec(`主题 --accent：${keys.map((k) => `${k}=${accents[k]}`).join('  ')}`)
  }

  console.log('\npageErrors:', JSON.stringify(errs))
  if (errs.length) fail.push('存在 pageErrors: ' + JSON.stringify(errs))
  console.log('\n截图:')
  shots.forEach((s) => console.log('  ' + s))
  if (fail.length) {
    console.log('\nFAILURES:')
    fail.forEach((f) => console.log('  ✗ ' + f))
    process.exit(1)
  }
  console.log('\nALL PASS ✓')
})().catch((e) => { console.error(e); process.exit(1) })
