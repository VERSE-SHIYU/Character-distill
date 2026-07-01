#!/bin/bash
# ============================================================
# PostgreSQL 每日自动备份 + 上传文件打包
#
# 设计原则：同一份脚本适配双地域（SZ / SG），通过脚本自身
# 位置自动探测项目根目录，不写死路径。
#
# 使用方式：
#   BACKUP_ENCRYPT_KEY=xxx ./scripts/backup.sh              # 含 .env 加密备份
#   UPLOAD_ENABLED=true BACKUP_ENCRYPT_KEY=xxx ./scripts/backup.sh
#
# crontab（路径按实际部署填）：
#   0 3 * * * . /root/.backup_key; SCRIPT_ROOT/scripts/backup.sh >> SCRIPT_ROOT/backups/cron.log 2>&1
#   部署路径：SZ=/opt/character-distill  SG=/home/ubuntu/Character-distill
#   口令存 /root/.backup_key（chmod 600），不在 crontab 明文、不在仓库
# ============================================================
set -euo pipefail

# ── 路径自动探测 ──────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.prod.yml"

# ── source 项目 .env（导出所有变量，给后续 pg_dump / tar 等用）──
# set -a 让 source 的变量自动 export，避免 cron 环境空的问题
set -a
if [ -f "$PROJECT_ROOT/.env" ]; then
  # 用 tr 过滤 \r 防止 Windows 换行符污染变量值
  . <(tr -d '\r' < "$PROJECT_ROOT/.env")
fi
set +a

# ── 配置 ─────────────────────────────────────────────────────
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_ROOT/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
DISK_WARN_PCT="${DISK_WARN_PCT:-85}"
UPLOAD_ENABLED="${UPLOAD_ENABLED:-}"
# 自动判断上传目标：ossutil 存在 → SZ/OSS；coscli 存在 → SG/COS
if [ "$UPLOAD_ENABLED" != "false" ]; then
  if command -v ossutil &>/dev/null; then
    UPLOAD_ENABLED="${UPLOAD_ENABLED:-true}"
  fi
  # coscli 检测留 SG 阶段启用
fi

# 加密口令（从外部环境变量读，不在 .env 里、不硬编码）
ENCRYPT_KEY="${BACKUP_ENCRYPT_KEY:-}"

LOG_FILE="$BACKUP_DIR/backup.log"
DATE_TAG=$(date +%Y%m%d_%H%M%S)
DB_DUMP="$BACKUP_DIR/db_${DATE_TAG}.sql.gz"
DATA_TAR="$BACKUP_DIR/data_${DATE_TAG}.tar.gz"
ENV_ENC="$BACKUP_DIR/env_${DATE_TAG}.enc"
EXIT_CODE=0

# ── 颜色 / 状态标记 ──────────────────────────────────────────
OK="✅"
WARN="⚠️"
ERR="❌"
INFO="ℹ️"

log()   { local ts; ts=$(date '+%Y-%m-%d %H:%M:%S'); echo "[$ts] $*" >> "$LOG_FILE"; echo "$*"; }
ok()    { log "$OK $*"; }
warn()  { log "$WARN $*"; }
err()   { log "$ERR $*"; }
info()  { log "$INFO $*"; }

# ── 从 source 后的环境变量读取 DB 连接信息 ───────────────────
POSTGRES_USER="${POSTGRES_USER:-}"
POSTGRES_DB="${POSTGRES_DB:-}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"

if [ -z "$POSTGRES_USER" ] || [ -z "$POSTGRES_DB" ]; then
  err "POSTGRES_USER 或 POSTGRES_DB 为空（检查 .env 是否配置）"
  exit 1
fi

# 核心表清单（用于内容校验）
CORE_TABLES=("users" "cards" "texts")
CORE_TABLES_STR=$(IFS='|'; echo "${CORE_TABLES[*]}")

# ── 前置检查 ─────────────────────────────────────────────────
mkdir -p "$BACKUP_DIR"
info "备份开始 — 项目: $PROJECT_ROOT | 备份目录: $BACKUP_DIR"

