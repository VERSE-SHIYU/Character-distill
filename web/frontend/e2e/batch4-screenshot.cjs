// Batch4 页面截图（testadmin）：作者 / 个人资料 / 信息流 / 后台管理
// 输出 e2e/screenshots/b4-{view}.png（桌面 1280 + 移动抽查）
// 说明：feed 无真实数据时 route mock 示例动态，展示日期分隔线 + 卡片视觉
const { openApp, login, goToView, shot } = require('./helpers.cjs')

const OUT = 'b4'
const VIEWS = ['author', 'profile', 'feed', 'admin']
const DEMO_USER = 'u-demo-author'

function iso(offsetDays, hourOffset = 0) {
  const d = new Date(Date.now() - offsetDays * 86400000 - hourOffset * 3600000)
  return d.toISOString()
}

const mockPosts = [
  { id: 901, content: '剧光版视觉落地完成：沉浸式对话页、市场卡片、创作中心全部切换新设计语言。\n主题色保留六套：Aurora / Milktea / Ocean / Sakura / Midnight / Galaxy。', created_at: iso(0, 1), user_id: 'ta', author_name: 'testadmin', likes: 18, liked_by_me: true, comment_count: 4, location: '新加坡' },
  { id: 902, content: '信息流页的日期分隔线换成了「两侧细线 + 中央日期」的样式，扫读更清爽。', created_at: iso(0, 5), user_id: 'ta', author_name: 'testadmin', likes: 6, liked_by_me: false, comment_count: 1 },
  { id: 903, content: '昨天把个人资料页的网格徽章统一成了危险色 / 成功色 token。', created_at: iso(1, 2), user_id: 'ta', author_name: 'testadmin', likes: 3, liked_by_me: false, comment_count: 0 },
  { id: 904, content: '这个角色卡来自上周的蒸馏任务，欢迎大家去市场看看。', created_at: iso(3, 6), user_id: 'ta', author_name: 'testadmin', likes: 22, liked_by_me: false, comment_count: 7, card_id: 42, card_name: '剧光·示例角色', card_json: JSON.stringify({ identity: '一个来自蒸馏任务的角色' }), card_updated_at: iso(0, 1) },
]

const mockAuthor = {
  author: { id: DEMO_USER, username: 'demo', name: '剧光·示例作者', avatar_data: null },
  cards: [
    { id: 501, name: '星野遥', likes: 128, avatar_data: null, card_json: JSON.stringify({ name: '星野遥', identity: '天才程序员的虚拟助手', background: '生于东京的一个雨夜，喜欢在代码注释里写俳句。擅长帮你理清思路，也偶尔毒舌。' }) },
    { id: 502, name: '林晚秋', likes: 96, avatar_data: null, card_json: JSON.stringify({ name: '林晚秋', identity: '古籍修复师', background: '在旧书店工作，安静而敏锐，总能用一句话点破你纠结很久的事。' }) },
  ],
  texts: [
    { id: 701, title: '角色蒸馏方法论', description: '从文本到角色的七步蒸馏法', char_count: 23456 },
    { id: 702, title: '沉浸式对话设计笔记', description: '舞台感布局与交互设计实践', char_count: 18765 },
  ],
  is_following: false,
  followers_count: 1284,
  following_count: 42,
  stats_visible: true,
  online: true,
  last_active_at: null,
  presence_hidden: false,
}

;(async () => {
  const { browser, page, errors: pageErrors } = await openApp({ width: 1280, height: 900 })
  // feed 无数据时注入示例动态；作者页注入富数据的示例用户（testadmin 主页会重定向到 MinePage）
  await page.route('**/api/market/feed*', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ posts: mockPosts }) }),
  )
  await page.route(`**/api/market/author/${DEMO_USER}`, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mockAuthor) }),
  )
  await page.route(`**/api/market/author/${DEMO_USER}/posts`, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ posts: mockPosts }) }),
  )
  await login(page, { settleMs: 1400 })

  const results = {}
  for (const view of VIEWS) {
    // 值经参数序列化传进页面（不要写 '${...}' 单引号包模板，Node 侧不插值）
    await goToView(page, view, { viaHome: true, authorUserId: view === 'author' ? DEMO_USER : undefined })
    await page.waitForTimeout(1800)
    // 作者页数据异步加载，等真实内容就绪再截图，避免抓到 loading/占位
    // （.mine-profile-name 加载态就有占位文本，必须等卡片数据出现才算就绪）
    if (view === 'author') {
      await page.waitForFunction(() => {
        const name = document.querySelector('.mine-profile-name')?.textContent?.trim() || ''
        const cards = document.querySelectorAll('.market-card-v2').length
        return name.length > 0 && cards > 0
      }, { timeout: 6000 }).catch(() => {})
    }
    await shot(page, `b4-${view}.png`, OUT)
    const info = await page.evaluate(() => ({
      title: document.querySelector('.page-header-title, .panel-title, .mine-title, .admin-card-title')?.textContent?.trim() || '',
      cards: document.querySelectorAll('.admin-card, .post-card, .market-card-v2, .mine-book-cover-card, .profile-grid').length,
      dateDividers: document.querySelectorAll('.feed-date-divider').length,
    }))
    results[view] = info
  }

  // 移动端抽查：信息流
  await page.setViewportSize({ width: 390, height: 844 })
  await page.evaluate(() => { window.__appStore.getState().setView('home') })
  await page.waitForTimeout(500)
  await page.evaluate(() => { window.__appStore.getState().pushView('feed') })
  await page.waitForTimeout(1400)
  await shot(page, 'b4-feed-mobile.png', OUT)

  console.log(JSON.stringify({ results, pageErrors }, null, 2))
  await browser.close()
})().catch(e => { console.error('FATAL', e); process.exit(1) })
