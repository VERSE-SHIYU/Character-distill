// 角色市场截图探针：桌面 / 移动 / 详情（复用 market-verify mock 数据）
// 用法: node e2e/market-shot.cjs
const { openApp, login, pushView, shot } = require('./helpers.cjs')

const TAGS = ['治愈', '古风', '科幻']

const makeCard = (id, name, identity, traits, tags, avatar_data = '') => ({
  id,
  name,
  text_id: 't1',
  card_json: JSON.stringify({
    name,
    identity,
    personality_traits: traits,
    speaking_style: { tone: '安静' },
    values: ['真诚'],
    key_memories: [],
    background: `背景：${name}`,
    relationships: [],
  }),
  user_id: 'u-other',
  avatar_data,
  forked_from: null,
  likes: 12,
  created_at: '2026-08-01T10:00:00',
  market_description: `${name} 的公开卡`,
  market_tags: tags.join(','),
  author_name: '青梧',
  author_avatar: '',
  text_title: 'demo.txt',
  comment_count: 2,
  is_remote: false,
  origin_region: null,
  liked_by_me: false,
})

const CARDS = [
  makeCard('m1', '沈若言', '深夜电台 · 主播', ['温柔', '倾听者', '慢热'], ['治愈']),
  makeCard('m2', '林知夏', '古风 · 侠女', ['洒脱', '重情'], ['古风']),
  makeCard('m3', '顾南乔', '科幻 · 向导', ['冷静', '理性'], ['科幻']),
  makeCard('m4', '苏晚晴', '民国 · 名伶', ['温婉', '坚韧'], ['古风']),
]

async function mockRoutes(page) {
  await page.route('**/api/history/list*', (r) => r.fulfill({ json: { items: [], total: 0, page: 1, page_size: 4 } }))
  await page.route('**/api/market/featured*', (r) => r.fulfill({ json: [] }))
  await page.route('**/api/distill/cards/standalone', (r) => r.fulfill({ json: [] }))
  await page.route('**/api/distill/cards/by-text/t1', (r) => r.fulfill({ json: [] }))
  await page.route('**/api/market/tags*', (r) => r.fulfill({ json: { tags: TAGS } }))
  await page.route('**/api/market/list*', (r) => r.fulfill({ json: { cards: CARDS, total: CARDS.length, page: 1, page_size: 20 } }))
  await page.route('**/api/market/search*', (r) => r.fulfill({ json: { cards: [], total: 0, page: 1, page_size: 20 } }))
  await page.route('**/api/cards/m1/detail', (r) => r.fulfill({ json: { ...CARDS[0], visibility: 'public' } }))
  await page.route('**/api/market/card/m1/book-versions', (r) => r.fulfill({ json: { versions: [] } }))
  await page.route('**/api/market/m1/comments', (r) => r.fulfill({ json: { comments: [] } }))
}

;(async () => {
  // 桌面市场
  {
    const { browser, page } = await openApp({ width: 1280, height: 900 })
    await mockRoutes(page)
    await login(page, { settleMs: 1200 })
    await pushView(page, 'market')
    await page.waitForSelector('.market-card-v2', { timeout: 10000 })
    await page.waitForTimeout(300)
    await shot(page, 'market-desktop.png', 'screenshots')
    await page.locator('.mkt-cat').nth(1).click()
    await page.waitForTimeout(300)
    await shot(page, 'market-desktop-tag.png', 'screenshots')
    // 详情
    await page.locator('.market-card-v2-name').first().click()
    await page.waitForSelector('.market-detail-name', { timeout: 10000 })
    await page.waitForTimeout(300)
    await shot(page, 'market-detail-desktop.png', 'screenshots')
    await browser.close()
  }
  // 移动市场
  {
    const { browser, page } = await openApp({ width: 390, height: 844 })
    await mockRoutes(page)
    await login(page, { settleMs: 1200 })
    await pushView(page, 'market')
    await page.waitForSelector('.market-card-v2', { timeout: 10000 })
    await page.waitForTimeout(300)
    await shot(page, 'market-mobile.png', 'screenshots')
    await browser.close()
  }
  console.log('done → e2e/screenshots/market-*.png')
})().catch((e) => { console.error(e); process.exit(1) })
