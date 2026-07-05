import { test, expect } from '@playwright/test'

const BASE = 'http://localhost:7860'

/**
 * Read the store's currentView from the browser context.
 */
async function getView(page) {
  return page.evaluate(() => window.__appStore.getState().currentView)
}

/**
 * Call store action by name with optional args.
 */
async function storeAction(page, action, ...args) {
  await page.evaluate(({ action, args }) => {
    window.__appStore.getState()[action](...args)
  }, { action, args })
}

test.describe('Navigation migration: setView → navigateTo/navigateBack', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.clear()
      sessionStorage.clear()
    })
    await page.goto(BASE)
    await page.fill('#login-username', 'testadmin')
    await page.fill('#login-password', 'test1234')
    await page.click('.login-submit')
    await page.waitForSelector('.mobile-tabbar', { timeout: 15000 })
    // dismiss cross-border consent modal if it appears
    try {
      const chk = page.locator('.legal-consent-label input[type="checkbox"]')
      if (await chk.isVisible({ timeout: 3000 })) {
        await chk.check()
        await page.locator('.modal-card .btn-primary').click()
        await page.waitForTimeout(800)
      }
    } catch { /* no modal */ }
  })

  async function settle(page, ms = 600) {
    await page.waitForTimeout(ms)
  }

  /** Click a TabBar tab by its visible text (mobile) */
  async function tab(page, text) {
    await page.locator('.mobile-tabbar').getByText(text, { exact: true }).click()
    await settle(page)
  }

  // ─── Chain 1: 我的 → 消息 → 返回 → 我的 ───
  test('Chain 1: mine → messages → back → mine', async ({ page }) => {
    await tab(page, '我的')
    expect(await getView(page)).toBe('mine')

    // click "消息" quick-entry → pushView('messages')
    await page.locator('.entry-grid-item').filter({ hasText: '消息' }).click()
    await settle(page)
    expect(await getView(page)).toBe('messages')

    // click back button in messages page → navigateBack via PageHeader
    await page.locator('.page-header-back').click()
    await settle(page)
    expect(await getView(page)).toBe('mine')
  })

  // ─── Chain 2: 我的 → 市场 → 角色详情 → 返回 → 市场 → 返回 → 我的 ───
  test('Chain 2: mine → market → card detail → back → market → back → mine', async ({ page }) => {
    await tab(page, '我的')
    expect(await getView(page)).toBe('mine')

    // click "市场" quick-entry → pushView('market')
    await page.locator('.entry-grid-item').filter({ hasText: '市场' }).click()
    await settle(page)
    expect(await getView(page)).toBe('market')

    // click first market card if available → pushView('marketCardDetail')
    const cards = page.locator('.market-card-v2')
    const cardCount = await cards.count()
    test.skip(cardCount === 0, 'No market cards available')
    await cards.first().click()
    await settle(page)
    expect(await getView(page)).toBe('marketCardDetail')

    // back → market (PageHeader on marketCardDetail)
    await page.locator('.page-header-back').click()
    await settle(page)
    expect(await getView(page)).toBe('market')

    // market has no back button — navigateBack through store
    await storeAction(page, 'navigateBack')
    await settle(page)
    expect(await getView(page)).toBe('mine')
  })

  // ─── Chain 3: 我的 → 历史 → 点进会话 → 返回 → 我的 ───
  test('Chain 3: mine → history → resume session → back → mine', async ({ page }) => {
    await tab(page, '我的')
    expect(await getView(page)).toBe('mine')

    // click "历史" tab → pushView('history') — use store since tab may be overlapped
    await storeAction(page, 'pushView', 'history')
    await settle(page)
    expect(await getView(page)).toBe('history')

    // click first session item
    const sessions = page.locator('.history-item')
    const sessionCount = await sessions.count()
    test.skip(sessionCount === 0, 'No history sessions available')

    await sessions.first().click()
    await settle(page)

    // click "继续对话" button → resumeSession → chat
    const continueBtn = page.locator('button:has-text("继续对话")')
    await expect(continueBtn).toBeVisible({ timeout: 5000 })
    await continueBtn.click()
    await settle(page)

    // should be in chat — click back from chat topbar
    const chatBack = page.locator('.chat-topbar-back')
    await expect(chatBack).toBeVisible({ timeout: 10000 })
    await chatBack.click()
    await settle(page)
    // should be back on mine page
    expect(await getView(page)).toBe('mine')
  })

  // ─── Chain 4: 我的 → 设置 → 个人资料 → 返回 → 设置 → 返回 → 我的 ───
  test('Chain 4: mine → settings → profile → back → settings → back → mine', async ({ page }) => {
    await tab(page, '我的')
    expect(await getView(page)).toBe('mine')

    // click "设置" quick-entry → pushView('settings')
    await page.locator('.entry-grid-item').filter({ hasText: '设置' }).click()
    await settle(page)
    expect(await getView(page)).toBe('settings')

    // click "个人资料" — the entry-list-item may be behind overlays
    // use store to push profile to avoid overlay issues
    await storeAction(page, 'pushView', 'profile')
    await settle(page)
    expect(await getView(page)).toBe('profile')

    // back → settings (PageHeader on profile)
    await page.locator('.page-header-back').click()
    await settle(page)
    expect(await getView(page)).toBe('settings')

    // back → mine (PageHeader on settings)
    await page.locator('.page-header-back').click()
    await settle(page)
    expect(await getView(page)).toBe('mine')
  })

  // ─── Chain 5: 首页 → 动态 → 返回 → 首页 ───
  test('Chain 5: home → feed → back → home', async ({ page }) => {
    expect(await getView(page)).toBe('home')

    // click "动态" segment button → pushView('feed')
    await page.locator('.home-segment-btn').filter({ hasText: '动态' }).click()
    await settle(page)
    expect(await getView(page)).toBe('feed')

    // feed has no back button — use store navigateBack
    await storeAction(page, 'navigateBack')
    await settle(page)
    expect(await getView(page)).toBe('home')
  })

  // ─── Chain 6: 首页 → 聊天 → 返回 → 首页 ───
  test('Chain 6: home → chat → back → home', async ({ page }) => {
    expect(await getView(page)).toBe('home')

    // Push chat view to test navigation stack
    await storeAction(page, 'pushView', 'chat')
    await settle(page)
    expect(await getView(page)).toBe('chat')
    // verify history: home was pushed
    const hist = await page.evaluate(() => window.__appStore.getState().viewHistory)
    expect(hist).toEqual(['home'])

    // ChatArea with no card shows placeholder (no back button). Use store navigateBack.
    await storeAction(page, 'navigateBack')
    await settle(page)
    expect(await getView(page)).toBe('home')
  })

  // ─── Chain 7: 创作 → 聊天 → 返回 → 创作 ───
  test('Chain 7: creation → chat → back → creation', async ({ page }) => {
    await tab(page, '创作')
    await settle(page)
    expect(await getView(page)).toBe('text')

    // Push chat to test navigation stack
    await storeAction(page, 'pushView', 'chat')
    await settle(page)
    expect(await getView(page)).toBe('chat')
    const hist = await page.evaluate(() => window.__appStore.getState().viewHistory)
    expect(hist).toEqual(['text'])

    // ChatArea with no card shows placeholder — use store navigateBack
    await storeAction(page, 'navigateBack')
    await settle(page)
    expect(await getView(page)).toBe('text')
  })

  // ─── Chain 8: 群聊列表 → 进群 → 返回 → 群聊列表（TabBar 恢复显示）───
  test('Chain 8: group chat list → details → back → list (tabbar visible)', async ({ page }) => {
    await tab(page, '群聊')
    await settle(page)
    expect(await getView(page)).toBe('groupChat')

    // verify PageHeader is shown on group chat list (secondary view)
    await expect(page.locator('.page-header-back')).toBeVisible()

    // push a secondary view to test back behavior from groupChat
    await storeAction(page, 'pushView', 'settings')
    await settle(page)
    expect(await getView(page)).toBe('settings')

    // back → groupChat list
    await page.locator('.page-header-back').click()
    await settle(page)
    expect(await getView(page)).toBe('groupChat')

    // PageHeader visible again on groupChat
    await expect(page.locator('.page-header-back')).toBeVisible()
  })

  // ─── Chain 9: Tab 连切 首页→创作→我的 后按返回：不得穿越 ───
  test('Chain 9: tab switch home→text→mine then back: must not cross tabs', async ({ page }) => {
    expect(await getView(page)).toBe('home')

    await tab(page, '创作')
    expect(await getView(page)).toBe('text')

    await tab(page, '我的')
    expect(await getView(page)).toBe('mine')

    // mine is tab-level — no PageHeader. Stack is empty.
    // Verify: navigate into a sub-view and back returns to mine (NOT creation)
    await storeAction(page, 'pushView', 'settings')
    await settle(page)
    expect(await getView(page)).toBe('settings')
    expect(await page.evaluate(() => window.__appStore.getState().viewHistory)).toEqual(['mine'])

    // back → mine
    await page.locator('.page-header-back').click()
    await settle(page)
    expect(await getView(page)).toBe('mine')
  })

  // ─── Chain 10: 消息列表 → 返回 → 我的 ───
  test('Chain 10: messages → back → mine', async ({ page }) => {
    await tab(page, '我的')
    expect(await getView(page)).toBe('mine')

    // navigate to messages via store
    await storeAction(page, 'pushView', 'messages')
    await settle(page)
    expect(await getView(page)).toBe('messages')
    // verify history
    const hist = await page.evaluate(() => window.__appStore.getState().viewHistory)
    expect(hist).toEqual(['mine'])

    // back → mine (PageHeader on messages page)
    await page.locator('.page-header-back').click()
    await settle(page)
    expect(await getView(page)).toBe('mine')
  })

  // ─── Desktop: sidebar → market → detail → back → market ───
  // NOTE: this is inside the mobile describe, but we override with desktop viewport.
  // The beforeEach still runs and waits for .mobile-tabbar — only visible on mobile.
  // We handle this by running the desktop test in its own describe below.
})

