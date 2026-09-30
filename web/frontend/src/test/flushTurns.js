// 让事件循环转 n 圈，用于断言「某件事没有发生」之前的收尾。
// 每圈 setTimeout(0) 之后事件循环会经过 check 阶段，React 调度器在 Node 下走 setImmediate，
// 于是排队的 promise 链与 React 提交都会跑完一轮。计的是圈数，不是毫秒，与机器快慢无关。
// 不包 act：被测代码若陷入自触发循环，act 会一直排空、永不返回，用例就判不出红。
export async function flushTurns(n = 2) {
  for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0))
}
