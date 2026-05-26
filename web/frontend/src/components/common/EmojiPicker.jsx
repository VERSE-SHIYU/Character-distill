import { useCallback, useEffect, useRef, useState } from 'react'

const CATEGORIES = [
  {
    id: 'smile',
    label: '笑脸',
    icon: '😊',
    emojis: [
      '😀','😃','😄','😁','😆','😅','🤣','😂','🙂','😉','😊','😇','🥰','😍','🤩','😘',
      '😋','😛','😜','🤪','😝','🤑','🤗','🤭','🤔','😐','😏','😒','🙄','😬','😌','😴',
      '😷','🤒','🥳','🥺','😢','😭','😤','😡','🤬','💀','💩','🤡','👻','👽','🤖',
    ],
  },
  {
    id: 'hand',
    label: '手势',
    icon: '👋',
    emojis: [
      '👋','🤚','🖐️','✋','👌','🤌','🤏','✌️','🤞','🤟','🤘','🤙','👈','👉','👆','🖕',
      '👇','👍','👎','✊','👊','🤛','🤜','👏','🙌','👐','🤲','🤝','🙏','✍️','💅','🤳','💪',
    ],
  },
  {
    id: 'heart',
    label: '爱心',
    icon: '❤️',
    emojis: [
      '❤️','🧡','💛','💚','💙','💜','🖤','🤍','🤎','💔','❣️','💕','💞','💓','💗','💖','💘','💝','🫶',
    ],
  },
  {
    id: 'item',
    label: '物品',
    icon: '🎉',
    emojis: [
      '🎉','🎊','🎈','🎁','🎀','✨','🌟','⭐','🌈','🔥','💫','⚡','💥','🎵','🎶','🎤','🎧',
      '📱','💻','📷','🎥','📧','📨','💾','🎮','🎲','🧩','📚','📖','✏️','📝','📌','🔗',
    ],
  },
  {
    id: 'animal',
    label: '动物',
    icon: '🐱',
    emojis: [
      '🐶','🐱','🐭','🐹','🐰','🦊','🐻','🐼','🐨','🐯','🦁','🐮','🐷','🐸','🐵','🐔','🐧',
      '🐦','🦆','🦅','🦉','🐺','🐴','🦄','🐝','🦋','🐌','🐞','🐢','🐍','🐙','🐬','🐳','🦈',
    ],
  },
  {
    id: 'food',
    label: '食物',
    icon: '🍔',
    emojis: [
      '🍎','🍊','🍋','🍌','🍉','🍇','🍓','🍑','🥭','🍍','🥑','🥦','🥕','🌽','🍞','🧀','🥚',
      '🍳','🥓','🍔','🍟','🍕','🌮','🌯','🥗','🍜','🍲','🍣','🍱','🍤','🍙','🍰','🎂','🍦',
      '🍩','🍪','🍫','☕','🍵','🧃','🥤','🧋','🍺','🍻','🥂','🍷',
    ],
  },
  {
    id: 'sport',
    label: '运动',
    icon: '⚽',
    emojis: [
      '⚽','🏀','🏈','⚾','🎾','🏐','🏓','🏸','🎱','⛳','🏹','🎣','🥊','🥋','🎿','🏂','🏋️',
      '🤸','🤼','🤽','🤾','🏄','🏊','🧗','🧘',
    ],
  },
  {
    id: 'travel',
    label: '旅行',
    icon: '🚗',
    emojis: [
      '🚗','🚕','🚙','🚌','🚎','🚓','🚑','🚒','🚐','🚚','🚛','🏍️','🛵','🛺','🚲','🚏','⛽',
      '🚤','🚢','✈️','🚁','🚀','🌍','🌎','🌏','🗺️','🏔️','🏖️','🏝️',
    ],
  },
]

export default function EmojiPicker({ onEmojiSelect, textareaRef }) {
  const [catIdx, setCatIdx] = useState(0)
  const pickerRef = useRef(null)
  const catBarRef = useRef(null)

  const handleClick = useCallback((emoji) => {
    if (textareaRef?.current) {
      const ta = textareaRef.current
      const start = ta.selectionStart
      const end = ta.selectionEnd
      const val = ta.value
      ta.value = val.slice(0, start) + emoji + val.slice(end)
      ta.selectionStart = ta.selectionEnd = start + emoji.length
      ta.focus()
      ta.dispatchEvent(new Event('input', { bubbles: true }))
    }
    onEmojiSelect?.(emoji)
  }, [onEmojiSelect, textareaRef])

  // Scroll active category into view in the tab bar
  useEffect(() => {
    const el = catBarRef.current?.children[catIdx]
    el?.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' })
  }, [catIdx])

  return (
    <div className="emoji-picker" ref={pickerRef} onClick={(e) => e.stopPropagation()}>
      <div className="emoji-picker-cats" ref={catBarRef}>
        {CATEGORIES.map((cat, i) => (
          <button
            key={cat.id}
            type="button"
            className={`emoji-picker-cat${i === catIdx ? ' active' : ''}`}
            onClick={() => setCatIdx(i)}
            title={cat.label}
          >
            {cat.icon}
          </button>
        ))}
      </div>
      <div className="emoji-picker-grid">
        {CATEGORIES[catIdx].emojis.map((emoji) => (
          <button
            key={emoji}
            type="button"
            className="emoji-picker-item"
            onClick={() => handleClick(emoji)}
          >
            {emoji}
          </button>
        ))}
      </div>
    </div>
  )
}