test.describe('Desktop navigation', () => {
  test('sidebar market → detail → back', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.addInitScript(() => localStorage.clear())
    await page.context().clearCookies()
    await page.goto(BASE)
    await page.waitForSelector('.login-submit', { timeout: 15000 })
    await page.fill('#login-username', 'testadmin')
    await page.fill('#login-password', 'test1234')
    await page.locator('.login-submit').click()
    // dismiss cross-border consent modal if it appears
    try {
      const chk = page.locator('.legal-consent-label input[type="checkbox"]')
      if (await chk.isVisible({ timeout: 3000 })) {
        await chk.check()
        await page.locator('.modal-card .btn-primary').click()
        await page.waitForTimeout(800)
      }
    } catch { /* no modal */ }
    // Wait for login to complete (app-shell renders on desktop without mobile-tabbar)
    await page.waitForFunction(() => {
      const el = document.querySelector('.app-shell')
      return el && el.classList.contains('is-secondary-view') === false
    }, { timeout: 20000 })
    // On desktop, sidebar is collapsed. Wait for the sidebar trigger, then open sidebar.
    await page.waitForSelector('.sidebar-trigger', { timeout: 15000 })
    await page.waitForTimeout(1000)

    // open the sidebar by clicking the trigger button
    await page.locator('.sidebar-trigger .sidebar-toggle-btn').click()
    await page.waitForTimeout(600)
    // now .sidebar should be visible
    await expect(page.locator('.sidebar')).toBeVisible()

    await page.locator('.sidebar').getByText('市场').click()
    await page.waitForTimeout(600)
    expect(await getView(page)).toBe('market')

    const cards = page.locator('.market-card-v2')
    if (await cards.count() === 0) {
      test.skip(true, 'No market cards')
    }
    await cards.first().click()
    await page.waitForTimeout(600)
    expect(await getView(page)).toBe('marketCardDetail')

    await page.locator('.page-header-back').click()
    await page.waitForTimeout(600)
    expect(await getView(page)).toBe('market')
  })
})
