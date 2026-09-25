import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { applyFlushReport, pendingSaveKeys, withSaveResult } from './withSaveResult'

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
  it('V1 有 save 才多出 saveState/saveKey，没有则原样返回同一个引用', () => {
    const msg = { id: 7, content: 'hi' }

    const pending = withSaveResult(msg, { state: 'pending', key: 'k1' })
    expect(pending).toEqual({ id: 7, content: 'hi', saveState: 'pending', saveKey: 'k1' })
    expect(pending).not.toBe(msg)
    expect(msg.saveState).toBeUndefined()

    // 落库了就不带 save —— 判「有没有」而不是「真不真」：hidden 用户消息、摘要帧、
    // 旧接口都不会带它，拿 falsy 当失败会让用户看到满屏「未保存」。
    expect(withSaveResult(msg, undefined)).toBe(msg)
    expect(withSaveResult(msg, null)).toBe(msg)
  })

  it('V2 落点都走这一处判断，且没有第二份 saveState 判断/文案', () => {
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
      if (rel(p) !== 'utils/withSaveResult.js' && /saveState\s*:/.test(text)) {
        offenders.push(rel(p))
      }
      if (rel(p) !== 'components/common/UnsavedHint.jsx' && text.includes('未保存，刷新后会丢失')) {
        offenders.push(`${rel(p)}（文案写死在第二处）`)
      }
    }
    expect(offenders).toEqual([])
  })
})

describe('pendingSaveKeys', () => {
  it('只挑 pending 的 key —— failed 的那条后端已经放弃了，带去对账只会白问', () => {
    const msgs = [
      { id: 'tmp-1', saveState: 'pending', saveKey: 'k1' },
      { id: 'tmp-2', saveState: 'failed', saveKey: 'k2' },
      { id: 9, content: '落库了的' },
      { id: 'tmp-3', saveState: 'pending', saveKey: 'k3' },
    ]

    expect(pendingSaveKeys(msgs)).toEqual(['k1', 'k3'])
  })

  it('没有未保存的消息时回空数组（空 keys = 只补写，与老行为一字不差）', () => {
    expect(pendingSaveKeys([])).toEqual([])
    expect(pendingSaveKeys(undefined)).toEqual([])
  })
})

describe('applyFlushReport', () => {
  it('V1 补上的填回真 id 并清掉「未保存」，其余保持同一个引用', () => {
    const queued = { id: 'tmp-1', content: 'a', saveState: 'pending', saveKey: 'k1' }
    const other = { id: 9, content: 'b' }

    const out = applyFlushReport([queued, other], {
      flushed: [{ key: 'k1', id: 42 }],
      dropped: [],
    })

    expect(out[0]).toEqual({ id: 42, content: 'a' })
    expect(out[1]).toBe(other)
  })

  it('V2 判死的翻成 failed（不给重试），认不出的 key 原样跳过', () => {
    const queued = { id: 'tmp-1', content: 'a', saveState: 'pending', saveKey: 'k1' }
    const gone = { id: 'tmp-2', content: 'b', saveState: 'pending', saveKey: 'k2' }
    const other = { id: 9, content: 'c' }

    const out = applyFlushReport([queued, gone], { flushed: [], dropped: ['k1', 'kX'] })

    expect(out[0]).toEqual({ id: 'tmp-1', content: 'a', saveState: 'failed', saveKey: 'k1' })
    expect(out[1]).toBe(gone) // key 没被这次读数提到 —— 还是 pending
    expect(applyFlushReport([other], { flushed: [], dropped: [] })).toEqual([other])
  })
})
