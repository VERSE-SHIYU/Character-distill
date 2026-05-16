# 📖 角色模拟器 Character Simulator

> 上传任何文本，自动蒸馏出角色，跟他沉浸式对话。

## 功能
- 🧬 **角色蒸馏**：从小说/聊天记录/人物资料自动提取性格、说话风格、价值观、内在矛盾
- 💬 **沉浸对话**：以角色口吻回复，RAG 检索原文保证设定一致性
- 🔌 **可扩展**：适配器模式支持切换 LLM，角色卡结构兼容 SillyTavern 标准

## 快速开始

```bash
git clone <repo>
cd character-simulator
py -3.12 -m pip install -r requirements.txt
cp .env.example .env   # 填入 DEEPSEEK_API_KEY
py -3.12 web/app.py    # 浏览器打开 localhost:7860
```

## 架构
输入文本
↓
adapters/llm_adapter.py   ← DeepSeek API 封装
↓
core/distiller.py         ← 角色蒸馏（→ CharacterCard）
core/rag.py               ← 原文向量检索（ChromaDB 内存模式）
core/chat_engine.py       ← 对话引擎（system prompt 构建 + history 管理）
↓
web/app.py                ← Gradio 界面

## 方法论

蒸馏 prompt 设计参考：
- **Nuwa-skill**：认知框架分层提取
- **BookWorld**（ACL 2025）：小说角色一致性建模

核心原则：跨场景验证 + 保留矛盾 + 忠于原文，拒绝美化。

## 路线图

- [ ] 局势分析（好感度 / 成功概率实时计算）
- [ ] 多角色同时蒸馏
- [ ] SillyTavern 角色卡导出（`.json`）
- [ ] React 前端替换 Gradio
- [ ] 微信接入
