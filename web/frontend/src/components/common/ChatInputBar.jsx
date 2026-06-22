import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react'
import { Mic } from './Icon'
import { useAutoResizeTextarea } from '../../utils/useAutoResizeTextarea'
import { useMention } from '../../utils/useMention'
import useAppStore from '../../store/useAppStore'
import EmojiPicker from './EmojiPicker'
import MentionDropdown from './MentionDropdown'
import ResizableInputArea from './ResizableInputArea'

/**
 * Unified chat input bar used by ChatArea (人物对话), PrivateMessageChat (私信)
 * and GroupChatPage (群聊).
 *
 * Layout is always horizontal: [voice?] [emoji] [textarea + mention] [send].
 * Reply preview (if any) renders above the bar; an optional `topSlot` renders
 * above that (auto-conversation banner, etc.).
 *
 * Text state can be controlled (pass value + onChange) or uncontrolled
 * (component owns it). On send the component calls onSend(text) then clears.
 *
 * Exposes an imperative `focus()` via ref so callers (e.g. a quote action in a
 * message bubble) can focus the textarea.
 *
 * Props:
 *   onSend(text)            required. Called with trimmed-non-empty text.
 *   value / onChange        optional controlled text. onChange must accept an
 *                           updater fn (it does when it's a useState setter).
 *   disabled                disables textarea + send.
 *   placeholder             default '输入消息…'.
 *   disabledPlaceholder     placeholder shown while disabled (e.g. '等待回复中…').
 *   sending                 shows '…' on the send button instead of '发送'.
 *   mentionableItems        array → enables @mention dropdown. Omit/[] to disable.
 *   replyTo                 { preview, speaker? } → shows reply preview bar.
 *   onCancelReply()         called when reply preview is dismissed.
 *   voice                   { status, isRecording, recordingDuration, sendVoiceMessage }
 *                           → enables press-to-talk mic. Omit to hide mic entirely.
 *   topSlot                 node rendered above the bar (banners).
 */
const ChatInputBar = forwardRef(function ChatInputBar(props, ref) {
  const { topSlot, replyTo, onCancelReply, voice } = props
  const isRecording = !!voice?.isRecording

  return (
    <div className="chat-input-bar-wrap">
      {topSlot}
      {replyTo && !isRecording && (
        <div className="reply-preview-bar">
          <div className="reply-preview-info">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" /></svg>
            <span className="reply-preview-label">{replyTo.speaker ? `回复 ${replyTo.speaker}:` : '回复:'}</span>
            <span className="reply-preview-text">{replyTo.preview}</span>
          </div>
          <button type="button" className="reply-preview-close" onClick={onCancelReply}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>
          </button>
        </div>
      )}
      <ResizableInputArea>
        <ChatInputBarBody {...props} ref={ref} />
      </ResizableInputArea>
    </div>
  )
})

