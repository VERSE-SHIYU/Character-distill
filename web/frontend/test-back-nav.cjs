const { chromium } = require('playwright')

const BASE = 'http://localhost:5173'

;(async () => {
  const browser = await chromium.launch({ headless: true })
  const context = await browser.newContext()
  const page = await context.newPage()

  // Collect console logs
  const logs = []
  page.on('console', (msg) => logs.push(`[${msg.type()}] ${msg.text()}`))

  try {
    // 1. Login
    console.log('--- Navigating to app ---')
    await page.goto(BASE, { waitUntil: 'networkidle' })
    await page.waitForTimeout(1000)

    // Check if we're on login page
    const isLoginPage = await page.$('.login-submit')
    if (isLoginPage) {
      console.log('--- Logging in ---')
      await page.fill('#login-username', 'testadmin')
      await page.fill('#login-password', 'test1234')
      await page.click('.login-submit')
      await page.waitForTimeout(2000)
      await page.waitForSelector('.app-shell', { timeout: 5000 })
      console.log('--- Logged in ---')
    }

    // 2. Navigate to market to find an author
    console.log('--- Navigating to market ---')
    // Try clicking the market link in sidebar
    const marketLink = await page.$('button:has-text("市场")')
    if (marketLink) {
      await marketLink.click()
      await page.waitForTimeout(2000)
    }

    // 3. Try to find an author card and click it
    console.log('--- Looking for author card ---')
    const authorCard = await page.$('.market-card, [class*=author]')
    if (authorCard) {
      await authorCard.click()
      await page.waitForTimeout(2000)
    }

    // 4. Navigate to messages from author page
    console.log('--- Looking for 发私信 button ---')
    const msgBtn = await page.$('button:has-text("私信")')
    if (msgBtn) {
      await msgBtn.click()
      await page.waitForTimeout(2000)
    }

    // 5. Check if we're on messages page
    console.log('--- Checking current URL / view ---')
    const currentUrl = page.url()
    console.log('URL:', currentUrl)
    const messagesPage = await page.$('.messages-page')
    console.log('On messages page:', !!messagesPage)

    // 6. Check debug logs for previousView value
    const debugLogs = logs.filter(l => l.includes('[DEBUG-back]'))
    console.log('--- Debug logs for back button ---')
    debugLogs.forEach(l => console.log(l))

    // 7. Try clicking back
    const backBtn = await page.$('.chat-back-btn')
    if (backBtn) {
      console.log('--- Clicking back button ---')
      await backBtn.click()
      await page.waitForTimeout(2000)
    }

    // 8. Check where we ended up
    const afterUrl = page.url()
    console.log('URL after back:', afterUrl)
    const afterView = await page.evaluate(() => {
      // Try to read zustand store state
      try {
        // Access zustand store from __ZUSTAND_DEVTOOLS__ or window
        const store = window.__ZUSTAND__ || window.__ZUSTAND_DEVTOOLS__
        return 'unknown'
      } catch {
        return 'cannot access'
      }
    })
    console.log('View after back:', afterView)

    // Check page content
    const pageText = await page.textContent('body')
    const isHome = pageText.includes('首页') || pageText.includes('角色市场')
    const isAuthor = pageText.includes('作者') || pageText.includes('作品')
    const isMessages = pageText.includes('私信')
    const isLogin = pageText.includes('登录')

    console.log('--- Page detection ---')
    console.log('Home page:', isHome)
    console.log('Author page:', isAuthor)
    console.log('Messages page:', isMessages)
    console.log('Login page:', isLogin)

    // Print all debug logs
    console.log('\n--- All console logs relevant to debug ---')
    logs.filter(l => l.includes('DEBUG') || l.includes('previousView') || l.includes('goBack'))
      .forEach(l => console.log(l))

  } catch (err) {
    console.error('Error:', err.message)
  }

  await browser.close()
})()
