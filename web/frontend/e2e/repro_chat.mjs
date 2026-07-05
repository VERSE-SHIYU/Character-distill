import { chromium } from '@playwright/test'
import { fileURLToPath } from 'url'
import { dirname } from 'path'
import fs from 'fs'

const BASE = 'http://localhost:7862'
const OUT_DIR = fileURLToPath(new URL('./repro-out', import.meta.url))
fs.mkdirSync(OUT_DIR, { recursive: true })

async function screenshot(page, name) {
  await page.screenshot({ path: `${OUT_DIR}/${name}.png`, fullPage: true })
  console.log(`  screenshot: ${name}.png`)
}

async function main() {
  const browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
  })
  const page = await context.newPage()

  page.on('pageerror', (err) => {
    console.log('\n[PAGE_ERROR]', err.message)
    if (err.stack) console.log(err.stack.split('\n').slice(0, 10).join('\n'))
  })
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      console.log('\n[CONSOLE_ERR]', msg.text())
    }
  })

  // Login via UI
  console.log('1. Navigating to app...')
  await page.goto(BASE, { waitUntil: 'networkidle' })
  await screenshot(page, '01_login_page')

  console.log('2. Logging in...')
  await page.waitForSelector('#login-username', { timeout: 10000 })
  await page.fill('#login-username', 'testplay')
  await page.fill('#login-password', 'test1234')
  await page.locator('.login-submit').click()
  await page.waitForTimeout(3000)
  await screenshot(page, '02_after_login')

  // Navigate to text (创作) tab
  console.log('3. Clicking 创作 tab...')
  const tabbar = page.locator('.mobile-tabbar')
  if (await tabbar.isVisible({ timeout: 2000 }).catch(() => false)) {
    await tabbar.getByText('创作', { exact: true }).click()
    await page.waitForTimeout(2000)
  }
  await screenshot(page, '03_text_tab')

  // Click "角色管理" tab to see character cards
  console.log('4. Clicking 角色管理 tab...')
  const charTab = page.locator('.creation-tab', { hasText: '角色管理' })
  if (await charTab.isVisible({ timeout: 2000 }).catch(() => false)) {
    await charTab.click()
    await page.waitForTimeout(2000)
    await screenshot(page, '04_char_tab')
    console.log('  角色管理 tab clicked')
  } else {
    console.log('  角色管理 tab not found')
  }

  // Click the ⋯ menu button on a card to open dropdown, then click 聊天
  console.log('5. Clicking card menu button...')
  const menuBtn = page.locator('.creation-char-menu-btn').first()
  if (await menuBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
    await menuBtn.click()
    await page.waitForTimeout(500)
    await screenshot(page, '05_menu_open')

    // Click "聊天" button from dropdown
    console.log('6. Clicking 聊天...')
    const chatBtn = page.locator('.creation-char-dropdown button', { hasText: '聊天' })
    if (await chatBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
      await chatBtn.click()
      console.log('  聊天 clicked')
    } else {
      console.log('  聊天 button not found in dropdown')
    }
  } else {
    console.log('  No card menu button found')
  }

  await page.waitForTimeout(3000)
  await screenshot(page, '06_chat_view')

  // Check for ErrorBoundary
  console.log('7. Checking for errors...')
  const errorUI = page.locator('text=页面出错了')
  const hasError = await errorUI.isVisible({ timeout: 3000 }).catch(() => false)
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
  }

  // Print body text
  const bodyText = await page.evaluate(() => document.body?.innerText?.slice(0, 1500)).catch(() => '')
  if (bodyText) console.log('\nPage body text:', bodyText)

  console.log('\n=== DONE ===')
  await browser.close()
}

main().catch((err) => {
  console.error('Script error:', err)
  process.exit(1)
})
