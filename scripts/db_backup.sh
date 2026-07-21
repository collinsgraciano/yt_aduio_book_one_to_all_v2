#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# 数据库备份脚本 — 全库备份到压缩 SQL 文件
# ═══════════════════════════════════════════════════════════════
# 用法：
#   bash scripts/db_backup.sh                    # 备份到默认目录 ./backups/
#   bash scripts/db_backup.sh /path/to/backupdir  # 备份到指定目录
#   bash scripts/db_backup.sh /path/to/file.sql.gz  # 备份到指定文件
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "${PROJECT_ROOT}"

CONTAINER="audiobook_postgres"
PG_USER="audiobook_app"
PG_DB="audiobook"

# ─── 确定备份路径 ───
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

if [ $# -ge 1 ]; then
    TARGET="$1"
    if [[ "$TARGET" == *.sql.gz ]]; then
        BACKUP_FILE="$TARGET"
    else
        BACKUP_FILE="${TARGET}/audiobook_backup_${TIMESTAMP}.sql.gz"
    fi
else
    BACKUP_DIR="${PROJECT_ROOT}/backups"
    BACKUP_FILE="${BACKUP_DIR}/audiobook_backup_${TIMESTAMP}.sql.gz"
fi

BACKUP_DIR=$(dirname "$BACKUP_FILE")
mkdir -p "$BACKUP_DIR"

echo "═══════════════════════════════════════════════════"
echo "  数据库备份"
echo "  容器: ${CONTAINER}"
echo "  数据库: ${PG_DB}"
echo "  备份文件: ${BACKUP_FILE}"
echo "═══════════════════════════════════════════════════"

# ─── 检查容器是否运行 ───
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    echo "  [x] 容器 ${CONTAINER} 未运行"
    exit 1
fi

# ─── 执行备份 ───
echo "[1/2] 导出数据库..."
if ! docker exec "$CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" --no-owner --no-privileges | gzip > "$BACKUP_FILE"; then
    rm -f "$BACKUP_FILE"
    echo "  [x] 备份失败，已删除不完整的备份文件"
    exit 1
fi

FILE_SIZE=$(du -h "$BACKUP_FILE" | awk '{print $1}')
echo "  ✓ 备份完成: ${FILE_SIZE}"

# ─── 显示表统计 ───
echo ""
echo "[2/2] 表统计:"
docker exec "$CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -t -c "
SELECT schemaname||'.'||relname AS table_name, n_live_tup AS row_count
FROM pg_stat_user_tables
ORDER BY n_live_tup DESC;
" 2>/dev/null | while read -r line; do
    [ -n "$line" ] && echo "  $line"
done

echo ""
echo "═══════════════════════════════════════════════════"
echo "  备份完成: ${BACKUP_FILE} (${FILE_SIZE})"
echo "  恢复命令: bash scripts/db_restore.sh ${BACKUP_FILE}"
echo "═══════════════════════════════════════════════════"

# ─── 清理旧备份（保留最近 7 个）───
BACKUP_COUNT=$(ls -1 "${BACKUP_DIR}"/audiobook_backup_*.sql.gz 2>/dev/null | wc -l)
if [ "$BACKUP_COUNT" -gt 7 ]; then
    echo "  清理旧备份（保留最近 7 个）..."
    ls -1t "${BACKUP_DIR}"/audiobook_backup_*.sql.gz | tail -n +8 | while read -r old_file; do
        rm -f "$old_file"
        echo "  已删除: $(basename "$old_file")"
    done
fi
