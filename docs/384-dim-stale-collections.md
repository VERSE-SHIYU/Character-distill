# 384 维旧集合：静默失效根因修复 + 重建前预期行为

> 状态：根因已修（commit 4a971d1，2026-09-09）。本页是运维对照表 —— 看到
> 下面的日志先查这里，别误判成新故障。重建方案另议。

## 背景

- commit db8d1de（2026-06-24）把 embedder 从本地 SentenceTransformer(384) 换成
  DashScope text-embedding-v4(1024)。**迁移前写入的 chroma 集合全是 384 维**。
- **根因（已修）**：`rag.py` `load_existing` 只看 `count()>0` 不验维度 → 384 集合被
  当"可用"装载；`query` 时 chroma 维度错被 `query` / `query_with_emotion` 两层宽
  except 吞成 `[]` → 调用方当"没检索到" → **场景检索静默失效、无日志**。老卡用户
  感知为角色"不记得原著"，产品核心命题（从原著反演人格）在老卡上残废。
- **修复**：`load_existing` 验维度，不符抛 `CollectionUnusableError`（带 stored/expected）；
  `query`/`query_with_emotion` 失败显式上抛，真无匹配（空 documents）仍返回 `[]`；
  三条调用路径（单卡 chat / 群聊 / MCP）都降级可见、**不自动重建**。
- 回归：`tests/test_rag_unusable.py`（13 断言，hermetic + 三条调用路径都不 index 都有可见日志）；
  真实 chroma 在 Linux 容器验证 384 集合 `load_existing` 显式抛(384 vs 1024)、1024 集合正常装载。

## 影响面

本机 rig（sqlite）只读审计（2026-09-09）：**12 个 text_id → 19 张卡**（Shiyu 15 张全
2026-05 早于迁移 + testadmin 4 张）处于 384 静默空。生产 SZ/SG 是否同病**未做远端审计**
（用户指示不碰远端）；行为模型一致，受卡数需另核。

## 修复后 384 卡的预期行为（重建前）

- 单卡 chat：记 `RAG build failed (degraded)` → 降级
- 群聊 / MCP：记 WARN → 跳过场景检索
- 19 张受影响的卡从"静默返回空"变为"明确降级 + 可见日志"
- 用户体验未变好（场景检索仍无），但失败现在可见
- 因此日志里会出现一批 WARN，这是预期，不是新故障
- 重建方案另议
