import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist', '**/*.test.mjs']),
  {
    files: ['**/*.{js,jsx}'],
    extends: [js.configs.recommended],
    languageOptions: {
      globals: { ...globals.browser },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: { 'react-hooks': reactHooks },
    rules: {
      // Disable all recommended rules except no-undef
      ...Object.fromEntries(
        Object.keys(js.configs.recommended.rules)
          .filter((r) => r !== 'no-undef')
          .map((r) => [r, 'off']),
      ),
      'no-undef': 'error',
      'react-hooks/rules-of-hooks': 'error',
    },
  },
  {
    // 测试文件跑在 Node/vitest 环境下，需要 node globals（process、__dirname 等）
    // 必须与 eslint.config.js 中的同名 override 保持一致
    files: ['**/*.test.{js,jsx,mjs}', '**/__tests__/**/*.{js,jsx}'],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node, ...globals.es2021 },
    },
  },
])
