import { test } from '@playwright/test'

const BASE = 'http://localhost:7862'

test('reproduce 1-on-1 chat white screen', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.context().addInitScript(() => {
    localStorage.clear()
    Object.defineProperty(navigator, 'userAgent', {
      value: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
      configurable: true,
    })
  })

  page.on('pageerror', (err) => {
    console.log('\n[PAGE_ERROR]', err.message)
    if (err.stack) console.log(err.stack.split('\n').slice(0, 8).join('\n'))
  })
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      console.log('\n[CONSOLE_ERR]', msg.text())
    }
  })

  // Login via UI
  await page.goto(BASE)
  await page.waitForSelector('#login-username', { timeout: 10000 })
  await page.fill('#login-username', 'testplay')
  await page.fill('#login-password', 'test1234')
  await page.locator('.login-submit').click()
  await page.waitForTimeout(3000)
  await page.screenshot({ path: 'e2e/01_login.png', fullPage: true })

  // Check current view
  const view = await page.evaluate(() => {
    try { return window.__appStore?.getState()?.currentView } catch { return null }
  }).catch(() => null)
  console.log('View after login:', view)

  // Navigate to text (创作 tab) then select text and push chat
  const tabbar = page.locator('.mobile-tabbar')
  if (await tabbar.isVisible({ timeout: 2000 }).catch(() => false)) {
    await tabbar.getByText('创作', { exact: true }).click()
    await page.waitForTimeout(1500)
  }
  await page.screenshot({ path: 'e2e/02_text_tab.png', fullPage: true })

  // Use store to select text and navigate to chat
  await page.evaluate(() => {
    const store = window.__appStore
    if (!store) { console.log('NO STORE'); return }
    const s = store.getState()

    // Load texts (needs cards to show up)
    s.loadTexts().catch(() => {})

    // Select the demo text
    s.selectText('txt_demo001')
    console.log('Text selected')

    // Set currentCard and push chat - this is the flow when clicking a card to enter chat
    store.setState({
      currentCard: {
        id: 'card_demo001',
        name: '小明',
        text_id: 'txt_demo001',
        card_json: JSON.stringify({ name: '小明', identity: '学生', description: '测试角色' }),
      },
    })
    s.pushView('chat')
    console.log('Pushed chat view')
  })

  await page.waitForTimeout(3000)
  await page.screenshot({ path: 'e2e/03_chat.png', fullPage: true })

  // Check for ErrorBoundary or errors
  const hasError = await page.locator('text=页面出错了').isVisible({ timeout: 3000 }).catch(() => false)
  if (hasError) {
    console.log('\n*** ERROR BOUNDARY TRIGGERED ***')
    const errText = await page.locator('pre').first().textContent()
    console.log('Error message:', errText)
    const details = page.locator('details summary')
    if (await details.isVisible().catch(() => false)) {
      await details.click()
      await page.waitForTimeout(300)
      const stack = await page.locator('details pre').textContent()
      console.log('Stack trace:', stack)
    }
    await page.screenshot({ path: 'e2e/04_error.png', fullPage: true })
  }

  // Print body text for debugging
  const bodyText = await page.evaluate(() => document.body?.innerText?.slice(0, 1000)).catch(() => '')
  if (bodyText) console.log('\nBody text:', bodyText)

  console.log('\n=== DONE ===')
})
