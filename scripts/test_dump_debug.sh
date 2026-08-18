#!/bin/bash
# Debug script to test pg_dump + grep content check in isolation
set -euo pipefail
set -a
. /opt/character-distill/.env
set +a

DATE_TAG=$(date +%Y%m%d_%H%M%S)
DB_DUMP=/opt/character-distill/backups/db_test_${DATE_TAG}.sql.gz
mkdir -p /opt/character-distill/backups

echo "=== Step 1: pg_dump ==="
docker compose -f /opt/character-distill/docker-compose.prod.yml exec -T postgres sh -c "pg_dump -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" --no-owner --no-comments" 2>/dev/null | gzip > "$DB_DUMP"
echo "PIPESTATUS: ${PIPESTATUS[0]} ${PIPESTATUS[1]}"
echo "DUMP_SIZE: $(stat -c%s "$DB_DUMP")"

echo "=== Step 2: gzip -t ==="
gzip -t "$DB_DUMP" && echo "PASS" || echo "FAIL"

echo "=== Step 3: grep content check ==="
CORE_TABLES=("users" "cards" "texts")
CORE_TABLES_STR=$(IFS='|'; echo "${CORE_TABLES[*]}")
gunzip -c "$DB_DUMP" 2>/dev/null | grep -qE "COPY\s+(public\.)?($CORE_TABLES_STR)\s" && echo "MATCH" || echo "NO MATCH"

echo "=== Step 4: Raw COPY lines ==="
gunzip -c "$DB_DUMP" 2>/dev/null | grep -E "COPY" | head -10

ls -lh "$DB_DUMP"
rm -f "$DB_DUMP"
