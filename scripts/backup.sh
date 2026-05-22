#!/bin/bash
# SQLite每日自动备份 — crontab: 0 3 * * * /home/ubuntu/Character-distill/scripts/backup.sh
set -e

BACKUP_DIR="${BACKUP_DIR:-/home/ubuntu/backups}"
DB_PATH="${DB_PATH:-/home/ubuntu/Character-distill/data/charsim.db}"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"

# SQLite在线备份（不锁库）
sqlite3 "$DB_PATH" ".backup '$BACKUP_DIR/charsim_$DATE.db'"

# 压缩
gzip "$BACKUP_DIR/charsim_$DATE.db"

# 保留最近7天
find "$BACKUP_DIR" -name "*.gz" -mtime +7 -delete

echo "[backup] $(date): Backup created: charsim_$DATE.db.gz"

# 上传腾讯云COS（需安装coscli并先配置）
# coscli cp "$BACKUP_DIR/charsim_$DATE.db.gz" cos://your-bucket/backups/
# echo "[backup] $(date): Uploaded to COS"
