import useAppStore from '../store/useAppStore'
import { isGuest } from '../utils/role'

/**
 * 当前账号能不能发写请求。`true` = 不是游客。
 *
 * 规则的唯一入口：判据是 `utils/role.js` 的 `isGuest`，规则说明写在那个文件的顶部。
 * 组件不要自己写 `isGuest(authUser)` —— 藏入口一律取这个 hook 的返回值，
 * 这样「哪些入口对游客不可见」只有一个可以改的地方。
 *
 * 未登录（`authUser` 为 null）返回 `true`：登录页那类屏不属于「游客」，
 * 按游客处理会把注册/登录入口一起藏掉。
 */
export default function useCanWrite() {
  const authUser = useAppStore((s) => s.authUser)
  return !isGuest(authUser)
}
