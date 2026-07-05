// @ts-check
/**
 * Mobile layout overflow audit.
 * 390×844 viewport. Logs every overflowing element per page.
 * After running, check console output for the overflow list.
 *
 * Usage:
 *   npx playwright test e2e/audit-overflow.spec.js
 */

import { test, expect } from '@playwright/test'
import * as fs from 'fs'

const AUDIT_LOG = 'e2e/audit-overflow-results.txt'
const OVERFLOW_SELECTOR = `
  (function() {
    const all = document.querySelectorAll('*');
    const bad = [];
    const iw = window.innerWidth;
    for (const el of all) {
      const r = el.getBoundingClientRect();
      if (el.scrollWidth > el.clientWidth) {
        bad.push('[HSCROLL] ' + el.tagName + (el.className ? '.' + el.className.trim().split(/\\s+/).join('.') : '') + ' scrollW=' + el.scrollWidth + ' clientW=' + el.clientWidth + ' r=' + r.width.toFixed(0));
      }
      if (el.scrollHeight > el.clientHeight) {
        bad.push('[VSCROLL] ' + el.tagName + (el.className ? '.' + el.className.trim().split(/\\s+/).join('.') : '') + ' scrollH=' + el.scrollHeight + ' clientH=' + el.clientHeight);
      }
      if (r.right > iw + 1) {
        bad.push('[R_OVERFLOW] ' + el.tagName + (el.className ? '.' + el.className.trim().split(/\\s+/).join('.') : '') + ' right=' + r.right.toFixed(0) + ' iw=' + iw);
      }
      if (r.left < -1) {
        bad.push('[L_OVERFLOW] ' + el.tagName + (el.className ? '.' + el.className.trim().split(/\\s+/).join('.') : '') + ' left=' + r.left.toFixed(0));
      }
    }
    const dd = document.documentElement;
    bad.push('--- doc: scrollW=' + dd.scrollWidth + ' clientW=' + dd.clientWidth + ' scrollH=' + dd.scrollHeight + ' clientH=' + dd.clientHeight);
    return bad.join('\\n');
  })()
`

test.describe('Mobile overflow audit (390×844)', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test.beforeEach(async ({ page }) => {
    page.on('console', (msg) => {
      if (msg.type() === 'log' && msg.text().startsWith('[AUDIT]')) {
        console.log(msg.text())
      }
    })
  })

  async function login(page) {
    await page.goto('/')
    await page.fill('input[name="username"]', 'testadmin')
    await page.fill('input[type="password"]', 'test1234')
    await page.click('button[type="submit"]')
    await page.waitForSelector('.mobile-tabbar', { timeout: 15000 })
    // dismiss cross-border consent if it appears
    try {
      const chk = page.locator('.legal-consent-label input[type="checkbox"]')
      if (await chk.isVisible({ timeout: 3000 })) {
        await chk.check()
        await page.locator('.modal-card .btn-primary').click()
        await page.waitForTimeout(800)
      }
    } catch { /* no modal */ }
  }

  async function audit(page, label) {
    const result = await page.evaluate(OVERFLOW_SELECTOR)
    const lines = result.split('\n')
    const block = '=== ' + label + ' ===\n' + lines.join('\n') + '\n\n'
    fs.appendFileSync(AUDIT_LOG, block, 'utf-8')
  }

  // ─── 1. Chat placeholder (no card selected) ───
  test('1. Chat placeholder page', async ({ page }) => {
    await login(page)
    // Navigate to chat via store
    await page.evaluate(() => {
      window.__appStore.getState().pushView('chat')
    })
    await page.waitForTimeout(1000)
    await audit(page, 'chat-placeholder')
  })

  // ─── 2. Character chat with long message ───
  test('2. Character chat with long text', async ({ page }) => {
    await login(page)

    // Mock start_session to avoid LLM key requirement
    const longMsg = 'x'.repeat(120)
    await page.route('**/api/cards/*/start_session', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          session_id: 'mock-session-audit-001',
          messages: [
            { role: 'assistant', content: '你好！我是测试角色小明。', id: 'mock-msg-1' },
            { role: 'user', content: '你好', id: 'mock-msg-2' },
            { role: 'assistant', content: longMsg, id: 'mock-msg-3' },
          ],
        }),
      })
    })

    // Use store to set card and navigate to chat.
    // ChatArea auto-recovery will call startChat which triggers the mocked API.
    await page.evaluate(() => {
      window.__appStore.setState({ currentCard: { id: 9999, name: '测试角色小明', card_id: 'test-card-999', text_id: 1 } })
      window.__appStore.getState().pushView('chat')
    })
    await page.waitForTimeout(3000)

    await audit(page, 'character-chat')
  })

  // ─── 3. Private messages (DM) ───
  test('3. Private messages page', async ({ page }) => {
    await login(page)

    // Navigate to messages
    await page.evaluate(() => {
      window.__appStore.getState().pushView('messages')
    })
    await page.waitForTimeout(1500)

    await audit(page, 'private-messages')

    // If there's a conversation, enter it
    const convItem = page.locator('.messages-conv-item').first()
    if (await convItem.isVisible({ timeout: 2000 }).catch(() => false)) {
      await convItem.click()
      await page.waitForTimeout(1000)
      await audit(page, 'private-messages-chat')
    }
  })

  // ─── 4. Group chat ───
  test('4. Group chat page', async ({ page }) => {
    await login(page)

    // Navigate to group chat
    await page.evaluate(() => {
      window.__appStore.getState().pushView('groupChat')
    })
    await page.waitForTimeout(1500)

    // Audit list view
    await audit(page, 'group-chat-list')

    // If there's a group, enter it
    const groupItem = page.locator('.messages-conv-item').first()
    if (await groupItem.isVisible({ timeout: 2000 }).catch(() => false)) {
      await groupItem.click()
      await page.waitForTimeout(1500)
      await audit(page, 'group-chat-detail')
    }
  })
})
