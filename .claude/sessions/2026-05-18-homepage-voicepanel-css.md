### 09:30 补全 HomePage 和 VoicePanel 缺失的 CSS class
- **做了什么**：在 global.css 末尾追加 13 个缺失的 CSS class 定义（home-page, home-stats, home-stat-num, home-stat-count, home-action-row, home-empty-text, voice-panel, voice-section, voice-options, voice-options-title, voice-option, voice-status, voice-status-dot），以及更新了 voice-toggle-row、voice-toggle-label 等已有 class 的玻璃态样式
- **为什么**：HomePage.jsx 和 VoicePanel.jsx 引用了 13 个 global.css 中未定义的 class，页面渲染无样式
- **影响范围**：web/frontend/src/styles/global.css（末尾追加 ~150 行）
