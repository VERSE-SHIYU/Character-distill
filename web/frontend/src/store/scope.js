// 结构性竞态防护（代际计数）：写会话/角色态数据的 async action 用 scoped 包装，
// 在途请求跨过身份变更后，其写回被静默丢弃。guard 只存在这里，action 永不手写守卫。
let epoch = 0
export const bumpScope = () => { epoch += 1 }

// 捕获时机：在【调用时】捕获 at。scoped 本身只在 store 创建时执行一次，
// 若在包装时捕获会永久冻结为初始代际，此后任何 bumpScope 都会让写回全部失效。
export const scoped = (fn, set, get) => (...args) => {
  const at = epoch
  return fn(
    (patch) => { if (at === epoch) set(patch) },
    get,
    ...args,
  )
}