// Body lives INSIDE ResizableInputArea so useAutoResizeTextarea can read the
// InputHeightContext (drag-to-resize sync). The original ChatArea.ChatInput got
// this wrong by calling the hook outside the provider.
const ChatInputBarBody = forwardRef(function ChatInputBarBody(
  {
    onSend,
    value,
    onChange,
    disabled = false,
    placeholder = '输入消息…',
    disabledPlaceholder,
    sending = false,
    mentionableItems,
    onMention,
    voice,
  },
  ref,
) {
  const isControlled = value !== undefined
  const [internal, setInternal] = useState('')
  const text = isControlled ? value : internal
  const setText = isControlled ? onChange : setInternal

  const { textareaRef: taRef, resize } = useAutoResizeTextarea()
  const [showEmoji, setShowEmoji] = useState(false)

  useImperativeHandle(ref, () => ({
    focus: () => taRef.current?.focus(),
    clear: () => setText(''),
  }), [taRef, setText])

  // ---- @mention ----
  const mentionEnabled = Array.isArray(mentionableItems) && mentionableItems.length > 0
  const handleMentionSelect = useCallback((item, atPos) => {
    onMention?.(item)
    if (atPos >= 0) {
      setText((prev) => {
        const cursorAfter = taRef.current?.selectionStart ?? prev.length
        return prev.slice(0, atPos) + '@' + item.name + ' ' + prev.slice(cursorAfter)
      })
    }
    setTimeout(() => taRef.current?.focus(), 0)
  }, [setText, taRef, onMention])
  const mentionHook = useMention(mentionableItems || [], {
    onSelect: handleMentionSelect,
    maxResults: 6,
  })

  // ---- emoji outside-click ----
  useEffect(() => {
    if (!showEmoji) return
    const handler = (e) => {
      if (!e.target.closest('.emoji-picker') && !e.target.closest('[data-emoji-btn]')) {
        setShowEmoji(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showEmoji])

  // ---- send ----
  const handleSubmit = () => {
    const t = text
    if (!t.trim() || disabled) return
    onSend(t)
    setText('')
    setTimeout(() => {
      resize()
      taRef.current?.focus()
    }, 0)
  }
  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  // ---- voice recording (only when `voice` provided) ----
  const set = useAppStore.setState
  const mediaRecorderRef = useRef(null)
  const chunksRef = useRef([])
  const timerRef = useRef(null)
  const sendVoiceMessage = voice?.sendVoiceMessage
  const isRecording = !!voice?.isRecording
  const recordingDuration = voice?.recordingDuration ?? 0
  const funasrReady = !!voice?.status?.funasr

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream, { mimeType: 'audio/webm' })
      mediaRecorderRef.current = mr
      chunksRef.current = []
      mr.ondataavailable = (e) => chunksRef.current.push(e.data)
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop())
        if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
        const dur = useAppStore.getState().recordingDuration
        set({ isRecording: false, recordingDuration: 0 })
        if (dur < 1) return
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' })
        const asrText = await sendVoiceMessage?.(blob)
        if (asrText) {
          setText(asrText)
          setTimeout(() => resize(), 0)
        }
      }
      mr.start()
      set({ isRecording: true, recordingDuration: 0 })
      timerRef.current = setInterval(() => {
        set((s) => ({ recordingDuration: s.recordingDuration + 1 }))
      }, 1000)
    } catch {
      // permission denied or no mic
    }
  }
  const stopRecording = () => {
    if (mediaRecorderRef.current?.state === 'recording') {
      if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
      mediaRecorderRef.current.stop()
    }
  }
  const cancelRecording = useCallback(() => {
    const mr = mediaRecorderRef.current
    if (mr && mr.state !== 'inactive') {
      mr.ondataavailable = null
      mr.onstop = null
      mr.stop()
      mr.stream?.getTracks().forEach((t) => t.stop())
    }
    chunksRef.current = []
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
    set({ isRecording: false, recordingDuration: 0 })
  }, [set])

  useEffect(() => {
    if (!isRecording) return
    const onKey = (e) => { if (e.key === 'Escape') cancelRecording() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [isRecording, cancelRecording])

  if (isRecording) {
    return (
      <div className="chat-input-bar recording-bar">
        <span className="recording-dot" />
        <span className="recording-text">{`录音中 ${recordingDuration}s`}</span>
        <button type="button" className="recording-cancel-btn" onClick={cancelRecording}>取消</button>
        <span className="recording-hint">按 Esc 取消</span>
      </div>
    )
  }

  return (
    <div className="chat-input-bar">
      {voice && (
        funasrReady ? (
          <button
            type="button"
            className="record-btn"
            onMouseDown={startRecording}
            onMouseUp={stopRecording}
            onMouseLeave={stopRecording}
            onTouchStart={startRecording}
            onTouchEnd={stopRecording}
            title="按住录音"
          >
            <Mic size={16} />
          </button>
        ) : (
          <button type="button" className="chat-input-voice-btn" title="需要配置语音识别服务" disabled>
            {'\u{1F399}'}
          </button>
        )
      )}

      <button
        type="button"
        data-emoji-btn
        className="record-btn"
        title="表情"
        onClick={() => setShowEmoji((v) => !v)}
        style={{ fontSize: 18, lineHeight: 1 }}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10" /><path d="M8 14s1.5 2 4 2 4-2 4-2" /><line x1="9" y1="9" x2="9.01" y2="9" /><line x1="15" y1="9" x2="15.01" y2="9" /></svg>
      </button>

      <div style={{ position: 'relative', flex: 1, minWidth: 0 }}>
        {showEmoji && (
          <EmojiPicker
            controlled
            onEmojiSelect={(emoji) => {
              const ta = taRef.current
              const start = ta?.selectionStart ?? text.length
              const end = ta?.selectionEnd ?? start
              setText((prev) => prev.slice(0, start) + emoji + prev.slice(end))
              setShowEmoji(false)
              requestAnimationFrame(() => {
                if (ta) {
                  ta.focus()
                  ta.selectionStart = ta.selectionEnd = start + emoji.length
                  resize()
                }
              })
            }}
          />
        )}
        <div style={{ overflow: 'hidden', borderRadius: 'inherit' }}>
          <textarea
            ref={taRef}
            className="chat-textarea"
            rows={1}
            placeholder={disabled ? (disabledPlaceholder || placeholder) : placeholder}
            value={text}
            onChange={(e) => {
              const val = e.target.value
              setText(val)
              if (mentionEnabled) mentionHook.handleMentionInput(val, e.target.selectionStart, e.target)
              resize()
            }}
            onKeyDown={(e) => {
              if (mentionEnabled && mentionHook.handleMentionKeyDown(e)) return
              handleKeyDown(e)
            }}
            disabled={disabled}
          />
        </div>
        {mentionEnabled && (
          <MentionDropdown
            show={mentionHook.mentionActive}
            items={mentionHook.mentionItems}
            selectedIndex={mentionHook.selectedIndex}
            onSelect={(item) => handleMentionSelect(item, mentionHook.mentionAtPos)}
            position={mentionHook.mentionPosition}
          />
        )}
      </div>

      <button
        type="button"
        className="chat-send-btn"
        disabled={disabled || !text.trim()}
        onClick={handleSubmit}
      >
        {sending ? '…' : '发送'}
      </button>
    </div>
  )
})

export default ChatInputBar
