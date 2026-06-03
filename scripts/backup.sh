#!/bin/bash
# PostgreSQL 每日自动备份
# crontab: 0 3 * * * /home/ubuntu/Character-distill/scripts/backup.sh >> /var/log/backup.log 2>&1
#
# 双地域说明：
#   - 深圳机：备份本机 PG，上传阿里云 OSS（境内数据留境内）
#   - 新加坡机：备份本机 PG，上传腾讯云 COS（境外数据留境外）
#   两地各跑各的，互不传输，符合分库存储合规要求。
set -e

BACKUP_DIR="${BACKUP_DIR:-/home/ubuntu/backups}"

# 从 .env / 环境读取数据库连接（与 app 用同一套）
# DATABASE_URL=postgresql://USER:PASS@127.0.0.1:5432/DBNAME
DATABASE_URL="${DATABASE_URL:?DATABASE_URL 未设置}"

DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p "$BACKUP_DIR"

OUT="$BACKUP_DIR/charsim_${DATE}.sql.gz"

# pg_dump 在线逻辑备份（不锁库），直接压缩
# 通过容器执行，避免宿主机装 pg 客户端版本不匹配
docker compose -f /home/ubuntu/Character-distill/docker-compose.prod.yml \
  exec -T postgres pg_dump "$DATABASE_URL" | gzip > "$OUT"

echo "[backup] $(date): Backup created: $(basename "$OUT")"

# 保留最近 7 天
find "$BACKUP_DIR" -name "charsim_*.sql.gz" -mtime +7 -delete

# ---- 上传到对应地域的对象存储（按机器二选一，取消注释并配置）----
# 深圳机 → 阿里云 OSS（需先 ossutil config）
# ossutil cp "$OUT" oss://your-bucket/backups/ && echo "[backup] $(date): Uploaded to OSS"

# 新加坡机 → 腾讯云 COS（需先 coscli config）
# coscli cp "$OUT" cos://your-bucket/backups/ && echo "[backup] $(date): Uploaded to COS"

# ---- 恢复方法（应急时手动执行）----
# gunzip -c charsim_YYYYMMDD_HHMMSS.sql.gz | \
#   docker compose -f docker-compose.prod.yml exec -T postgres psql "$DATABASE_URL"
