// 角色语义的唯一定义处是后端 `core/roles.py`；这里只回答前端的问题——
// 「这个账号要不要看到管理入口」。它不承担鉴权：每个写接口后端都自己判一次。
export function isAdmin(user) {
  return user?.role === 'admin'
}
