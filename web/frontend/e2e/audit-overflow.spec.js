// @ts-check
/**
 * Layout overflow audit — mobile (390×844) + desktop (1440×900).
 *
 * Per page:
 *   - Asserts documentElement.scrollWidth === clientWidth (no page-level overflow)
 *   - Asserts target message containers have no internal horizontal scroll
 *   - Logs all [HSCROLL] / [R_OVERFLOW] elements to audit log
 *
 * Usage:
 *   npx playwright test e2e/audit-overflow.spec.js
 */

import { test, expect } from '@playwright/test'
import * as fs from 'fs'

const AUDIT_LOG = 'e2e/audit-overflow-results.txt'
const SCREENSHOT_DIR = 'e2e/screenshots'
fs.mkdirSync(SCREENSHOT_DIR, { recursive: true })
const CONTAINER_SELECTORS = '.chat-messages, .private-chat-body, .group-chat-messages-area, .messages-chat-area'

const OVERFLOW_SCAN = `
  (function() {
    function cls(el) {
      if (!el.className) return '';
      if (typeof el.className === 'string') return '.' + el.className.trim().split(/\\s+/).join('.');
      if (el.className.baseVal) return '.' + el.className.baseVal.trim().split(/\\s+/).join('.');
      return '';
    }
    const all = document.querySelectorAll('*');
    const bad = [];
    const iw = window.innerWidth;
    for (const el of all) {
      const r = el.getBoundingClientRect();
      const tag = el.tagName + cls(el);
      if (el.scrollWidth > el.clientWidth) {
        bad.push('[HSCROLL] ' + tag + ' scrollW=' + el.scrollWidth + ' clientW=' + el.clientWidth + ' r=' + r.width.toFixed(0));
      }
      if (r.right > iw + 1) {
        bad.push('[R_OVERFLOW] ' + tag + ' right=' + r.right.toFixed(0) + ' iw=' + iw);
      }
      if (r.left < -1) {
        bad.push('[L_OVERFLOW] ' + tag + ' left=' + r.left.toFixed(0));
      }
    }
    const dd = document.documentElement;
    bad.push('--- doc: scrollW=' + dd.scrollWidth + ' clientW=' + dd.clientWidth + ' scrollH=' + dd.scrollHeight + ' clientH=' + dd.clientHeight);
    return bad.join('\\n');
  })()
`

const CONTAINER_SCROLL_CHECK = `
  (function() {
    const els = document.querySelectorAll('${CONTAINER_SELECTORS}');
    const bad = [];
    for (const el of els) {
      if (el.scrollWidth > el.clientWidth) {
        bad.push(el.className + ' scrollW=' + el.scrollWidth + ' clientW=' + el.clientWidth);
      }
    }
    return bad;
  })()
`

/** Shared pages to visit */
const PAGES = [
  { label: 'chat-placeholder',        action: 'pushView("chat")' },
  { label: 'character-chat',          mockSession: true },
  { label: 'private-messages',         action: 'pushView("messages")', clickFirst: '.messages-conv-item' },
  { label: 'group-chat-list',          action: 'pushView("groupChat")', clickFirst: '.messages-conv-item' },
]

async function login(page) {
  await page.goto('/')
  await page.fill('input[name="username"]', 'testadmin')
  await page.fill('input[type="password"]', 'test1234')
  await page.click('button[type="submit"]')

  // Wait for either mobile-tabbar (mobile) or sidebar-trigger (desktop)
  const isMobile = await page.locator('.mobile-tabbar').isVisible({ timeout: 15000 }).catch(() => false)
  if (isMobile) {
    // dismiss cross-border consent if it appears
    try {
      const chk = page.locator('.legal-consent-label input[type="checkbox"]')
      if (await chk.isVisible({ timeout: 3000 })) {
        await chk.check()
        await page.locator('.modal-card .btn-primary').click()
        await page.waitForTimeout(800)
      }
    } catch { /* no modal */ }
  } else {
    // Desktop: wait for the app to finish loading
    await page.waitForSelector('.main-panel', { timeout: 20000 })
    await page.waitForTimeout(1500)
  }
}


