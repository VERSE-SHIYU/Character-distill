// Self-test for formatChatTime — timestamps in system local timezone
import { formatChatTime } from './src/utils/time.js'

const OriginalDate = globalThis.Date
const TZ_OFFSET = -new OriginalDate().getTimezoneOffset() // minutes east of UTC
const TZ_SIGN = TZ_OFFSET >= 0 ? '+' : '-'
const TZ_HH = String(Math.floor(Math.abs(TZ_OFFSET) / 60)).padStart(2, '0')
const TZ_MM = String(Math.abs(TZ_OFFSET) % 60).padStart(2, '0')
const TZ = `${TZ_SIGN}${TZ_HH}:${TZ_MM}`

console.log(`System timezone: UTC${TZ}`)

function freezeNow(localISO) {
  const frozen = new OriginalDate(localISO)
  globalThis.Date = class extends OriginalDate {
    constructor(...args) {
      if (args.length === 0) return new OriginalDate(frozen.getTime())
      return new OriginalDate(...args)
    }
  }
  Object.setPrototypeOf(Date, OriginalDate)
  Date.now = () => frozen.getTime()
  Date.parse = OriginalDate.parse
  Date.UTC = OriginalDate.UTC
}

function restoreDate() {
  globalThis.Date = OriginalDate
}

const PASS = '\x1b[32mPASS\x1b[0m'
const FAIL = '\x1b[31mFAIL\x1b[0m'
let passed = 0, failed = 0

function test(label, nowLocal, msgLocal, expected) {
  const nowIso = nowLocal + TZ
  const msgIso = msgLocal + TZ

  freezeNow(nowIso)
  const actual = formatChatTime(msgIso)
  restoreDate()

  const ok = actual === expected
  console.log(`${ok ? PASS : FAIL} ${label}`)
  if (!ok) {
    console.log(`  expected: "${expected}"`)
    console.log(`    actual: "${actual}"`)
  }
  ok ? passed++ : failed++
}

console.log('=== formatChatTime boundary tests ===\n')

// 1. Today 14:30
test('Today 14:30',
  '2025-06-15T20:00:00', '2025-06-15T14:30:00',
  '14:30')

// 2. Cross-midnight: now=Tue 00:30, msg=Mon 23:50
test('Cross-midnight: now Tue 00:30, msg Mon 23:50 => Yesterday',
  '2025-06-17T00:30:00', '2025-06-16T23:50:00',
  '昨天 23:50')

// 3. Yesterday 09:05 (zero-padded minute)
test('Yesterday 09:05',
  '2025-06-15T12:00:00', '2025-06-14T09:05:00',
  '昨天 09:05')

// 4. Day-before-yesterday 20:51
test('Day before yesterday 20:51',
  '2025-06-15T22:00:00', '2025-06-13T20:51:00',
  '前天 20:51')

// 5. 4 days ago (Monday)
test('4 days ago => Monday',
  '2025-06-13T12:00:00', '2025-06-09T10:30:00',
  '星期一 10:30')

// 6. 6 days ago — still weekday tier
test('6 days ago => still weekday',
  '2025-06-13T12:00:00', '2025-06-07T15:45:00',
  '星期六 15:45')

// 7. 7 days exactly — crosses to M月D日 (NOT weekday)
test('7 days exactly => M月D日 (not weekday)',
  '2025-03-09T12:00:00', '2025-03-02T14:30:00',
  '3月2日 14:30')

// 8. 8 days ago, same year
test('8 days ago, same year => M月D日',
  '2025-03-10T12:00:00', '2025-03-02T14:30:00',
  '3月2日 14:30')

// 9. Last year
test('Last year Jun 9',
  '2025-06-15T12:00:00', '2024-06-09T06:52:00',
  '2024年6月9日 06:52')

// 10. Hour zero-padded
test('Hour zero-padded 06:52',
  '2025-06-15T12:00:00', '2025-06-14T06:52:00',
  '昨天 06:52')

// 11. Months ago, same year => M月D日
test('Months ago, same year => M月D日',
  '2025-06-15T12:00:00', '2025-01-03T08:15:00',
  '1月3日 08:15')

console.log(`\n${passed} passed, ${failed} failed / ${passed + failed} tests`)
process.exit(failed > 0 ? 1 : 0)
