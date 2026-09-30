import '@testing-library/jest-dom'
import { configure } from '@testing-library/react'
import { ASYNC_UTIL_TIMEOUT_MS } from './timeouts'

configure({ asyncUtilTimeout: ASYNC_UTIL_TIMEOUT_MS })