async function setupChatMock(page) {
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
  await page.evaluate(() => {
    window.__appStore.setState({ currentCard: { id: 9999, name: '测试角色小明', card_id: 'test-card-999', text_id: 1 } })
    window.__appStore.getState().pushView('chat')
  })
}

async function ensureContainer(page, label) {
  if (label === 'character-chat') return // containers appear after mock
  // Navigate via store
  if (label === 'chat-placeholder') {
    await page.evaluate(() => { window.__appStore.getState().pushView('chat') })
  } else if (label === 'private-messages') {
    await page.evaluate(() => { window.__appStore.getState().pushView('messages') })
  } else if (label === 'group-chat-list') {
    await page.evaluate(() => { window.__appStore.getState().pushView('groupChat') })
  }
}

async function auditAndAssert(page, label) {
  await page.waitForTimeout(1000)

  // Screenshot for visual regression review
  const vp = page.viewportSize()
  const prefix = vp && vp.width >= 1024 ? 'desktop' : 'mobile'
  await page.screenshot({ path: `${SCREENSHOT_DIR}/${prefix}-${label}.png`, fullPage: false })

  // Scroll check on message containers
  const containerBad = await page.evaluate(CONTAINER_SCROLL_CHECK)
  const containerOk = containerBad.length === 0

  // Full overflow scan
  const scanResult = await page.evaluate(OVERFLOW_SCAN)
  const scanLines = scanResult.split('\n')
  const hscrollLines = scanLines.filter(l => l.startsWith('[HSCROLL]'))
  const roverflowLines = scanLines.filter(l => l.startsWith('[R_OVERFLOW]'))
  const docLine = scanLines.find(l => l.startsWith('--- doc:')) || ''
  const docMatch = docLine.match(/scrollW=(\d+) clientW=(\d+)/)
  const docOk = docMatch ? docMatch[1] === docMatch[2] : false

  // Log to file
  const block = '=== ' + label + ' ===\n' + scanLines.join('\n') + '\n\n'
  fs.appendFileSync(AUDIT_LOG, block, 'utf-8')

  // Assertions — real layout issues (tolerate ocean-bg and adm-px-tester)
  const realHscroll = hscrollLines.filter(l =>
    !l.includes('ocean-bg') && !l.includes('adm-px-tester')
  )
  const realRoverflow = roverflowLines.filter(l =>
    !l.includes('ocean-bg') && !l.includes('adm-px-tester')
  )

  expect(docOk, label + ': documentElement scrollWidth === clientWidth').toBe(true)
  expect(containerOk, label + ': all message containers have scrollWidth <= clientWidth').toBe(true)
  expect(realHscroll.length, label + ': no real [HSCROLL] elements (excl. ocean-bg/adm-px-tester)').toBe(0)
  expect(realRoverflow.length, label + ': no real [R_OVERFLOW] elements (excl. ocean-bg/adm-px-tester)').toBe(0)
}

// ─── Test: iterate all pages for a given viewport ───
function createPageTests() {
  for (const pageDef of PAGES) {
    test(pageDef.label, async ({ page }) => {
      await login(page)

      if (pageDef.mockSession) {
        await setupChatMock(page)
        await page.waitForTimeout(3000)
      } else {
        await ensureContainer(page, pageDef.label)
        await page.waitForTimeout(1500)

        // If there's a first-item click, do it after initial audit
        if (pageDef.clickFirst) {
          // Already audited the list page above
        }
      }

      await auditAndAssert(page, pageDef.label)

      // For DMs and groups, click first item and audit sub-page
      if (pageDef.clickFirst) {
        const item = page.locator(pageDef.clickFirst).first()
        if (await item.isVisible({ timeout: 2000 }).catch(() => false)) {
          await item.click()
          await page.waitForTimeout(1500)
          await auditAndAssert(page, pageDef.label + '-chat')
        }
      }
    })
  }
}

test.describe('Mobile overflow audit (390×844)', () => {
  test.use({ viewport: { width: 390, height: 844 } })
  createPageTests()
})

test.describe('Desktop overflow audit (1440×900)', () => {
  test.use({ viewport: { width: 1440, height: 900 } })
  createPageTests()
})
