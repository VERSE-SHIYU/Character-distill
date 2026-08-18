// e2e/helpers.cjs — 共享测试基建（业务原语封装）
// 历史背景：16 个脚本各自手写 launch/登录/导航/错误收集/聊天播种样板，改一处要改 16 处，
// 还出过「单引号包 ${} 不插值」的坑。现在统一抽到本模块，脚本只写各自的断言/探针。
// 用法: const { openApp, login, pushView, seedChat, cs, shot } = require('./helpers.cjs')
// （package.json 是 type:module，.cjs 文件之间必须带显式扩展名才能被 require）
const { chromium } = require('@playwright/test')
const path = require('path')
const fs = require('fs')

// 本地测试专用账号（Character-distill 项目红线：只允许操作 testadmin，绝不碰真实用户）
const BASE = 'http://localhost:7861'
const TEST_USER = 'testadmin'
const TEST_PASS = 'test1234'
const SHELL_SELECTOR = '.mobile-tabbar, [class*="shell"]'
const LOGIN_TIMEOUT = 15000

// 启动浏览器 + 注入 __E2E + 挂 pageerror 收集。返回 { browser, page, errors }
async function openApp({ width = 390, height = 844 } = {}) {
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width, height } })
  // __E2E 必须在 bundle 执行前设置（addInitScript 先于页面脚本），app 据此暴露 __appStore
  await page.addInitScript(() => { window.__E2E = true })
  const errors = []
  page.on('pageerror', e => errors.push(String(e).slice(0, 200)))
  return { browser, page, errors }
}

// 只到登录页（不填表）——需要给登录页截图/探针的脚本用
async function gotoLogin(page) {
  await page.goto(BASE)
  await page.waitForSelector('#login-username', { timeout: LOGIN_TIMEOUT })
}

// testadmin 登录 + 等主框架渲染。settleMs=0 跳过结算等待
async function login(page, { settleMs = 1500, shellSelector = SHELL_SELECTOR } = {}) {
  await gotoLogin(page)
  await page.fill('#login-username', TEST_USER)
  await page.fill('#login-password', TEST_PASS)
  await page.locator('.login-submit').click()
  await page.waitForSelector(shellSelector, { timeout: LOGIN_TIMEOUT })
  if (settleMs) await page.waitForTimeout(settleMs)
}

// 直接推入目标视图（store 导航唯一入口）
async function pushView(page, view) {
  await page.evaluate((v) => window.__appStore.getState().pushView(v), view)
}

// 先回 home 再进目标视图的导航（截图类脚本需要从 tab 根进入，避免视图栈过深）；
// authorUserId 传入时顺带切到某作者主页。delay 对应历史脚本里的 setTimeout
async function goToView(page, view, { viaHome = false, authorUserId, delay = 80 } = {}) {
  await page.evaluate(({ v, viaHome, authorUserId, delay }) => new Promise((resolve) => {
    const st = window.__appStore.getState()
    if (viaHome) { try { st.setView('home') } catch {} }
    setTimeout(() => {
      const st2 = window.__appStore.getState()
      if (authorUserId) st2.setAuthorUserId(authorUserId)
      st2.pushView(v)
      resolve(true)
    }, delay)
  }), { v: view, viaHome, authorUserId, delay })
}

// 聊天播种业务流：选文本 → 找角色卡 → startChat → （archive 弹窗则进入） → 可选注入 mock 消息
// inject 会整体替换 messages（原脚本行为）；affinity 一并写入（含 affinityEnabled）
// 返回 { ok, sessionId, view, reason }
async function seedChat(page, {
  textId, cardId,
  inject = [], affinity = null,
  textWait = 800, startWait = 1800, archiveWait = 1800, injectWait = 600,
} = {}) {
  return page.evaluate(async ({ textId, cardId, inject, affinity, textWait, startWait, archiveWait, injectWait }) => {
    const st = window.__appStore.getState()
    await st.selectText(textId)
    await new Promise(r => setTimeout(r, textWait))
    const card = (window.__appStore.getState().cards || []).find(c => c.id === cardId)
    if (!card) return { ok: false, reason: 'no-card', sessionId: null, view: null }
    try {
      await window.__appStore.getState().startChat(card)
      await new Promise(r => setTimeout(r, startWait))
      const s = window.__appStore.getState()
      if (s.archiveModalOpen && s.archiveList && s.archiveList.length) {
        await window.__appStore.getState().enterArchive(s.archiveList[0])
        await new Promise(r => setTimeout(r, archiveWait))
      }
    } catch (e) { return { ok: false, reason: String(e), sessionId: null, view: null } }
    const s2 = window.__appStore.getState()
    if (inject.length || affinity) {
      window.__appStore.setState({
        messages: inject.length ? inject : s2.messages,
        ...(affinity ? { affinityEnabled: true, affinity } : {}),
      })
    }
    await new Promise(r => setTimeout(r, injectWait))
    return { ok: !!s2.sessionId, sessionId: s2.sessionId, view: s2.currentView, reason: null }
  }, { textId, cardId, inject, affinity, textWait, startWait, archiveWait, injectWait })
}

// 计算样式探针（sel 不存在返回 null）
const cs = (page, sel, props) => page.evaluate(({ sel, props }) => {
  const el = document.querySelector(sel)
  if (!el) return null
  const c = getComputedStyle(el)
  const out = {}
  for (const p of props) out[p] = c[p]
  return out
}, { sel, props })

// 截图到 e2e/<dir>/<file>（自动建目录）
async function shot(page, file, dir = 'screenshots') {
  const out = path.join(__dirname, dir)
  fs.mkdirSync(out, { recursive: true })
  await page.screenshot({ path: path.join(out, file) })
}

module.exports = { BASE, TEST_USER, TEST_PASS, openApp, gotoLogin, login, pushView, goToView, seedChat, cs, shot }
