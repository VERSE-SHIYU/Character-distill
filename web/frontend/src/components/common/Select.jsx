import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChevronDown } from './Icon'

// 弹层渲染前先估算高度决定向上还是向下翻：行高固定 32px（选项单行 + 6px 上下内边距），
// 弹层上下内边距 4px×2。列表超过 MAX_MENU_H 转为滚动，故估高按它封顶 —— 与 max-height
// 同值，估高即精确值，不需要 useLayoutEffect 实测。GAP 是弹层与触发器之间的缝。
const ITEM_H = 32
const MENU_PAD = 8
const MAX_MENU_H = 280
const GAP = 4

// 键盘行为对齐 W3C WAI-ARIA APG《Select-Only Combobox》：PageUp/PageDown 一次跳 10 项；
// 键入缓冲 500ms 无新键即清空（APG 用搜键串匹配，超时后重新开始）。
const PAGE_JUMP = 10
const TYPE_RESET_MS = 500

const CARET_SIZE = { md: 16, sm: 14 }

// A3 键入匹配，照抄 APG《Select-Only Combobox》的 getIndexByLetter：把选项按 startIndex
// 旋转（即「从当前高亮之后开始找、绕回」），整串前缀匹配优先；整串全是同一个字符时退化成
// 按首字符在候选间循环（连按同一个字母要在候选里轮转，而不是永远停在第一个）。
function getIndexByLetter(options, filter, startIndex = 0) {
  const ordered = [...options.slice(startIndex), ...options.slice(0, startIndex)]
  const startingWith = (list, needle) =>
    list.filter((o) => o.label.toLowerCase().indexOf(needle.toLowerCase()) === 0)
  const firstMatch = startingWith(ordered, filter)[0]
  if (firstMatch) return options.indexOf(firstMatch)
  if (filter.split('').every((c) => c === filter[0])) {
    return options.indexOf(startingWith(ordered, filter[0])[0])
  }
  return -1
}

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
  const listId = useId()
  const typeBufferRef = useRef('')
  const typeTimerRef = useRef(null)

  const optionId = (i) => `${listId}-opt-${i}`
  const selectedIndex = options.findIndex((o) => o.value === value)
  const selectedLabel = selectedIndex >= 0 ? options[selectedIndex].label : ''

  const clearTypeBuffer = () => {
    clearTimeout(typeTimerRef.current)
    typeBufferRef.current = ''
  }

  useEffect(() => clearTypeBuffer, [])

  const openMenu = (index = selectedIndex >= 0 ? selectedIndex : 0) => {
    if (disabled) return
    setActiveIndex(index)
    setRect(triggerRef.current.getBoundingClientRect())
    setOpen(true)
  }

  const closeMenu = () => {
    clearTypeBuffer()
    setOpen(false)
    setActiveIndex(-1)
  }

  const commit = (index) => {
    const opt = options[index]
    if (!opt) return
    closeMenu()
    if (opt.value !== value) onChange(opt.value)
  }

  // 可打印单字符才算键入；Space 归 B5，带修饰键的交给浏览器。
  const isPrintableKey = (e) =>
    e.key.length === 1 && e.key !== ' ' && !e.ctrlKey && !e.metaKey && !e.altKey

  // A3 键入缓冲，照抄 APG 的 getSearchString：缓冲串追加本键，500ms 无新键即清空。
  const typeahead = (char) => {
    clearTimeout(typeTimerRef.current)
    const buffer = (typeBufferRef.current + char).toLowerCase()
    typeBufferRef.current = buffer
    typeTimerRef.current = setTimeout(clearTypeBuffer, TYPE_RESET_MS)
    return getIndexByLetter(options, buffer, activeIndex + 1)
  }

  // A3 照抄 APG 的 onComboType 尾段：有匹配就移高亮；没匹配就当场清掉缓冲与计时器，
  // 否则下一次按键会跟这个死字符拼成两字串，永远匹配不上。
  const onType = (char) => {
    const next = typeahead(char)
    if (next >= 0) setActiveIndex(next)
    else clearTypeBuffer()
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

  // 长列表里 ↓ 走到第 9 项以后，高亮会滚出可视区 —— 跟着滚回来。
  // jsdom 没有实现 scrollIntoView，用可选调用，测试里 mock 后即可断言。
  useEffect(() => {
    if (!open || activeIndex < 0) return
    menuRef.current?.children[activeIndex]?.scrollIntoView?.({ block: 'nearest' })
  }, [open, activeIndex])

  const onTriggerKeyDown = (e) => {
    if (disabled) return
    if (!open) {
      if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        openMenu()
      } else if (e.key === 'ArrowUp' || e.key === 'Home') {
        // A1/A2：收起时 ↑ 与 Home 都是「展开 + 高亮首项」，不是当前值。
        e.preventDefault()
        openMenu(0)
      } else if (e.key === 'End') {
        e.preventDefault()
        openMenu(options.length - 1)
      } else if (isPrintableKey(e)) {
        // A3：无论有没有匹配都先展开 —— APG onComboType 第一行就是 updateMenuState(true)。
        openMenu()
        onType(e.key)
      }
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIndex((i) => (i + 1 < options.length ? i + 1 : i))
    } else if (e.key === 'ArrowUp' && e.altKey) {
      // A5：Alt+↑ 等同于 Enter（先于普通 ↑ 判断）。
      e.preventDefault()
      commit(activeIndex)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIndex((i) => (i - 1 >= 0 ? i - 1 : i))
    } else if (e.key === 'Home') {
      e.preventDefault()
      setActiveIndex(0)
    } else if (e.key === 'End') {
      e.preventDefault()
      setActiveIndex(options.length - 1)
    } else if (e.key === 'PageDown') {
      e.preventDefault()
      setActiveIndex((i) => Math.min(i + PAGE_JUMP, options.length - 1))
    } else if (e.key === 'PageUp') {
      e.preventDefault()
      setActiveIndex((i) => Math.max(i - PAGE_JUMP, 0))
    } else if (e.key === 'Enter' || e.key === ' ') {
      // Space 必须与 Enter 同效并 preventDefault：否则按钮会在 keyup 时触发一次
      // 原生 click，只把弹层关掉而选不中。
      e.preventDefault()
      commit(activeIndex)
    } else if (e.key === 'Escape') {
      e.preventDefault()
      closeMenu()
      triggerRef.current?.focus()
    } else if (e.key === 'Tab') {
      // A4：Tab 先选中高亮项，但不 preventDefault —— 焦点要照常移到下一个可聚焦元素。
      commit(activeIndex)
    } else if (isPrintableKey(e)) {
      onType(e.key)
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
        aria-controls={open ? listId : undefined}
        aria-activedescendant={open && activeIndex >= 0 ? optionId(activeIndex) : undefined}
        disabled={disabled}
        className={`ui-select ui-select--${size}${className ? ` ${className}` : ''}`}
        onClick={() => (open ? closeMenu() : openMenu())}
        onKeyDown={onTriggerKeyDown}
      >
        {selectedLabel}
        <ChevronDown size={CARET_SIZE[size] || CARET_SIZE.md} />
      </button>
      {open && menuStyle && createPortal(
        <div id={listId} ref={menuRef} role="listbox" aria-label={ariaLabel} className="ui-select-menu" style={menuStyle}>
          {options.map((o, i) => (
            <div
              key={o.value}
              id={optionId(i)}
              role="option"
              title={o.label}
              // A8：aria-selected 跟的是键盘高亮项（APG 原文：只出现在被
              // aria-activedescendant 引用的选项上）；「当前值」改用 .is-current 标。
              aria-selected={i === activeIndex}
              className={`ui-select-option${i === activeIndex ? ' is-active' : ''}${o.value === value ? ' is-current' : ''}`}
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
