// @ts-check
// Reproduction script: verify ChatView renders without white screen
// Mocks /api/start_session so no LLM key needed.
// Requires a running backend at localhost:7861 (Docker or dev).
//
// Usage:
//   cd web/frontend && npx playwright test e2e/repro-chat-view.spec.js

import { test, expect } from '@playwright/test'

test.describe('ChatView rendering after no-undef fixes', () => {
  test('chat view renders with mocked start_session', async ({ page }) => {
    // Intercept /start_session to bypass LLM key requirement
    await page.route('**/api/cards/*/start_session', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          session_id: 'mock-session-123',
          messages: [{ role: 'assistant', content: '你好！我是测试角色。' }],
        }),
      })
    })

    await page.goto('/')
    await page.waitForTimeout(500)

    // Login
    await page.fill('input[name="username"]', 'testadmin')
    await page.fill('input[type="password"]', 'test1234')
    await page.click('button[type="submit"]')
    await page.waitForTimeout(1500)

    // Navigate to character tab via the mobile bottom tab bar
    const charNav = page.locator('.mobile-tabbar a, .mobile-tabbar button, [class*="tab"]', { hasText: '角色' }).first()
    if (await charNav.isVisible()) await charNav.click()
    else {
      // fallback: try any nav link
      await page.goto('/character')
    }
    await page.waitForTimeout(2000)

    // Click the first character card to open detail
    const firstCard = page.locator('.card-item, .char-card, [class*="card-item"]').first()
    if (await firstCard.isVisible({ timeout: 3000 }).catch(() => false)) {
      await firstCard.click()
      await page.waitForTimeout(1000)

      // Click "开始聊天"
      const chatBtn = page.locator('button', { hasText: '开始聊天' }).first()
      if (await chatBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
        await chatBtn.click()
      }
    }

    await page.waitForTimeout(3000)

    // Screenshot for manual review
    await page.screenshot({ path: 'e2e/repro-chat-view.png', fullPage: true })

    // Verify: page rendered actual content, not white screen
    const bodyText = await page.locator('body').innerText()
    expect(bodyText.length).toBeGreaterThan(50)

    // Verify chat area or messages container exists
    const chatContainer = page.locator('.chat-messages, .chat-area, [class*="message"]').first()
    await expect(chatContainer).toBeVisible({ timeout: 3000 })
  })
})
