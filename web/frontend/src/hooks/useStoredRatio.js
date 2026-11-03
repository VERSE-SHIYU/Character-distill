import { useCallback, useState } from 'react'

function read(storageKey, fallback, min, max) {
  if (!storageKey) return fallback
  try {
    const v = Number(localStorage.getItem(storageKey))
    return v >= min && v <= max ? v : fallback
  } catch { return fallback }
}

/**
 * 分栏比例：组件自己持有，按 `storageKey` 记在 localStorage（换页回来还是上次拖的位置）。
 * 读不到 / 越界就用 `defaultRatio`；存不下（隐私模式等）只是不记位置。
 * 左右分栏（SplitOrFullscreen）与上下分栏（StackSplit）共用，比例怎么存只此一处。
 */
export default function useStoredRatio(storageKey, defaultRatio, min, max) {
  const [ratio, setRatioState] = useState(() => read(storageKey, defaultRatio, min, max))
  const setRatio = useCallback((r) => {
    setRatioState(r)
    if (!storageKey) return
    try { localStorage.setItem(storageKey, String(r)) } catch { /* 存不下只是不记位置 */ }
  }, [storageKey])
  return [ratio, setRatio]
}
