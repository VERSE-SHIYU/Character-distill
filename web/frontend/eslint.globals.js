import globals from 'globals'

// 被 lint 的源文件范围。两份配置的基础块共用这一份 —— 各写一遍就会漂移
// （.cjs 曾经因此两边都没被扫到）。
export const sourceFiles = ['**/*.{js,jsx,cjs}']

// 按文件划分的运行环境。全量配置（eslint.config.js）与 CI 门配置（eslint.ci.config.js）
// 共用这一份 —— 同一张表写两遍就会漂移。这里只补环境多出来的全局（node /
// serviceworker）；浏览器全局由各配置的基础块给。
export default [
  {
    // Node 环境：构建与工具配置、Playwright e2e、以及所有测试文件（vitest 跑在 Node 下）
    files: ['**/*.config.js', 'e2e/**/*.{js,jsx}', '**/*.test.{js,jsx,mjs}', '**/__tests__/**/*.{js,jsx}'],
    languageOptions: { globals: { ...globals.node, ...globals.es2021 } },
  },
  {
    // e2e 验收脚本是 CommonJS：既在 Node 下跑，也在 page.evaluate 的回调体里用
    // document 等浏览器全局（回调体写在源文件里，得让 eslint 看得见）
    files: ['**/*.cjs'],
    languageOptions: {
      sourceType: 'commonjs',
      globals: { ...globals.node, ...globals.browser, ...globals.es2021 },
    },
  },
  {
    // Service Worker：self、clients、caches 等
    files: ['public/sw.js'],
    languageOptions: { globals: { ...globals.serviceworker } },
  },
]
