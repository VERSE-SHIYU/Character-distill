/**
 * formatRelativeTime 单元测试
 *
 * 固定 now = 2026-06-26 12:00:00 UTC 验证每个时间分支。
 * 无测试框架，Node 直接运行：node src/utils/time.test.mjs
 */
import { formatRelativeTime } from './time.js'

const FIXED_NOW = new Date('2026-06-26T12:00:00Z')

function mockDate(fixedNow) {
  const OrigDate = globalThis.Date
  class MockDate extends OrigDate {
    constructor(...args) {
      if (args.length === 0) super(fixedNow.getTime())
      else super(...args)
    }
    static now() { return fixedNow.getTime() }
  }
  MockDate.UTC = OrigDate.UTC
  MockDate.parse = OrigDate.parse
  globalThis.Date = MockDate
  return () => { globalThis.Date = OrigDate }
}

let passed = 0
let failed = 0

function test(name, fn) {
  const restore = mockDate(FIXED_NOW)
  try {
    fn()
    passed++
    console.log(`  ✅ ${name}`)
  } catch (e) {
    failed++
    console.log(`  ❌ ${name}\n      ${e.message}`)
  } finally {
    restore()
  }
}

function assert(cond, msg) { if (!cond) throw new Error(msg || 'assertion failed') }
function assertMatch(actual, regex, label) {
  if (!regex.test(actual)) throw new Error(`${label}: ${JSON.stringify(actual)} ∉ ${regex}`)
}

/* ── 用例 ── */

test('30秒前 → 刚刚', () => {
  const iso = new Date(FIXED_NOW.getTime() - 30 * 1000).toISOString()
  assert(formatRelativeTime(iso) === '刚刚')
})

test('5分钟前 → "5 分钟前"', () => {
  const iso = new Date(FIXED_NOW.getTime() - 5 * 60 * 1000).toISOString()
  assert(formatRelativeTime(iso) === '5 分钟前')
})

test('3小时前 → "3 小时前"', () => {
  const iso = new Date(FIXED_NOW.getTime() - 3 * 3600 * 1000).toISOString()
  assert(formatRelativeTime(iso) === '3 小时前')
})

test('昨天 → 匹配 /^昨天 \\d{2}:\\d{2}$/', () => {
  const iso = new Date(FIXED_NOW.getTime() - 24 * 3600 * 1000).toISOString()
  assertMatch(formatRelativeTime(iso), /^昨天 \d{2}:\d{2}$/, '昨天')
})

test('3天前 → 匹配星期X HH:MM', () => {
  const iso = new Date(FIXED_NOW.getTime() - 3 * 86400 * 1000).toISOString()
  assertMatch(formatRelativeTime(iso), /^星期[日一二三四五六] \d{2}:\d{2}$/, '3天前')
})

test('10天前（今年，超一周★核心★）→ 带时分', () => {
  const iso = new Date(FIXED_NOW.getTime() - 10 * 86400 * 1000).toISOString()
  const r = formatRelativeTime(iso)
  assertMatch(r, /月.*日 \d{2}:\d{2}$/, '10天前')
  assert(r.includes(':'), '必须包含时分')
})

test('去年（★核心★）→ 带时分', () => {
  const iso = new Date('2025-03-15T08:30:00Z').toISOString()
  const r = formatRelativeTime(iso)
  assertMatch(r, /^\d{4}年.*日 \d{2}:\d{2}$/, '去年')
  assert(r.includes(':'), '必须包含时分')
})

test('空字符串 → 返回空', () => assert(formatRelativeTime('') === ''))
test('null → 返回空', () => assert(formatRelativeTime(null) === ''))
test('无效日期 → 返回空', () => assert(formatRelativeTime('invalid') === ''))

test('无时区标记 "2026-05-26 08:00:46" → 正确解析为UTC并带时分', () => {
  const r = formatRelativeTime('2026-05-26 08:00:46')
  assertMatch(r, /月.*日 \d{2}:\d{2}$/, '无时区标记')
  assert(r.includes(':'), '必须带时分')
})

/* ── 汇总 ── */
console.log(`\n${'='.repeat(36)}`)
console.log(`总计: ${passed + failed}  |  通过: ${passed}  |  失败: ${failed}`)
process.exit(failed > 0 ? 1 : 0)
