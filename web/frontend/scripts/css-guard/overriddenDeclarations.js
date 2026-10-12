// 白写的声明：同一份 CSS 里，同一个 @media/@supports 条件下，一条规则里的某个属性，
// 对这条规则的「每一个」选择器，后面都有另一条规则用同一选择器、同一属性又写了一遍
// （优先级相同、后写的赢）→ 这处声明永远不生效。
// 不算的：同一条规则里写两遍（浏览器兼容的回退写法）；只被部分选择器覆盖（对其余选择器仍生效）；
// !important 与普通声明不互相覆盖。

function lineIndex(text) {
  const starts = [0]
  for (let i = 0; i < text.length; i++) if (text[i] === '\n') starts.push(i + 1)
  return (offset) => {
    let lo = 0
    let hi = starts.length - 1
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (starts[mid] <= offset) lo = mid
      else hi = mid - 1
    }
    return lo + 1
  }
}

function parse(css) {
  const clean = css.replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, ' '))
  const rules = []
  const stack = []
  let start = 0
  for (let i = 0; i < clean.length; i++) {
    const ch = clean[i]
    if (ch === '{') {
      stack.push({ prelude: clean.slice(start, i), preludeStart: start, body: i + 1 })
      start = i + 1
    } else if (ch === '}') {
      const top = stack.pop()
      start = i + 1
      if (!top) continue
      const prelude = top.prelude.trim()
      if (prelude.startsWith('@') || stack.some((s) => s.prelude.includes('keyframes'))) continue
      const body = clean.slice(top.body, i)
      if (body.includes('{')) continue
      const decls = []
      let offset = top.body
      for (const raw of body.split(';')) {
        const colon = raw.indexOf(':')
        if (colon > 0) {
          const lead = raw.length - raw.trimStart().length
          decls.push({
            property: raw.slice(0, colon).trim().toLowerCase(),
            important: /!\s*important/i.test(raw),
            start: offset + lead,
            end: offset + raw.length + 1,
          })
        }
        offset += raw.length + 1
      }
      rules.push({
        context: stack.map((s) => s.prelude.trim().replace(/\s+/g, ' ')).join(' | '),
        selectors: prelude.split(',').map((s) => s.trim().replace(/\s+/g, ' ')).filter(Boolean),
        start: top.preludeStart + (top.prelude.length - top.prelude.trimStart().length),
        end: i + 1,
        decls,
      })
    }
  }
  return rules
}

/** [{ selectors, property, line, start, end, ruleStart, ruleEnd }]：白写的声明（偏移量供改写脚本用）。 */
export function findOverriddenDeclarations(css) {
  const rules = parse(css)
  const lineOf = lineIndex(css)
  // 每个 (上下文, 选择器, 属性, important) 最后一次出现在哪条规则
  const last = new Map()
  rules.forEach((r, idx) => r.decls.forEach((d) => r.selectors.forEach((s) => {
    last.set(`${r.context}§${s}§${d.property}§${d.important}`, idx)
  })))
  const out = []
  rules.forEach((r, idx) => r.decls.forEach((d) => {
    const overridden = r.selectors.every((s) => last.get(`${r.context}§${s}§${d.property}§${d.important}`) > idx)
    if (overridden) {
      out.push({ selectors: r.selectors, property: d.property, line: lineOf(d.start), start: d.start, end: d.end, ruleStart: r.start, ruleEnd: r.end })
    }
  }))
  return out
}
