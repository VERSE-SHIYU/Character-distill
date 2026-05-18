# 2026-05-17 语音合成模块 (edge-tts)

### 操作摘要

- **做了什么**：安装 edge-tts 7.2.8，创建 `speech/` 模块（抽象基类 + Edge TTS 实现），更新 dependencies
- **为什么**：为角色对话添加语音合成能力，使用 Microsoft Edge 免费 TTS API
- **影响范围**：`speech/`（新建 3 文件）、`requirements.txt`（追加 edge-tts>=6.1.0）

### 模块结构

```
speech/
├── __init__.py           # 导出 TTSEngine, EdgeTTSEngine, VOICES
├── tts_engine.py         # 抽象基类，定义 async synthesize(text, voice) -> bytes
└── edge_tts_client.py    # Edge TTS 实现，含 4 个中文语音预设
```

### 可用语音

| 键 | 语音 ID | 描述 |
|---|---------|------|
| xiaoxiao | zh-CN-XiaoxiaoNeural | 女声，活泼 |
| yunxi | zh-CN-YunxiNeural | 男声，青年 |
| xiaoyi | zh-CN-XiaoyiNeural | 女声，温柔 |
| yunyang | zh-CN-YunyangNeural | 男声，新闻播报 |
