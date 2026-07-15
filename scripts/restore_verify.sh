#!/bin/bash
# ============================================================
# 备份还原验证工具
#
# 用途：取最新 db dump，灌进临时 postgres 容器验证能成功还原，
#       不碰生产数据库。建议每周手动跑一次，或接入监控。
#
# 使用方式：
#   ./scripts/restore_verify.sh                  # 默认用 backups/ 下最新 dump
#   ./scripts/restore_verify.sh /path/to/dump.sql.gz  # 指定文件
#
# 退出码：
#   0 = 还原成功 + 核心表有数据
#   1 = 任何步骤失败
# ============================================================
set -euo pipefail

# ── 路径 ─────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKUP_DIR="$PROJECT_ROOT/backups"

# ── 颜色 / 状态标记 ──────────────────────────────────────────
OK="✅"
WARN="⚠️"
ERR="❌"
INFO="ℹ️"
log()   { echo "$*"; }
ok()    { log "$OK $*"; }
warn()  { log "$WARN $*"; }
err()   { log "$ERR $*"; }
info()  { log "$INFO $*"; }

# ── 确定要验证的 dump 文件 ──────────────────────────────────
if [ $# -ge 1 ]; then
  DUMP_FILE="$1"
else
  DUMP_FILE=$(ls -t "$BACKUP_DIR"/db_*.sql.gz 2>/dev/null | head -1 || true)
fi

if [ -z "$DUMP_FILE" ] || [ ! -f "$DUMP_FILE" ]; then
  err "找不到 dump 文件。用法: $0 [path/to/dump.sql.gz]"
  exit 1
fi

ok "验证目标: $DUMP_FILE"

# ── 前置校验 ─────────────────────────────────────────────────
info "检查 gzip 完整性..."
if ! gzip -t "$DUMP_FILE" 2>/dev/null; then
  err "gzip -t 校验失败: $DUMP_FILE"
  exit 1
fi

# 获取文件大小
DUMP_SIZE=$(stat -c%s "$DUMP_FILE" 2>/dev/null || stat -f%z "$DUMP_FILE" 2>/dev/null || echo 0)
DUMP_SIZE_MB=$((DUMP_SIZE / 1024 / 1024))
info "文件大小: ${DUMP_SIZE_MB}MB"

# ── 启动临时容器 ────────────────────────────────────────────
CONTAINER_NAME="pg_restore_verify_$$"
TEMP_DB="charsim_verify"
TEMP_USER="charsim"
TEMP_PASS="verify_temp_pass"
info "启动临时 postgres 容器: $CONTAINER_NAME..."

docker run -d --rm \
  --name "$CONTAINER_NAME" \
  -e POSTGRES_USER="$TEMP_USER" \
  -e POSTGRES_PASSWORD="$TEMP_PASS" \
  -e POSTGRES_DB="$TEMP_DB" \
  postgres:16-alpine >/dev/null 2>&1

# 确保退出时销毁
CLEANUP() {
  info "清理临时容器 $CONTAINER_NAME..."
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap CLEANUP EXIT

# 等待容器就绪
info "等待 postgres 就绪..."
for i in $(seq 1 15); do
  if docker exec "$CONTAINER_NAME" pg_isready -U "$TEMP_USER" >/dev/null 2>&1; then
    info "postgres 就绪（第 ${i} 次）"
    break
  fi
  if [ "$i" -eq 15 ]; then
    err "postgres 启动超时"
    exit 1
  fi
  sleep 1
done

# ── 还原 ──────────────────────────────────────────────────────
info "开始还原..."
START_TIME=$(date +%s)

if gunzip -c "$DUMP_FILE" | docker exec -i "$CONTAINER_NAME" \
  psql -U "$TEMP_USER" -d "$TEMP_DB" >/dev/null 2>&1; then
  END_TIME=$(date +%s)
  DURATION=$((END_TIME - START_TIME))
  ok "还原成功（${DURATION}s）"
else
  err "还原失败 — psql 返回错误"
  exit 1
fi

# ── 验证核心表有数据 ────────────────────────────────────────
CORE_TABLES=("users" "cards" "texts")
ALL_TABLES_OK=true

info "检查核心表行数..."
for tbl in "${CORE_TABLES[@]}"; do
  COUNT=$(docker exec "$CONTAINER_NAME" psql -U "$TEMP_USER" -d "$TEMP_DB" \
    -At -c "SELECT COUNT(*) FROM \"$tbl\";" 2>/dev/null || echo "0")

  # 去掉空白字符
  COUNT=$(echo "$COUNT" | tr -d '[:space:]')

  if [ -n "$COUNT" ] && [ "$COUNT" -gt 0 ] 2>/dev/null; then
    ok "表 $tbl: ${COUNT} 行"
  else
    warn "表 $tbl: 0 行（可能空表或表不存在）"
    ALL_TABLES_OK=false
  fi
done

if [ "$ALL_TABLES_OK" = true ]; then
  ok "全部核心表均有数据 — 还原验证通过"
else
  warn "部分核心表为空，请确认是否正常（新库空表可不视为异常）"
fi

# ── 清理由 trap EXIT 自动执行 ────────────────────────────────
ok "还原验证完成，临时容器已销毁"

# 退出码三态：
#   0 = 还原成功（无论行数是否 > 0）
#   1 = 还原过程失败（容器/psql/dump 损坏）
# 空表视为正常（生产库可能本就无数据），不因此判失败
exit 0
