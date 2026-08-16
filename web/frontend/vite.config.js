import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': process.env.VITE_PROXY_TARGET || 'http://localhost:7860'
    }
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // Vite 8 默认 lightningcss 压缩会把 `-webkit-backdrop-filter` + `backdrop-filter` 压成只留前缀版，
    // 而现代 Chromium 已不认该别名 → Chrome 上玻璃全部失效。关掉 CSS 压缩以保留双属性（Safari/Chrome 都有雾感）。
    // CSS 未压缩 357KB vs 压缩 272KB，可接受；也避免未来再被"智能压缩"删掉作者写死的属性。
    cssMinify: false
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.js',
    exclude: ['**/node_modules/**', '**/*.mjs', '**/e2e/**'],
  }
})
