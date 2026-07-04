import { test, expect } from '@playwright/test'

const BASE = 'http://localhost:7860'

test.describe('Navigation migration: setView → navigateTo/navigateBack', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(BASE)
    // login
    await page.fill('#login-username', 'testadmin')
    await page.fill('#login-password', 'test1234')
    await page.click('.login-submit')
    // wait for post-login render (tabbar appears)
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

  /** Small pause to let view transitions & lazy loading settle */
  async function settle(page, ms = 600) {
    await page.waitForTimeout(ms)
  }

  /** Click a TabBar tab by its visible text (mobile) */
  async function tab(page, label) {
    await page.locator('.mobile-tabbar').getByText(label, { exact: true }).click()
    await settle(page)
  }

  // ─── Chain 1: 我的 → 消息 → 返回 → 我的 ───
  test('Chain 1: mine → messages → back → mine', async ({ page }) => {
    await tab(page, '我的')
    await expect(page.locator('.mine-page-v2')).toBeVisible()

    // click "消息" quick-entry → pushView('messages')
    await page.locator('.entry-grid-item').filter({ hasText: '消息' }).click()
    await settle(page)
    await expect(page.locator('.messages-page')).toBeVisible()

    // click back button in messages page → navigateBack
    await page.locator('.chat-back-btn').click()
    await settle(page)
    await expect(page.locator('.mine-page-v2')).toBeVisible()
  })

  // ─── Chain 2: 我的 → 市场 → 角色详情 → 返回 → 市场 → 返回 → 我的 ───
  test('Chain 2: mine → market → card detail → back → market → tab(mine) → mine', async ({ page }) => {
    await tab(page, '我的')
    await expect(page.locator('.mine-page-v2')).toBeVisible()

    // click "市场" quick-entry → pushView('market')
    await page.locator('.entry-grid-item').filter({ hasText: '市场' }).click()
    await settle(page)
    await expect(page.locator('.market-toolbar')).toBeVisible()

    // click first market card if available
    const cards = page.locator('.market-card-v2')
    const cardCount = await cards.count()
    test.skip(cardCount === 0, 'No market cards available')

    await cards.first().click()
    await settle(page)
    await expect(page.locator('.market-detail-page')).toBeVisible()

    // back → market (marketCardDetail has chat-back-btn)
    await page.locator('.chat-back-btn').click()
    await settle(page)
    await expect(page.locator('.market-toolbar')).toBeVisible()

    // market is NOT a secondary view — no MobileBackBar.
    // Navigate back to mine via TabBar
    await tab(page, '我的')
    await expect(page.locator('.mine-page-v2')).toBeVisible()
  })

  // ─── Chain 3: 我的 → 历史 → 点进会话 → 返回 → 我的 ───
  test('Chain 3: mine → history → resume session → back → mine', async ({ page }) => {
    // start from mine
    await tab(page, '我的')
    await settle(page)

    // click "历史" tab → pushView('history')
    const historyTab = page.locator('.mine-tab-bar .mine-tab').filter({ hasText: '历史' })
    await historyTab.click()
    await settle(page)
    // HistoryPanel renders with .history-panel
    await expect(page.locator('.history-panel')).toBeVisible()

    // click first session item to open detail
    const sessions = page.locator('.history-item')
    const sessionCount = await sessions.count()
    test.skip(sessionCount === 0, 'No history sessions available')

    await sessions.first().click()
    await settle(page)

    // click "继续对话" button in detail view → resumeSession
    const continueBtn = page.locator('button:has-text("继续对话")')
    if (await continueBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await continueBtn.click()
      await settle(page)
    } else {
      // directly click session item which might start the chat
      test.skip(true, 'No continue button in session detail')
    }

    // should be in chat — click back from chat topbar
    const chatBack = page.locator('.chat-topbar-back')
    await expect(chatBack).toBeVisible({ timeout: 10000 })
    await chatBack.click()
    await settle(page)

    // should be back on mine page (history tab → back lands on mine)
    await expect(page.locator('.mine-page-v2')).toBeVisible()
  })

  // ─── Chain 4: 我的 → 设置 → 个人资料 → 返回 → 设置 → 返回 → 我的 ───
  test('Chain 4: mine → settings → profile → back → settings → back → mine', async ({ page }) => {
    await tab(page, '我的')
    await expect(page.locator('.mine-page-v2')).toBeVisible()

    // click "设置" quick-entry → pushView('settings')
    await page.locator('.entry-grid-item').filter({ hasText: '设置' }).click()
    await settle(page)
    await expect(page.locator('.settings-panel')).toBeVisible()

    // click "个人资料" — use {force:true} because settings-panel may overlay
    await page.locator('.entry-list-item').filter({ hasText: '个人资料' }).click({ force: true })
    await settle(page)
    await expect(page.locator('.profile-page')).toBeVisible()

    // back → settings (profile is SECONDARY_VIEW → has MobileBackBar)
    await page.locator('.mobile-backbar-btn').click()
    await settle(page)
    await expect(page.locator('.settings-panel')).toBeVisible()

    // back → mine
    await page.locator('.mobile-backbar-btn').click()
    await settle(page)
    await expect(page.locator('.mine-page-v2')).toBeVisible()
  })

  // ─── Chain 5: 首页 → 动态 → 返回 → 首页 ───
  test('Chain 5: home → feed → back → home', async ({ page }) => {
    await expect(page.locator('.home-page')).toBeVisible()

    // click "动态" segment button → pushView('feed')
    await page.locator('.home-segment-btn').filter({ hasText: '动态' }).click()
    await settle(page)
    await expect(page.locator('.feed-page')).toBeVisible()

    // feed is NOT a secondary view — no MobileBackBar.
    // click "首页" tab in TabBar to navigate back
    await tab(page, '首页')
    await settle(page)
    await expect(page.locator('.home-page')).toBeVisible()
  })

  // ─── Chain 6: 首页 → 角色卡 → 聊天 → 返回 → 首页 ───
  test('Chain 6: home → recent session → chat → back → home', async ({ page }) => {
    await expect(page.locator('.home-page')).toBeVisible()

    // try clicking a "最近对话" session to enter chat
    const recentItems = page.locator('.home-recent-item')
    const recentCount = await recentItems.count()

    test.skip(recentCount === 0, 'No recent sessions available')

    await recentItems.first().click()
    await settle(page)

    // should now be in chat — wait for chat-topbar-back
    const chatBack = page.locator('.chat-topbar-back')
    await expect(chatBack).toBeVisible({ timeout: 15000 })
    await chatBack.click()
    await settle(page)

    // should be back on home
    await expect(page.locator('.home-page')).toBeVisible()
  })

  // ─── Chain 7: 创作 → 角色管理 → 聊天 → 返回 → 角色管理 ───
  test('Chain 7: creation → character → chat → back → creation', async ({ page }) => {
    await tab(page, '创作')
    await settle(page)
    await expect(page.locator('.creation-panel')).toBeVisible()

    // find a character card with "⋯" menu
    const menuBtns = page.locator('.creation-char-menu-btn')
    const mc = await menuBtns.count()
    test.skip(mc === 0, 'No characters in creation page')

    // open first menu
    await menuBtns.first().click()
    await settle(page)

    // click "聊天" dropdown item
    const chatItem = page.locator('.creation-char-dropdown button:has-text("聊天")')
    await expect(chatItem).toBeVisible()
    await chatItem.click()
    await settle(page)

    // should be in chat — back
    const chatBack = page.locator('.chat-topbar-back')
    await expect(chatBack).toBeVisible({ timeout: 10000 })
    await chatBack.click()
    await settle(page)

    // should be back on creation page
    await expect(page.locator('.creation-panel')).toBeVisible()
  })

  // ─── Chain 8: 群聊列表 → 进群 → 返回 → 群聊列表（TabBar 恢复显示）───
  test('Chain 8: group chat list → enter group → back → list (tabbar visible)', async ({ page }) => {
    await tab(page, '群聊')
    await settle(page)
    await expect(page.locator('.group-chat-page')).toBeVisible()

    // check for group entries — look for .messages-sidebar-item or anything clickable
    const sidebarItems = page.locator('.messages-sidebar-item')
    const si = await sidebarItems.count()

    test.skip(si === 0, 'No group chats available')

    // enter first group
    await sidebarItems.first().click()
    await settle(page)
    // group chat entered — tabbar should be hidden (inConversation or currentGroup)
    await expect(page.locator('.private-chat-header')).toBeVisible()
    await expect(page.locator('.mobile-tabbar')).not.toBeVisible()

    // back to list — group chat has its own back button
    await page.locator('.chat-back-btn').click()
    await settle(page)
    await expect(page.locator('.mobile-tabbar')).toBeVisible()
    await expect(page.locator('.group-chat-page')).toBeVisible()
  })

  // ─── Chain 9: Tab 连切 首页→创作→我的 后按返回：不得穿越 ───
  test('Chain 9: tab switch home→text→mine then back: must not cross tabs', async ({ page }) => {
    await expect(page.locator('.home-page')).toBeVisible()

    // switch to 创作
    await tab(page, '创作')
    await expect(page.locator('.creation-panel')).toBeVisible()

    // switch to 我的 (setView clears history)
    await tab(page, '我的')
    await expect(page.locator('.mine-page-v2')).toBeVisible()

    // mine is a tab-level view — no MobileBackBar, so user cannot "go back".
    // This is correct: the stack is empty, back is impossible.
    // Instead verify that navigating into a sub-view and back returns to mine.
    await page.locator('.entry-grid-item').filter({ hasText: '设置' }).click({ force: true })
    await settle(page)
    await expect(page.locator('.settings-panel')).toBeVisible()

    // back → should go to mine, NOT to creation
    await page.locator('.mobile-backbar-btn').click()
    await settle(page)
    await expect(page.locator('.mine-page-v2')).toBeVisible()
  })

  // ─── Chain 10: 私信会话内 → 返回 → 会话列表 → 返回 → 我的 ───
  test('Chain 10: messages → conversation → back → list → back → mine', async ({ page }) => {
    await tab(page, '我的')
    await expect(page.locator('.mine-page-v2')).toBeVisible()

    // click "私信" tab in mine tab-bar → pushView('messages')
    const msgTab = page.locator('.mine-tab-bar .mine-tab').filter({ hasText: '私信' })
    await msgTab.click({ force: true })  // may be overlapped by mine-tab-content
    await settle(page)
    await expect(page.locator('.messages-page')).toBeVisible()

    // find existing conversations
    const convItems = page.locator('.messages-page [class*="conversation"], .messages-page .messages-sidebar-item')
    const ci = await convItems.count()

    test.skip(ci === 0, 'No private message conversations available')

    // enter conversation
    await convItems.first().click()
    await settle(page)
    await expect(page.locator('.private-chat')).toBeVisible()

    // back → conversation list
    const backFromConv = page.locator('.private-chat-header .chat-back-btn, .messages-page .chat-back-btn').first()
    await backFromConv.click()
    await settle(page)
    await expect(page.locator('.messages-page')).toBeVisible()

    // back → mine
    // messages is NOT a secondary view — no MobileBackBar. Click TabBar "我的"
    await tab(page, '我的')
    await settle(page)
    await expect(page.locator('.mine-page-v2')).toBeVisible()
  })

  // ─── Desktop: sidebar → market → detail → back → market ───
  test('Desktop: sidebar market → detail → back', async ({ page }) => {
    // set desktop viewport & re-login
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.goto(BASE)
    await page.fill('#login-username', 'testadmin')
    await page.fill('#login-password', 'test1234')
    await page.click('.login-submit')
    await page.waitForSelector('.sidebar', { timeout: 15000 })
    await settle(page)

    // click "市场" in sidebar
    await page.locator('.sidebar').getByText('市场').click()
    await settle(page)
    await expect(page.locator('.market-toolbar')).toBeVisible()

    // click first card
    const cards = page.locator('.market-card-v2')
    if (await cards.count() === 0) {
      test.skip(true, 'No market cards')
    }
    await cards.first().click()
    await settle(page)
    await expect(page.locator('.market-detail-page')).toBeVisible()

    // back
    await page.locator('.chat-back-btn').click()
    await settle(page)
    await expect(page.locator('.market-toolbar')).toBeVisible()
  })
})
