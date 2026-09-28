import useSWR from 'swr'
import { fetchWithTimeout } from '../api/client'

const fetchJson = async (url) => (await fetchWithTimeout(url)).json()

// 某个用户的在线状态（/api/auth/user/:id/online）。请求、按 id 缓存、换人切换、定时刷新交给 SWR。
// online=null 表示尚未取到或对方对当前用户隐藏（接口隐藏时返回 online=null、hidden=true），调用方此时不显示状态。
export default function usePresence(userId, { refreshInterval = 0 } = {}) {
  const { data } = useSWR(userId ? `/api/auth/user/${userId}/online` : null, fetchJson, { refreshInterval })
  return {
    online: data ? data.online : null,
    hidden: !!data?.hidden,
    lastActive: data?.last_active_at || '',
  }
}
