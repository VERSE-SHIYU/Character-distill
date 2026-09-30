import { it, expect } from 'vitest'
import { getConfig } from '@testing-library/react'
import { ASYNC_UTIL_TIMEOUT_MS } from './timeouts'

// setup.js 那一行 configure 被删掉时，这里是唯一会红的地方（负载不高时其他用例照样绿）。
it('Testing Library 的等待上限来自 timeouts.js', () => {
  expect(getConfig().asyncUtilTimeout).toBe(ASYNC_UTIL_TIMEOUT_MS)
})
