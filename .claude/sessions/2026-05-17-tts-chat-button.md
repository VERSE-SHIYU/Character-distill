# 2026-05-17 聊天消息 TTS 播放按钮

### 操作摘要

- **做了什么**：ChatArea 角色消息气泡加 TTS 播放按钮，全局单例播放器，CSS 样式，前端 build 通过
- **为什么**：用户可点击任一角色消息听取语音，快速连点自动停止前一条
- **影响范围**：`web/frontend/src/components/ChatArea.jsx`、`web/frontend/src/styles/global.css`

### 设计

- **全局单例**：`audioRef` 在 ChatView 顶层，`playTTS` 被 `useCallback` 稳定引用
- **同时只播一条**：新点击先 `pause()` + `revokeObjectURL()` 当前音频
- **按钮状态**：idle 显示 🔉（hover 可见），playing 显示 ⏳ 且 disabled
- **资源清理**：`onended` / `onerror` 回调中 `revokeObjectURL` + 重置 `audioRef`

### 验证

- 前端 build：33 modules, 1.19s ✓
- TTS API：200 audio/mpeg，empty text → 400 ✓
- 缓存命中：相同 text+voice 秒返 ✓
