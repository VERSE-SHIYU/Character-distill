import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import { defineConfig, globalIgnores } from 'eslint/config'
import envGlobals, { sourceFiles } from './eslint.globals.js'

export default defineConfig([
  globalIgnores(['dist', '**/*.test.mjs']),
  {
    files: sourceFiles,
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
  ...envGlobals,
])
