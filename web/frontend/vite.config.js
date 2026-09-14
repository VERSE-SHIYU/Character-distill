import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// ---- 本地后端有两条拓扑，前端代理跟着走（详见 README「后端两条路」）----
//   原生：    uvicorn 直接跑，host 7860（README 方式二 / start_all.bat）
//   docker：  docker-compose.local.yml 把 host 7861 映射到容器 7860
// 默认只覆盖前者，另一套原靠人记住设 VITE_PROXY_TARGET —— 忘了就是满页 502 且看不出原因。
// 覆盖这个 env 的先例已存在于 playwright.config.js 的 webServer.env，这里沿用，不另造约定。
const NATIVE = 'http://localhost:7860'
const DOCKER_LOCAL = 'http://localhost:7861'

const TOPOLOGY_HELP = [
  '  原生拓扑：python -m uvicorn web.server:app --port 7860（或双击 start_all.bat）→ 后端在 7860',
  `  docker  ：docker compose -f docker-compose.local.yml up -d --build → 后端在 host 7861`,
  `            这一套要显式指定：VITE_PROXY_TARGET=${DOCKER_LOCAL} npm run dev`,
].join('\n')

async function probe(base) {
  try {
    const res = await fetch(`${base}/api/health`, { signal: AbortSignal.timeout(800) })
    return res.ok
  } catch {
    return false
  }
}

async function resolveProxyTarget() {
  const explicit = process.env.VITE_PROXY_TARGET
  if (explicit) {
    // 显式指定即无条件采信（拓扑语义不变），探活只用来把「设错了/没起」说出来
    if (await probe(explicit)) {
      console.log(`[vite] /api → ${explicit}（VITE_PROXY_TARGET 指定，/api/health 探通）`)
    } else {
      console.error(
        `[vite] /api → ${explicit}（VITE_PROXY_TARGET 指定，但 /api/health 探不通 —— 后端没起，或这个地址不对；页面会满屏 502）\n${TOPOLOGY_HELP}`,
      )
    }
    return explicit
  }

  for (const base of [NATIVE, DOCKER_LOCAL]) {
    if (await probe(base)) {
      console.log(`[vite] /api → ${base}（自动探活命中）`)
      return base
    }
  }

  console.error(
    `[vite] 后端探不到：${NATIVE} 与 ${DOCKER_LOCAL} 的 /api/health 都不通。\n` +
      `       代理暂时仍指向 ${NATIVE}，后端起来前页面会满屏 502。\n${TOPOLOGY_HELP}`,
  )
  return NATIVE
}

export default defineConfig(async ({ command }) => {
  // 只在真正起 dev server 时探活：build 与 vitest 都加载本配置，不该依赖后端存在
  const target =
    command === 'serve' && !process.env.VITEST
      ? await resolveProxyTarget()
      : process.env.VITE_PROXY_TARGET || NATIVE

  return {
    plugins: [react()],
    server: {
      proxy: {
        '/api': target,
      },
    },
    build: {
      outDir: 'dist',
      emptyOutDir: true,
      // Vite 8 默认 lightningcss 压缩会把 `-webkit-backdrop-filter` + `backdrop-filter` 压成只留前缀版，
      // 而现代 Chromium 已不认该别名 → Chrome 上玻璃全部失效。关掉 CSS 压缩以保留双属性（Safari/Chrome 都有雾感）。
      // CSS 未压缩 357KB vs 压缩 272KB，可接受；也避免未来再被"智能压缩"删掉作者写死的属性。
      cssMinify: false,
    },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: './src/test/setup.js',
      exclude: ['**/node_modules/**', '**/*.mjs', '**/e2e/**'],
    },
  }
})
