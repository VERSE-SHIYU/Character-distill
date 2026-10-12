// CSS 死选择器检查：CSS 里引用的类名，在前端源码里已经找不到 → 这条选择器匹配不到任何元素。
// 纯函数，不读文件；读文件和比对基线在 deadSelectors.test.js。
//
// 「用到的类名」= 源码里所有字符串 / 模板字面量中出现的词（模板的 ${...} 里递归再扫一遍）。
// 模板里紧贴 ${} 的静态片段若以 - 或 _ 结尾（`theme-${x}`），记为动态前缀，以它开头的类都算用到。
// 一条复合选择器「死」= 它要求的类里有一个没用到（:not/:is/:where/:has(...) 和 [...] 里的类不算要求）。

const STRING_RE = /'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"|`(?:\\.|[^`\\])*`/gs
const WORD_RE = /-?[A-Za-z_][\w-]*/g
const INTERP_RE = /\$\{((?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*)\}/g
const CLASS_RE = /\.(-?[A-Za-z_][\w-]*)/g

/** 从一份源码文本里收集用到的词和动态前缀，写进 acc（{ words: Set, prefixes: Set }）。 */
export function collectUsage(text, acc = { words: new Set(), prefixes: new Set() }) {
  for (const m of text.matchAll(STRING_RE)) {
    const quote = m[0][0]
    let body = m[0].slice(1, -1)
    if (quote === '`') {
      const inner = [...body.matchAll(INTERP_RE)].map((x) => x[1])
      const parts = body.split(INTERP_RE).filter((_, i) => i % 2 === 0)
      parts.forEach((part, i) => {
        const toks = part.split(/\s+/).filter(Boolean)
        const glued = i < parts.length - 1 && part && !/\s$/.test(part) && toks.length
        if (glued && /[-_]$/.test(toks.at(-1)) && toks.at(-1).length >= 2) acc.prefixes.add(toks.at(-1))
      })
      inner.forEach((code) => collectUsage(code, acc))
      body = parts.join(' ')
    }
    for (const w of body.matchAll(WORD_RE)) acc.words.add(w[0])
  }
  return acc
}

/** 一份 CSS 文本里的所有普通规则：{ line, selectors: string[] }。@keyframes 里的帧不算。 */
export function parseRules(css) {
  const clean = css.replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, ' '))
  const lineStarts = [0]
  for (let i = 0; i < clean.length; i++) if (clean[i] === '\n') lineStarts.push(i + 1)
  const lineOf = (offset) => {
    let lo = 0
    let hi = lineStarts.length - 1
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (lineStarts[mid] <= offset) lo = mid
      else hi = mid - 1
    }
    return lo + 1
  }
  const rules = []
  const stack = []
  let start = 0
  for (let i = 0; i < clean.length; i++) {
    const ch = clean[i]
    if (ch === '{') {
      const raw = clean.slice(start, i)
      const prelude = raw.trim()
      const inKeyframes = stack.some((s) => s.startsWith('@') && s.includes('keyframes'))
      if (prelude && !prelude.startsWith('@') && !inKeyframes) {
        const line = lineOf(start + (raw.length - raw.trimStart().length))
        rules.push({ line, selectors: prelude.split(',').map((s) => s.trim()).filter(Boolean) })
      }
      stack.push(prelude)
      start = i + 1
    } else if (ch === '}') {
      stack.pop()
      start = i + 1
    } else if (ch === ';') {
      start = i + 1
    }
  }
  return rules
}

function stripNonRequired(selector) {
  let s = selector.replace(/\[[^\]]*\]/g, '')
  for (;;) {
    const next = s.replace(/:(not|where|is|has)\((?:[^()]|\([^()]*\))*\)/g, '')
    if (next === s) return s
    s = next
  }
}

/** 这条选择器要求、但源码里没用到的类。 */
export function deadClasses(selector, usage, libPrefixes) {
  const required = [...stripNonRequired(selector).matchAll(CLASS_RE)].map((m) => m[1])
  return [...new Set(required)].filter((c) =>
    !usage.words.has(c)
    && ![...usage.prefixes].some((p) => c.startsWith(p))
    && !libPrefixes.some((p) => c.startsWith(p)))
}

/** 所有死选择器：[{ file, line, selector, dead: string[] }]。cssFiles: [{ file, text }]。 */
export function findDeadSelectors(cssFiles, usage, libPrefixes) {
  const out = []
  for (const { file, text } of cssFiles) {
    for (const rule of parseRules(text)) {
      for (const selector of rule.selectors) {
        const dead = deadClasses(selector, usage, libPrefixes)
        if (dead.length) out.push({ file, line: rule.line, selector: selector.replace(/\s+/g, ' '), dead })
      }
    }
  }
  return out
}
