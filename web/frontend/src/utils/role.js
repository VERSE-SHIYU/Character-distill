// 角色语义的唯一定义处是后端 `core/roles.py`；这里只回答前端的问题——
// 「这个账号要不要看到管理入口」。它不承担鉴权：每个写接口后端都自己判一次。
//
// 「是不是游客」的判据只有 `isGuest` 一处，组件里不许手写 `role === 'guest'`。
// **新增写操作入口时**，若其请求不在后端 `web/demo_gate.py` 的 `DEMO_WRITE_ALLOWLIST`
// 中，用 `hooks/useCanWrite.js` 的 `useCanWrite()` 在**入口层**隐藏该入口
// （页面入口 / 菜单 / 设置项 / 卡片操作区，不要下沉到逐个按钮）；
// 漏藏由后端 403 兜底 —— 前端隐藏只是体验层，不是安全边界。
export function isAdmin(user) {
  return user?.role === 'admin'
}

export function isGuest(user) {
  return user?.role === 'guest'
}
