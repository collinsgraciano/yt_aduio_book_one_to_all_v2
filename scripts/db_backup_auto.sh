#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# 定时数据库备份脚本 — 压缩保存，仅保留最新 7 个
# ═══════════════════════════════════════════════════════════════
# 用法：
#   bash scripts/db_backup_auto.sh
#
# 定时任务（crontab -e）：
#   0 3 * * * cd /root/audiobook && bash scripts/db_backup_auto.sh >> backups/cron.log 2>&1
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "${PROJECT_ROOT}"

# ─── 配置 ───
BACKUP_DIR="${PROJECT_ROOT}/backups"
KEEP_COUNT=7
CONTAINER="audiobook_postgres"
PG_USER="audiobook_app"
PG_DB="audiobook"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/audiobook_backup_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始备份..."

# ─── 检查容器 ───
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    echo "[ERROR] 容器 ${CONTAINER} 未运行，跳过备份"
    exit 1
fi

# ─── 备份 ───
if ! docker exec "$CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" \
    --no-owner --no-privileges | gzip > "$BACKUP_FILE"; then
    rm -f "$BACKUP_FILE"
    echo "[ERROR] 备份失败，已删除不完整的备份文件"
    exit 1
fi

FILE_SIZE=$(du -h "$BACKUP_FILE" | awk '{print $1}')
echo "[OK] 备份完成: $(basename "$BACKUP_FILE") (${FILE_SIZE})"

# ─── 清理旧备份，只保留最新 KEEP_COUNT 个 ───
mapfile -t BACKUP_LIST < <(ls -1t "${BACKUP_DIR}"/audiobook_backup_*.sql.gz 2>/dev/null)
TOTAL=${#BACKUP_LIST[@]}

if [ "$TOTAL" -gt "$KEEP_COUNT" ]; then
    REMOVE_COUNT=$((TOTAL - KEEP_COUNT))
    echo "[CLEAN] 共 ${TOTAL} 个备份，保留最新 ${KEEP_COUNT} 个，删除 ${REMOVE_COUNT} 个旧备份"
    for file in "${BACKUP_LIST[@]:$KEEP_COUNT}"; do
        rm -f "$file"
        echo "  已删除: $(basename "$file")"
    done
else
    echo "[CLEAN] 共 ${TOTAL} 个备份，无需清理"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 完成"
