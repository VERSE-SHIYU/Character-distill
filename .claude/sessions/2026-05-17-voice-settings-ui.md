# 2026-05-17 语音功能设置页 UI + 全局状态

## 改动

### useAppStore.js
- 新增 voice 状态：`voiceStatus`, `voiceEnabled`, `voiceSpeed`, `voiceRefInfo`
- 新增 action：`checkVoiceStatus`, `uploadRefAudio`, `loadVoiceRef`, `deleteVoiceRef`, `setVoiceEnabled`, `setVoiceSpeed`
- `selectCard` 中新增 `get().loadVoiceRef()` 调用，角色切换时自动刷新参考音频状态

### App.jsx
- 引入 `useEffect`，启动时静默调用 `checkVoiceStatus()`

### SettingsPanel.jsx
- 根据 GPT-SoVITS 可用性三种状态渲染：
  1. 服务未检测到 → 仅显示底部小字链接 "如何启用语音功能 →"
  2. 服务就绪 + 无参考音频 → 引导卡片：虚线边框 + 浅蓝背景 + 文件选择 + prompt_text 输入
  3. 已配置 → 实线边框卡片：toggle 开关 + 语速 pill 按钮组 + 更换/删除 + ASR 状态行
- 角色切换时通过 useEffect 自动 loadVoiceRef

### global.css
- 新增语音卡片样式：`.voice-guide-card`, `.voice-configured-card`
- iOS toggle 开关：`.voice-toggle`, `.voice-toggle-slider`
- 语速 pill 按钮组：`.voice-speed-pill`, `.voice-speed-group`
- 删除按钮危险色、ASR 状态行

## 验证
- 前端 build：33 modules, 313ms, 0 errors