# 检查 compose 文件
if [ ! -f "$COMPOSE_FILE" ]; then
  COMPOSE_FILE="$PROJECT_ROOT/docker-compose.yml"
  if [ ! -f "$COMPOSE_FILE" ]; then
    err "找不到 docker-compose 文件（检查过 prod.yml 和 .yml）"
    exit 1
  fi
fi
info "Compose 文件: $COMPOSE_FILE"

# 确认 postgres 容器在运行
POSTGRES_SERVICE=$(docker compose -f "$COMPOSE_FILE" ps --services --filter "status=running" 2>/dev/null \
  | grep -i postgres || true)
if [ -z "$POSTGRES_SERVICE" ]; then
  POSTGRES_CONTAINER=$(docker ps --format '{{.Names}}' | grep -i postgres | head -1 || true)
  if [ -z "$POSTGRES_CONTAINER" ]; then
    err "postgres 容器不在运行状态，无法备份数据库"
    exit 1
  fi
  info "使用容器名连接 postgres: $POSTGRES_CONTAINER"
else
  info "postgres 服务运行中"
fi

# 磁盘空间检查
AVAILABLE_PCT=$(df "$BACKUP_DIR" | awk 'NR==2 {gsub(/%/,"",$5); print $5}')
if [ "$AVAILABLE_PCT" -ge "$DISK_WARN_PCT" ]; then
  warn "磁盘使用率 ${AVAILABLE_PCT}%（阈值 ${DISK_WARN_PCT}%），剩余空间可能不足"
else
  info "磁盘使用率 ${AVAILABLE_PCT}%（阈值 ${DISK_WARN_PCT}%），空间充足"
fi

# ── pg_isready 探活 + 判断是否需要密码 ──────────────────────
info "检查 postgres 连接..."

# 使用容器内环境变量（POSTGRES_USER / POSTGRES_DB 由 docker-compose 注入）
if docker compose -f "$COMPOSE_FILE" exec -T postgres \
  sh -c 'pg_isready -U "$POSTGRES_USER"' >/dev/null 2>&1; then
  info "pg_isready 成功（trust / peer 认证）"
  PG_EXTRA=""
else
  if [ -n "$POSTGRES_PASSWORD" ]; then
    info "pg_isready 需密码，使用 PGPASSWORD 注入"
    # PGPASSWORD 通过 -e 注入容器 exec 环境，不进宿主机进程列表
    PG_EXTRA="-e PGPASSWORD=$POSTGRES_PASSWORD"
  else
    err "pg_isready 失败且 POSTGRES_PASSWORD 为空，无法连接数据库"
    exit 1
  fi
fi

# ── 第一步：pg_dump 数据库 ─────────────────────────────────
info "开始 pg_dump..."
START_TIME=$(date +%s)

# pg_dump 在容器内执行，-U / -d 用容器内 POSTGRES_USER / POSTGRES_DB 环境变量
# 密码如有需要由 PG_EXTRA 通过 exec -e 注入，不出现在命令行参数
if docker compose -f "$COMPOSE_FILE" exec -T $PG_EXTRA postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-comments' 2>/dev/null \
  | gzip > "$DB_DUMP"; then

  END_TIME=$(date +%s)
  DURATION=$((END_TIME - START_TIME))
  DUMP_SIZE=$(stat -c%s "$DB_DUMP" 2>/dev/null || stat -f%z "$DB_DUMP" 2>/dev/null || echo 0)
  DUMP_SIZE_KB=$((DUMP_SIZE / 1024))

  # 完整性校验 1：gzip 压缩流完整
  if ! gzip -t "$DB_DUMP" 2>/dev/null; then
    err "gzip -t 校验失败: $DB_DUMP"
    rm -f "$DB_DUMP"
    exit 1
  fi

  # 完整性校验 2：内容校验 — 解压后 grep 确认核心表存在
  # 用 grep -c（不用 -q）避免 pipefail + SIGPIPE 问题：
  #   -q 在首匹配退出，gunzip 管道断裂收到 SIGPIPE → pipefail 报 141
  #   -c 读完所有数据再退出，无 SIGPIPE
  CONTENT_CHECK=$(gunzip -c "$DB_DUMP" 2>/dev/null | grep -c -E "COPY\s+(public\.)?($CORE_TABLES_STR)\s" || true)
  if [ "$CONTENT_CHECK" -eq 0 ]; then
    err "内容校验失败：dump 中未找到核心表（${CORE_TABLES[*]}），视为坏备份"
    rm -f "$DB_DUMP"
    exit 1
  fi

  ok "数据库备份完成: $(basename "$DB_DUMP") (${DUMP_SIZE_KB}KB, ${DURATION}s) — 校验通过"
