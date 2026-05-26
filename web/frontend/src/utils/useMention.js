import { useCallback, useMemo, useState } from 'react'

/**
 * Hook for @mention autocomplete in textareas.
 *
 * @param {Array<{id:string, name:string, avatar?:string}>} items - all mentionable items
 * @param {object} opts
 * @param {function} opts.onSelect - called with (item, mentionAtPos) when an item is selected
 * @param {number} [opts.maxResults=6] - max results to show
 * @returns {{
 *   mentionActive, mentionItems, selectedIndex,
 *   handleMentionKeyDown, handleMentionInput, resetMention,
 *   mentionPosition, mentionAtPos
 * }}
 */
export function useMention(items, opts = {}) {
  const { onSelect, maxResults = 6 } = opts

  const [mentionActive, setMentionActive] = useState(false)
  const [query, setQuery] = useState('')
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [mentionPosition, setMentionPosition] = useState({ left: 0, bottom: 0 })
  const [mentionAtPos, setMentionAtPos] = useState(-1)

  const mentionItems = useMemo(() => {
    if (!query) return items || []
    const q = query.toLowerCase()
    return (items || []).filter((it) => it.name?.toLowerCase().includes(q))
  }, [items, query])

  const resetMention = useCallback(() => {
    setMentionActive(false)
    setQuery('')
    setSelectedIndex(0)
    setMentionAtPos(-1)
  }, [])

  const handleMentionInput = useCallback((value, cursorPos, textareaEl) => {
    if (cursorPos == null || !textareaEl) {
      resetMention()
      return
    }

    const before = value.slice(0, cursorPos)
    const atIdx = before.lastIndexOf('@')
    if (atIdx === -1) {
      resetMention()
      return
    }

    const afterAt = before.slice(atIdx + 1)
    const queryMatch = afterAt.match(/^([\w一-鿿]*)$/)
    if (!queryMatch) {
      resetMention()
      return
    }

    const q = queryMatch[1]
    setQuery(q)
    setSelectedIndex(0)
    setMentionActive(true)
    setMentionAtPos(atIdx)

    if (textareaEl instanceof HTMLTextAreaElement) {
      const textBefore = value.slice(0, atIdx)
      const lines = textBefore.split('\n')
      const lineNum = lines.length - 1
      const colNum = lines[lineNum].length

      const lineHeight = 20
      const charWidth = 8.5
      const top = (lineNum + 1) * lineHeight
      const left = Math.min(colNum * charWidth, textareaEl.clientWidth - 220)
      setMentionPosition({ left: Math.max(4, left), bottom: textareaEl.clientHeight - top + lineHeight })
    }
  }, [resetMention])

  const handleMentionKeyDown = useCallback((e) => {
    if (!mentionActive || mentionItems.length === 0) return false

    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        setSelectedIndex((prev) => (prev + 1) % Math.min(mentionItems.length, maxResults))
        return true
      case 'ArrowUp':
        e.preventDefault()
        setSelectedIndex((prev) => (prev - 1 + Math.min(mentionItems.length, maxResults)) % Math.min(mentionItems.length, maxResults))
        return true
      case 'Enter':
      case 'Tab':
        if (mentionItems[selectedIndex]) {
          e.preventDefault()
          onSelect?.(mentionItems[selectedIndex], mentionAtPos)
          resetMention()
          return true
        }
        return false
      case 'Escape':
        e.preventDefault()
        resetMention()
        return true
      default:
        return false
    }
  }, [mentionActive, mentionItems, selectedIndex, maxResults, onSelect, resetMention, mentionAtPos])

  return {
    mentionActive,
    mentionItems,
    selectedIndex,
    handleMentionKeyDown,
    handleMentionInput,
    resetMention,
    mentionPosition,
    mentionAtPos,
  }
}
