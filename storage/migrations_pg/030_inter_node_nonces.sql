-- ============================================================
-- 030 — 节点间请求签名（RFC 9421）的防重放 nonce 表
--
-- 与 SQLite `097_inter_node_nonces.sql` 同一语义（两侧真库的表集 / 列集锁要求相等）。
--
-- 每个 v2 签名请求带一个随机 nonce；接收端验签通过后把它插进来，主键冲突 = 同一请求
-- 第二次到达 = 重放，拒绝。签名本身只在 created 之后 30s 内有效，所以超过窗口的行对
-- 防重放已无用，由应用层按 `created_at` 定期清掉（`prune_inter_node_nonces`）。
--
-- 用表而不是进程内缓存：进程重启只要几秒，而签名窗口是 30s —— 缓存一清，窗口内截获的
-- 请求就能重放成功。
-- ============================================================

CREATE TABLE IF NOT EXISTS inter_node_nonces (
    nonce      TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_inter_node_nonces_created_at ON inter_node_nonces(created_at);
