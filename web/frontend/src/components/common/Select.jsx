import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChevronDown } from './Icon'

// 弹层渲染前先估算高度决定向上还是向下翻：行高固定 32px（选项单行 + 6px 上下内边距），
// 弹层上下内边距 4px×2。列表超过 MAX_MENU_H 转为滚动，故估高按它封顶 —— 与 max-height
// 同值，估高即精确值，不需要 useLayoutEffect 实测。GAP 是弹层与触发器之间的缝。
const ITEM_H = 32
const MENU_PAD = 8
const MAX_MENU_H = 280
const GAP = 4

const CARET_SIZE = { md: 16, sm: 14 }

export default function Select({
  value,
  options,
  onChange,
  disabled = false,
  size = 'md',
  ariaLabel,
  className = '',
}) {
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const [rect, setRect] = useState(null)
  const triggerRef = useRef(null)
  const menuRef = useRef(null)

  const selectedIndex = options.findIndex((o) => o.value === value)
  const selectedLabel = selectedIndex >= 0 ? options[selectedIndex].label : ''

  const openMenu = () => {
    if (disabled) return
    setActiveIndex(selectedIndex >= 0 ? selectedIndex : 0)
    setRect(triggerRef.current.getBoundingClientRect())
    setOpen(true)
  }

  const closeMenu = () => {
    setOpen(false)
    setActiveIndex(-1)
  }

  const commit = (index) => {
    const opt = options[index]
    if (!opt) return
    closeMenu()
    if (opt.value !== value) onChange(opt.value)
  }

  // 展开期间的三种关闭来源。处理器定义在 effect 内：既不留陈旧闭包，
  // 也让 add/remove 拿到同一组 (type, handler) 引用。
  useEffect(() => {
    if (!open) return
    const dismiss = () => {
      setOpen(false)
      setActiveIndex(-1)
    }
    // 触发器与弹层内部的 mousedown 不算「外部点击」——否则点选项会先被关掉，
    // 冒泡上来的 click 就落在已卸载的节点上。
    const onMouseDown = (e) => {
      if (triggerRef.current?.contains(e.target)) return
      if (menuRef.current?.contains(e.target)) return
      dismiss()
    }
    // 弹层自身可滚（长列表）—— 冒泡到 document 的那次滚动不算「页面在动」，
    // 否则一滚就关。捕获阶段挂在 document 上，弹层内的滚动同样会被收到。
    const onScroll = (e) => {
      if (menuRef.current?.contains(e.target)) return
      dismiss()
    }
    document.addEventListener('mousedown', onMouseDown)
    document.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', dismiss)
    return () => {
      document.removeEventListener('mousedown', onMouseDown)
      document.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', dismiss)
    }
  }, [open])

  const onTriggerKeyDown = (e) => {
    if (disabled) return
    if (!open) {
      if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        openMenu()
      }
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIndex((i) => (i + 1 < options.length ? i + 1 : i))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIndex((i) => (i - 1 >= 0 ? i - 1 : i))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      commit(activeIndex)
    } else if (e.key === 'Escape') {
      e.preventDefault()
      closeMenu()
      triggerRef.current?.focus()
    } else if (e.key === 'Tab') {
      closeMenu()
    }
  }

  const menuStyle = rect && {
    position: 'fixed',
    left: `${rect.left}px`,
    minWidth: `${rect.width}px`,
    maxHeight: `${MAX_MENU_H}px`,
    overflowY: 'auto',
    ...(rect.bottom + GAP + Math.min(options.length * ITEM_H + MENU_PAD, MAX_MENU_H) > window.innerHeight
      ? { bottom: `${window.innerHeight - rect.top + GAP}px` }
      : { top: `${rect.bottom + GAP}px` }),
  }

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        role="combobox"
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-label={ariaLabel}
        disabled={disabled}
        className={`ui-select ui-select--${size}${className ? ` ${className}` : ''}`}
        onClick={() => (open ? closeMenu() : openMenu())}
        onKeyDown={onTriggerKeyDown}
      >
        {selectedLabel}
        <ChevronDown size={CARET_SIZE[size] || CARET_SIZE.md} />
      </button>
      {open && menuStyle && createPortal(
        <div ref={menuRef} role="listbox" aria-label={ariaLabel} className="ui-select-menu" style={menuStyle}>
          {options.map((o, i) => (
            <div
              key={o.value}
              role="option"
              title={o.label}
              aria-selected={o.value === value}
              className={`ui-select-option${i === activeIndex ? ' is-active' : ''}`}
              onMouseEnter={() => setActiveIndex(i)}
              onClick={() => commit(i)}
            >
              {o.label}
            </div>
          ))}
        </div>,
        document.body,
      )}
    </>
  )
}
