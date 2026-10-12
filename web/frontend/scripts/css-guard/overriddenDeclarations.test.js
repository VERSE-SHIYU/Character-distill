// 白写的声明检查（CI 里随 npm test 跑）：前端所有 CSS 里不许有被后面同选择器同属性整条覆盖掉的声明。
import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { findOverriddenDeclarations } from './overriddenDeclarations.js'

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../src')
const cssFiles = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
  const p = path.join(dir, e.name)
  if (e.isDirectory()) return cssFiles(p)
  return p.endsWith('.css') ? [p] : []
})

describe('白写的声明', () => {
  it('后面同选择器同属性会覆盖前面；同一条规则里的回退写法不算', () => {
    const css = '.a { color: red; margin: 0 }\n.a { color: blue }\n.b { display: -webkit-box; display: flex }'
    expect(findOverriddenDeclarations(css).map((x) => [x.selectors, x.property, x.line])).toEqual([[['.a'], 'color', 1]])
  })

  it('只被部分选择器覆盖、!important、不同 @media 条件都不算', () => {
    const css = [
      '.a, .b { color: red }', '.a { color: blue }',
      '.c { color: red !important }', '.c { color: blue }',
      '.d { color: red }', '@media (max-width: 1px) { .d { color: blue } }',
    ].join('\n')
    expect(findOverriddenDeclarations(css)).toEqual([])
  })

  it('前端 CSS 里没有白写的声明', () => {
    const found = cssFiles(SRC).flatMap((f) => findOverriddenDeclarations(fs.readFileSync(f, 'utf8'))
      .map((x) => `  ${path.relative(SRC, f).split(path.sep).join('/')}:${x.line}  ${x.selectors.join(', ')} { ${x.property} }`))
    expect(found, ['这些声明会被后面同一选择器、同一属性的声明覆盖，永远不生效。删掉前面这处，或合并到后面那条规则：', ...found].join('\n')).toEqual([])
  })
})