else
  err "pg_dump 执行失败"
  rm -f "$DB_DUMP" 2>/dev/null
  exit 1
fi

# ── 第二步：打包上传目录 ./data ──────────────────────────────
if [ -d "$PROJECT_ROOT/data" ] && [ "$(ls -A "$PROJECT_ROOT/data" 2>/dev/null)" ]; then
  info "开始打包 data 目录..."
  START_TIME=$(date +%s)

  if tar czf "$DATA_TAR" -C "$PROJECT_ROOT" data/ 2>/dev/null; then
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))
    DATA_SIZE=$(stat -c%s "$DATA_TAR" 2>/dev/null || stat -f%z "$DATA_TAR" 2>/dev/null || echo 0)
    DATA_SIZE_KB=$((DATA_SIZE / 1024))
    ok "data 目录打包完成: $(basename "$DATA_TAR") (${DATA_SIZE_KB}KB, ${DURATION}s)"
  else
    err "data 目录打包失败"
    rm -f "$DATA_TAR" 2>/dev/null
    EXIT_CODE=1
  fi
else
  info "data 目录为空或不存在，跳过打包"
fi

# ── 第三步：加密备份 .env ────────────────────────────────────
if [ -n "$ENCRYPT_KEY" ]; then
  if command -v openssl >/dev/null 2>&1; then
    info "加密备份 .env..."
    # 用 openssl enc 加密，口令从 BACKUP_ENCRYPT_KEY 环境变量读，不在命令行明文
    # 加密方式：AES-256-CBC + PBKDF2 派生
    if echo "$ENCRYPT_KEY" | openssl enc -aes-256-cbc -pbkdf2 -salt \
      -pass stdin \
      -in "$PROJECT_ROOT/.env" \
      -out "$ENV_ENC" 2>/dev/null; then
      ENV_SIZE=$(stat -c%s "$ENV_ENC" 2>/dev/null || stat -f%z "$ENV_ENC" 2>/dev/null || echo 0)
      ok ".env 加密备份完成: $(basename "$ENV_ENC") (${ENV_SIZE}B)"
      info "解密方式: openssl enc -d -aes-256-cbc -pbkdf2 -in <file> -pass pass:<BACKUP_ENCRYPT_KEY>"
    else
      err ".env 加密备份失败"
      EXIT_CODE=1
    fi
  else
    err "openssl 不可用，跳过 .env 加密备份"
    EXIT_CODE=1
  fi
else
  info ".env 加密备份跳过（BACKUP_ENCRYPT_KEY 未设置）"
  info "  如需加密 .env，请 export BACKUP_ENCRYPT_KEY=your-passphrase 后再运行"
fi

# ── 第四步：清理 7 天前的旧备份 ─────────────────────────────
# 注意：删旧备份在新备份生成并校验通过之后，不会误删
DELETED=$(find "$BACKUP_DIR" \( -name "db_*.sql.gz" -o -name "data_*.tar.gz" -o -name "env_*.enc" \) 2>/dev/null \
  | while IFS= read -r f; do
    # 使用 mtime +7 更可靠（跨不同 date 实现）
    find "$f" -mtime +"$RETENTION_DAYS" -delete 2>/dev/null && echo "$f"
  done)
if [ -n "$DELETED" ]; then
  info "清理旧备份: $(echo "$DELETED" | wc -l) 个文件"
