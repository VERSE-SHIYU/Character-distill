// CSS 死选择器检查（CI 里随 npm test 跑）。
// 1）扫描器自身的单测；2）全仓扫描结果必须恰好等于基线：多出来的 = 新写出的死样式，少了的 = 基线该删的行。
import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { collectUsage, parseRules, deadClasses, findDeadSelectors } from './deadSelectors.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FRONTEND = path.resolve(HERE, '../..')
const BASELINE = path.join(HERE, 'dead-selectors.baseline.json')
const LIB_PREFIXES = JSON.parse(fs.readFileSync(path.join(HERE, 'allowlist.json'), 'utf8')).libPrefixes.map((x) => x.prefix)

const SKIP_DIRS = new Set(['node_modules', 'dist', '__tests__', 'e2e'])
function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name)
    if (e.isDirectory()) return SKIP_DIRS.has(e.name) ? [] : walk(p)
    return [p]
  })
}

function scanRepo() {
  const files = walk(path.join(FRONTEND, 'src')).concat([path.join(FRONTEND, 'index.html')])
  const usage = { words: new Set(), prefixes: new Set() }
  for (const f of files) {
    if (/\.(jsx?|tsx?|mjs|html)$/.test(f) && !/\.(test|spec)\./.test(f)) collectUsage(fs.readFileSync(f, 'utf8'), usage)
  }
  const css = files.filter((f) => f.endsWith('.css'))
    .map((f) => ({ file: path.relative(FRONTEND, f).split(path.sep).join('/'), text: fs.readFileSync(f, 'utf8') }))
  return findDeadSelectors(css, usage, LIB_PREFIXES)
}

const key = (d) => `${d.file} :: ${d.selector}`

// a 里没被 bKeys 配对上的条目：同一 key 在 bKeys 里出现几次就配几次，剩下的原样返回。
// 返回的是 a 的元素本身（不是 key），所以提示行和 key 列表出自同一次配对。
function unmatched(a, bKeys, keyOf) {
  const remain = new Map()
  for (const k of bKeys) remain.set(k, (remain.get(k) || 0) + 1)
  return a.filter((item) => {
    const k = keyOf(item)
    const n = remain.get(k) || 0
    if (n === 0) return true
    remain.set(k, n - 1)
    return false
  })
}

describe('扫描器', () => {
  it('普通字符串和模板 ${} 里的类名都算用到；紧贴 ${} 的前缀算动态前缀', () => {
    const u = collectUsage("const a = 'foo bar'; const b = `x-row${on ? ' is-on' : ''} theme-${t}`")
    expect([...u.words]).toEqual(expect.arrayContaining(['foo', 'bar', 'x-row', 'is-on']))
    expect([...u.prefixes]).toEqual(['theme-'])
  })

  it(':not(...) 和 [...] 里的类不算要求；库前缀豁免', () => {
    const u = { words: new Set(['live']), prefixes: new Set(['theme-']) }
    expect(deadClasses('.live:not(.gone)[data-x=".y"]', u, [])).toEqual([])
    expect(deadClasses('.theme-dark .live', u, [])).toEqual([])
    expect(deadClasses('.wrap .adm-tab-bar', { words: new Set(['wrap']), prefixes: new Set() }, ['adm-'])).toEqual([])
    expect(deadClasses('.gone .live', u, [])).toEqual(['gone'])
  })

  it('@media 里的规则照算，@keyframes 里的帧不算；行号是选择器所在行', () => {
    const rules = parseRules('/* c */\n.a { x: 1 }\n@media (max-width: 1px) {\n  .b, .c { y: 2 }\n}\n@keyframes k { from { o: 0 } to { o: 1 } }\n')
    expect(rules).toEqual([{ line: 2, selectors: ['.a'] }, { line: 4, selectors: ['.b', '.c'] }])
  })
})

describe('CSS 死选择器（基线模式）', () => {
  const found = scanRepo()
  const baseline = JSON.parse(fs.readFileSync(BASELINE, 'utf8')).entries

  it('没有新写出的死样式', () => {
    const added = unmatched(found, baseline, key)
    const detail = added.map((d) => `  ${d.file}:${d.line}  ${d.selector}  （找不到的类：${d.dead.join(' ')}）`)
    expect(added.map(key), [
      '下面这些样式匹配不到任何元素：选择器里的类名在前端代码里找不到。',
      '修法：删掉这条样式，或者把类名用回组件里。不要把它加进基线。',
      ...detail,
    ].join('\n')).toEqual([])
  })

  it('基线里没有过期的行（只减不增）', () => {
    const stale = unmatched(baseline, found.map(key), (k) => k)
    expect(stale, [
      '这些行已经不是死样式了（已删除或重新用上），请从 scripts/css-guard/dead-selectors.baseline.json 删掉：',
      ...stale.map((k) => `  ${k}`),
    ].join('\n')).toEqual([])
  })
})
