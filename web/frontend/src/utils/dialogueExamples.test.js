import { describe, it, expect } from 'vitest'
import { examplesToText, textToExamples } from './dialogueExamples'

const PAIR1 = '掌柜：孔乙己么？你还欠十九个钱呢！\n孔乙己：这……下回还清罢。这一回是现钱，酒要好。'
const PAIR2 = '阿Q：秃儿！快回去，和尚等着你……\n小尼姑：你怎么动手动脚……'

describe('examplesToText：列表 → 表单文本', () => {
  it('数组用空一行连接', () => {
    expect(examplesToText([PAIR1, PAIR2])).toBe(`${PAIR1}\n\n${PAIR2}`)
  })

  it('空数组给空串', () => {
    expect(examplesToText([])).toBe('')
  })

  it('不是数组的原样返回', () => {
    expect(examplesToText('原样')).toBe('原样')
  })

  it('空值给空串', () => {
    expect(examplesToText(null)).toBe('')
    expect(examplesToText(undefined)).toBe('')
  })
})

describe('textToExamples：表单文本 → 列表', () => {
  it('按空行分组，组内每行保留（不去掉组内换行）', () => {
    expect(textToExamples(`${PAIR1}\n\n${PAIR2}`)).toEqual([PAIR1, PAIR2])
  })

  it('组内每行去掉首尾空白', () => {
    expect(textToExamples('  对方：xxx  \n  角色：yyy  ')).toEqual(['对方：xxx\n角色：yyy'])
  })

  it('空行里只有空白也算分组，多余空行不产生空组', () => {
    // 与 EditCardModalGoal E2 同一形态：行尾空白 + 空白行 + 连续空行
    const typed = '  掌柜：孔乙己么？你还欠十九个钱呢！  \n孔乙己：这……下回还清罢。这一回是现钱，酒要好。\n \n\n'
      + '阿Q：秃儿！快回去，和尚等着你……\n  小尼姑：你怎么动手动脚……\n\n'
    expect(textToExamples(typed)).toEqual([PAIR1, PAIR2])
  })

  it('空组丢掉', () => {
    expect(textToExamples(' \n\n ')).toEqual([])
  })

  it('空输入给空列表', () => {
    expect(textToExamples('')).toEqual([])
    expect(textToExamples(undefined)).toEqual([])
  })
})