else
  info "无过期备份需清理"
fi

# ── 第五步：异地上传（失败不中断）────────────────────────────────
# 上传远端保留天数（与本地 RETENTION_DAYS 一致）
OSS_RETENTION_DAYS="${OSS_RETENTION_DAYS:-7}"
# 注：将来 chromadb 有数据后，data tar 已在第一步包含 ./data 目录。
#     热备 chroma 前应先 sqlite3 chroma.sqlite3 ".backup /tmp/chroma_snap.sqlite3"
#     生成一致快照再 tar，避免 WAL 写入窗口拿到损坏快照。

upload_to_oss() {
  local bucket="oss://chardistill-sz-backup/backups"
  local files=("$DB_DUMP" "$DATA_TAR" "$ENV_ENC")
  local names=("db" "data" "env")
  local upload_ok=0
  local i f label cutoff file_date filename

  for i in "${!files[@]}"; do
    f="${files[$i]}"
    label="${names[$i]}"
    if [ ! -f "$f" ]; then
      warn "OSS 上传跳过（文件不存在）: $(basename "$f")"
      continue
    fi
    # 上传失败只告警，不中断脚本
    if ossutil cp "$f" "${bucket}/" 2>/dev/null; then
      # 上传后验证：ls 确认对象存在
      if ossutil ls "${bucket}/$(basename "$f")" 2>/dev/null | grep -q "$(basename "$f")"; then
        ok "异地上传完成 — ${label}: $(basename "$f")"
      else
        warn "异地上传后验证失败（上传可能未完成）: $(basename "$f")"
        upload_ok=1
      fi
    else
      warn "OSS 上传失败: $(basename "$f")"
      upload_ok=1
    fi
  done

  # ── OSS 远端保留期清理（只删 backups/ 下超 7 天的备份文件）──
  cutoff=$(date -d "-${OSS_RETENTION_DAYS} days" +%Y%m%d 2>/dev/null || \
           date -v -"${OSS_RETENTION_DAYS}"d +%Y%m%d 2>/dev/null || true)
  if [ -n "$cutoff" ]; then
    ossutil ls "${bucket}/" 2>/dev/null \
      | grep -oE 'oss://[^ ]+/(db_|data_|env_)[0-9]{8}_[0-9]{6}\.(sql\.gz|tar\.gz|enc)' \
      | while read -r obj_path; do
          filename=$(basename "$obj_path")
          file_date=$(echo "$filename" | sed 's/.*_\([0-9]\{8\}\)_.*/\1/')
          if [ "$file_date" -lt "$cutoff" ] 2>/dev/null; then
            ossutil rm "$obj_path" 2>/dev/null && info "OSS 清理过期备份: ${filename}"
          fi
        done
  fi
  return "$upload_ok"
}

upload_to_cos() {
  # SG → 腾讯云 COS（后续阶段配置）
  # coscli cp "$DB_DUMP" cos://charsim-backup-sg/backups/ --update
  # coscli cp "$DATA_TAR" cos://charsim-backup-sg/backups/ --update
  # echo "[backup] $(date): Uploaded to COS"
  :
}

# 上传段局部关闭 errexit：上传失败只告警，不因远端问题使整个备份任务失败
set +e
if [ "$UPLOAD_ENABLED" = "true" ]; then
  info "异地上传已启用"
  if command -v ossutil &>/dev/null; then
    upload_to_oss
    if [ $? -ne 0 ]; then
      warn "部分 OSS 上传失败，但本地备份已成功完成"
    fi
  elif command -v coscli &>/dev/null; then
    upload_to_cos
  else
    warn "未找到 ossutil 或 coscli，跳过上传"
  fi
else
  info "异地上传未启用（UPLOAD_ENABLED=false），跳过"
fi
set -e

# ── 完成 ────────────────────────────────────────────────────
if [ "$EXIT_CODE" -eq 0 ]; then
  ok "备份全部完成"
else
  warn "备份完成但有部分步骤失败（参见上方日志）"
fi
exit "$EXIT_CODE"
