import { fetchWithTimeout } from '../api/client'

// 上传上限的唯一来源 = 后端 /api/text/limits。本模块只做三件事：取一次并缓存、
// 给纯函数做校验、给纯函数拼提示。**前端不留任何兜底数字** —— 写死一个上限，
// 后端调了值这里会悄悄放行（超限才拒）或误拒，且没有任何地方解释这个数字从哪来。
// 取不到就禁用上传并说明，这是唯一不引入第二个来源的做法。

export const LIMITS_UNAVAILABLE_MESSAGE = '暂时无法获取上传限制'

const LIMIT_FIELDS = ['story_max_tokens', 'chat_max_chars', 'max_file_bytes']

// 三个字段都必须是正整数：401/500 的错误体会被 res.ok 拦下，但 200 的错误壳
// （或字段改名）也会漏进来 —— 放行后校验没数可依，等于把「不知道」当「没上限」。
function assertLimits(data) {
  for (const k of LIMIT_FIELDS) {
    if (!Number.isInteger(data?.[k]) || data[k] <= 0) {
      throw new Error('上传限制响应格式非法')
    }
  }
  return data
}

let _cache = null
let _inflight = null

export function fetchTextLimits() {
  if (_cache) return Promise.resolve(_cache)
  if (_inflight) return _inflight
  _inflight = fetchWithTimeout('/api/text/limits')
    .then((res) => {
      if (!res.ok) throw new Error(`上传限制接口 ${res.status}`)
      return res.json()
    })
    .then(assertLimits)
    .then((data) => {
      _cache = data
      return data
    })
    .finally(() => { _inflight = null })
  return _inflight
}

export function resetTextLimitsCache() {
  _cache = null
  _inflight = null
}

// limits 为 null（还在取 / 取失败）→ 禁用。二者都禁用：加载期放行会与下面的
// 校验函数打架（校验没数可依），而加载是一瞬间的事。
export function uploadDisabled(limits) {
  return !limits
}

export function validateFile(file, limits) {
  if (!limits) return LIMITS_UNAVAILABLE_MESSAGE
  if (file.size > limits.max_file_bytes) {
    return `文件超过 ${Math.floor(limits.max_file_bytes / (1024 * 1024))}MB 上限`
  }
  return null
}

export function hintFromLimits(limits) {
  if (!limits) return LIMITS_UNAVAILABLE_MESSAGE
  const storyWan = Math.floor(limits.story_max_tokens / 10000)
  const chatWan = Math.floor(limits.chat_max_chars / 10000)
  const mb = Math.floor(limits.max_file_bytes / (1024 * 1024))
  return `小说按 token 计，上限 ${storyWan} 万 tokens · 聊天记录上限 ${chatWan} 万字 · 单文件最大 ${mb}MB`
}
