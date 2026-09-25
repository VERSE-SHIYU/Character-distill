// 前端上报的接线。配置**运行期**从 `/api/client-config` 取，不在构建期注入：一份镜像要
// 同时供 SZ、SG 用，构建期写死就只能按区各打一份（见 docs/specs/observability-glitchtip.md
// 的 D1）。
//
// 取不到配置、DSN 为空 —— 都不初始化 SDK，界面照常渲染。与后端
// `core/error_reporting.py` 那句「`SENTRY_DSN` 为空 = 整个模块不生效」同一口径：没有
// 上报不等于页面坏掉。
import * as Sentry from '@sentry/react'

/**
 * 取配置 → 有 DSN 才 `Sentry.init`，返回交给 `createRoot` 的选项。
 *
 * **返回值就是 createRoot 的第二个参数**，两种结果：
 *   - 初始化成功 → `{onUncaughtError, onCaughtError, onRecoverableError}` 三个钩子
 *   - 没初始化（未配置 / 取不到 / init 自己炸了）→ `{}`
 *
 * 空对象这个分支是必须的，不是「反正是空的」：React 19 只要在 `createRoot` 上收到这三个
 * 钩子里的任意一个，就不再走它**默认**的报错路径（dev 下的 console.error、以及在没被
 * boundary 接住时抛出）。没接上报却把钩子挂上去，等于把「现在能看见的错」换成「什么都
 * 看不见」—— 那比不接上报更糟。所以没上报时一个钩子都不给，行为与接线前逐字一致。
 *
 * **不抛异常**：这条跑在首屏渲染之前，它炸了就是白屏。
 */
export async function initErrorReporting() {
  try {
    const res = await fetch('/api/client-config')
    if (!res.ok) return {}
    const cfg = await res.json()
    if (!cfg || !cfg.sentry_dsn) return {}
    Sentry.init({
      dsn: cfg.sentry_dsn,
      release: cfg.release,
      initialScope: { tags: { region: cfg.region } },
      tracesSampleRate: 0,
      sendDefaultPii: false,
      // GlitchTip 不支持 session：留着只是每次加载各发一条白报的 envelope。
      integrations: (defaults) => defaults.filter((i) => i.name !== 'BrowserSession'),
    })
    // 三个钩子共用同一个处理函数，都走 SDK 的路径，不另写监听。
    const handler = Sentry.reactErrorHandler()
    return {
      onUncaughtError: handler,
      onCaughtError: handler,
      onRecoverableError: handler,
    }
  } catch (err) {
    // 这里再往上没有别的通道了（上报本身就没起来），console 是唯一去处。
    console.error('[observability] 上报未启用，照常渲染', err)
    return {}
  }
}
