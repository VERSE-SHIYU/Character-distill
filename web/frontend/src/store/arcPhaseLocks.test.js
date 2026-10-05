/**
 * 前端结构锁 S3 / S7 / S10：这些「只此一处」的约束 grep 源码即可判，不需要渲染。
 * 参照上一段 `phase-anchoring` 的结构锁写法（读源码文本数出现次数）。
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const SRC = readFileSync(resolve(process.cwd(), 'src/store/useAppStore.js'), 'utf8')

describe('前端结构锁', () => {
  it('S3：start_session 请求体只由 startSessionBody 构造（字面量只此一份）', () => {
    const hits = SRC.match(/['"]\/api\/distill\/start_session['"]/g) || []
    expect(SRC).toMatch(/startSessionBody/)
    expect(hits.length).toBeLessThanOrEqual(1)
  })

  it('S7：sessionUserRole 只在 applySessionIdentity 里赋值', () => {
    const hits = SRC.match(/sessionUserRole:/g) || []
    expect(SRC).toMatch(/applySessionIdentity/)
    // 声明 + setter + applySessionIdentity 三处；超出即又有散落的赋值点
    expect(hits.length).toBeLessThanOrEqual(3)
  })

  it('S10：按卡偏好读写收敛到通用函数', () => {
    expect(SRC).toMatch(/getCardPref/)
    expect(SRC).toMatch(/setCardPref/)
  })
})
