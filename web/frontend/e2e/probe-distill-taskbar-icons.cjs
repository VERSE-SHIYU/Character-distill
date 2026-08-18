// 一次性探针：验证 DistillTaskBar 四状态图标已从 emoji 换成 Icon SVG（切片⑨）
// 断言：done→Check / error→Close / queued→Clock / running→Zap；无 emoji 残留；无 pageErrors
const { openApp, login } = require('./helpers.cjs')

async function main() {
  const fail = []
  const { browser, page, errors } = await openApp({ width: 1280, height: 900 })
  await login(page, { settleMs: 1200 })

  const tasks = [
    { id: 't-done', status: 'done', character: '沈若言', progress_pct: 100 },
    { id: 't-err', status: 'error', character: '林知夏', message: '网络超时' },
    { id: 't-q', status: 'queued', character: '顾之遥', progress_pct: 0 },
    { id: 't-run', status: 'running', character: '云疏影', progress_pct: 40 },
  ]
  await page.evaluate((ts) => {
    window.__appStore.setState({ distillTasks: ts })
  }, tasks)

  await page.waitForSelector('.distill-fab', { timeout: 10000 })
  await page.locator('.distill-fab').click()
  await page.waitForSelector('.distill-task-item', { timeout: 10000 })
  await page.waitForTimeout(300)

  const icons = await page.evaluate(() => {
    const items = [...document.querySelectorAll('.distill-task-item')]
    return {
      count: items.length,
      // 每个任务图标应渲染一个 <svg>，无 emoji 文本
      svgPerItem: items.map((el) => el.querySelector('.distill-task-icon svg') ? 1 : 0),
      textHasEmoji: [...document.querySelectorAll('.distill-task-icon')].some((el) => /✅|❌|⏳|⚙/.test(el.textContent)),
    }
  })
  console.log('ICONS', JSON.stringify(icons))
  if (icons.count !== 4) fail.push('应渲染 4 个任务项: ' + icons.count)
  if (icons.svgPerItem.join('') !== '1111') fail.push('每个任务图标都应是 SVG: ' + icons.svgPerItem.join(''))
  if (icons.textHasEmoji) fail.push('状态图标仍有 emoji 残留')

  // 展开面板里应可见正确图标语义：done 项有 Check、error 项有 Close、queued 有 Clock、running 有 Zap
  const glyphs = await page.evaluate(() => {
    const item = (i) => document.querySelectorAll('.distill-task-item')[i]
    const paths = (el) => el ? el.querySelector('.distill-task-icon svg').querySelectorAll('path, polyline, circle, rect, line, polygon').length : 0
    return {
      done: paths(item(0)), error: paths(item(1)), queued: paths(item(2)), running: paths(item(3)),
    }
  })
  console.log('GLYPHS', JSON.stringify(glyphs))
  if (glyphs.done !== 1) fail.push('done 应渲染 Check(polyline): ' + glyphs.done)
  if (glyphs.error !== 2) fail.push('error 应渲染 Close(2 line): ' + glyphs.error)
  if (glyphs.queued !== 2) fail.push('queued 应渲染 Clock(circle+polyline): ' + glyphs.queued)
  if (glyphs.running !== 1) fail.push('running 应渲染 Zap(polygon): ' + glyphs.running)

  console.log('pageErrors:', JSON.stringify(errors))
  if (errors.length) fail.push('pageErrors: ' + JSON.stringify(errors))
  await browser.close()

  if (fail.length) {
    console.log('\nFAILURES:'); fail.forEach((f) => console.log('  ✗ ' + f))
    process.exit(1)
  }
  console.log('\nALL PASS ✓')
}
main().catch((e) => { console.error(e); process.exit(1) })
