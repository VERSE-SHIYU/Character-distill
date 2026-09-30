-- 097 — 节点间请求签名的防重放 nonce 表（PG `030_inter_node_nonces.sql` 的孪生，语义见那边）。
CREATE TABLE IF NOT EXISTS inter_node_nonces (
    nonce      TEXT PRIMARY KEY,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_inter_node_nonces_created_at ON inter_node_nonces(created_at);
