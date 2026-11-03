import { useRef } from 'react'
import useIsMobile from '../../hooks/useIsMobile'
import useSplitDrag from '../../hooks/useSplitDrag'
import useStoredRatio from '../../hooks/useStoredRatio'

const MIN = 0.15
const MAX = 0.85

/**
 * 上下两栏 + 中间可拖动的横向分隔条（桌面）；手机上不分栏，上下顺排、随外层一起滚。
 *
 * 两栏各自滚动、各有最低高度 —— 任何一栏内容再多也挤不没另一栏。比例只归本组件管
 * （useStoredRatio 按 `storageKey` 记住位置），调用方只给两栏内容。
 *
 * 触发链：拖分隔条 → useSplitDrag 算出纵向比例 → setRatio（useStoredRatio 顺带写 localStorage）
 *        → 上栏 flex = ratio、下栏 flex = 1 - ratio 重排。
 */
export default function StackSplit({ top, bottom, storageKey, defaultRatio = 0.4 }) {
  const isMobile = useIsMobile()
  const containerRef = useRef(null)
  const [ratio, setRatio] = useStoredRatio(storageKey, defaultRatio, MIN, MAX)

  const onSplitterPointerDown = useSplitDrag(containerRef, {
    axis: 'y', min: MIN, max: MAX, onChange: setRatio,
  })

  if (isMobile) return <>{top}{bottom}</>

  return (
    <div ref={containerRef} className="stack-split">
      <div className="stack-split-pane" data-pane="top" style={{ flex: ratio }}>{top}</div>
      <div
        className="stack-splitter"
        role="separator"
        aria-orientation="horizontal"
        aria-label="拖动调整上下两栏高度"
        onPointerDown={onSplitterPointerDown}
      />
      <div className="stack-split-pane" data-pane="bottom" style={{ flex: 1 - ratio }}>{bottom}</div>
    </div>
  )
}
