import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { withSaveResult } from './withSaveResult'

const SRC = path.join(__dirname, '..')

// 只看产品代码：测试文件里必然出现被断言的字面量（本文件自己就写着两处），
// 把它们算进来等于这条锁锁的是自己的断言。
function walk(dir, out = []) {
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, ent.name)
    if (ent.isDirectory()) walk(p, out)
    else if (/\.(js|jsx)$/.test(ent.name) && !/\.test\.(js|jsx)$/.test(ent.name)) out.push(p)
  }
  return out
}

const rel = (p) => path.relative(SRC, p).split(path.sep).join('/')

describe('withSaveResult', () => {
  it('V1 只有 saved === false 才多出 unsaved: true，其余原样返回同一个引用', () => {
    const msg = { id: 7, content: 'hi' }

    const failed = withSaveResult(msg, false)
    expect(failed).toEqual({ id: 7, content: 'hi', unsaved: true })
    expect(failed).not.toBe(msg)
    expect(msg.unsaved).toBeUndefined()

    // saved: true 与「没这个字段」都是「没问题」—— 拿 falsy 当失败会把 hidden 消息、
    // summary 帧、旧接口全标成「未保存」。
    expect(withSaveResult(msg, true)).toBe(msg)
    expect(withSaveResult(msg, undefined)).toBe(msg)
    expect(withSaveResult(msg, null)).toBe(msg)
  })

  it('V2 四个落点都走这一处判断，且没有第二份 unsaved 判断/文案', () => {
    const store = fs.readFileSync(path.join(SRC, 'store/useAppStore.js'), 'utf8')
    const group = fs.readFileSync(path.join(SRC, 'components/GroupChatPage.jsx'), 'utf8')

    // 一处一对一（非流式 user+char）、一处流式 done、一处撤回提示、一处群聊帧 ——
    // 少数一处，「未保存」就少一处，而用户看不出少的是哪处。
    expect(store).toContain('withSaveResult(')
    expect(group).toContain('withSaveResult(')
    expect(store.match(/withSaveResult\(/g).length).toBeGreaterThanOrEqual(4)
    expect(group.match(/withSaveResult\(/g).length).toBeGreaterThanOrEqual(2)

    const offenders = []
    for (const p of walk(SRC)) {
      const text = fs.readFileSync(p, 'utf8')
      if (rel(p) !== 'utils/withSaveResult.js' && /unsaved\s*:/.test(text)) {
        offenders.push(rel(p))
      }
      if (rel(p) !== 'components/common/UnsavedHint.jsx' && text.includes('未保存，刷新后会丢失')) {
        offenders.push(`${rel(p)}（文案写死在第二处）`)
      }
    }
    expect(offenders).toEqual([])
  })
})
