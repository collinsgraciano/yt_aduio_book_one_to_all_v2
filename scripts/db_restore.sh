#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# 数据库恢复脚本 — 从压缩 SQL 备份文件恢复
# ═══════════════════════════════════════════════════════════════
# 用法：
#   bash scripts/db_restore.sh backups/audiobook_backup_20260720_120000.sql.gz
#   bash scripts/db_restore.sh /path/to/backup.sql.gz
#
# 警告：恢复会覆盖当前数据库中的所有数据！
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "${PROJECT_ROOT}"

if [ $# -lt 1 ]; then
    echo "用法: bash scripts/db_restore.sh <备份文件路径>"
    echo "示例: bash scripts/db_restore.sh backups/audiobook_backup_20260720_120000.sql.gz"
    exit 1
fi

BACKUP_FILE="$1"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "  [x] 备份文件不存在: ${BACKUP_FILE}"
    exit 1
fi

# ─── 读取 .env 中的 POSTGRES_PASSWORD ───
if [ -f .env ]; then
    POSTGRES_PASSWORD="$(grep -E '^POSTGRES_PASSWORD=' .env | cut -d'=' -f2- | tr -d '[:space:]')"
fi
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-changeme_strong_password}"

CONTAINER="audiobook_postgres"
PG_USER="audiobook_app"
PG_DB="audiobook"

echo "═══════════════════════════════════════════════════"
echo "  数据库恢复"
echo "  容器: ${CONTAINER}"
echo "  数据库: ${PG_DB}"
echo "  备份文件: ${BACKUP_FILE}"
echo "═══════════════════════════════════════════════════"

# ─── 确认 ───
echo ""
echo "  ⚠️  警告：恢复将覆盖当前数据库中的所有数据！"
echo "  建议先执行备份：bash scripts/db_backup.sh"
echo ""
read -p "  确认恢复？输入 yes 继续: " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "  已取消"
    exit 0
fi

# ─── 检查容器是否运行 ───
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    echo "  [x] 容器 ${CONTAINER} 未运行，请先启动: docker-compose up -d postgres"
    exit 1
fi

# ─── 执行恢复 ───
echo ""
echo "[1/2] 恢复数据库..."

# 先断开所有连接
docker exec "$CONTAINER" psql -U "$PG_USER" -d postgres -c "
    SELECT pg_terminate_backend(pid) FROM pg_stat_activity
    WHERE datname = '${PG_DB}' AND pid <> pg_backend_pid();
" 2>/dev/null || true

# 删除并重建数据库
docker exec "$CONTAINER" psql -U "$PG_USER" -d postgres -c "DROP DATABASE IF EXISTS ${PG_DB};" 2>/dev/null
docker exec "$CONTAINER" psql -U "$PG_USER" -d postgres -c "CREATE DATABASE ${PG_DB};" 2>/dev/null

# 恢复数据
gunzip -c "$BACKUP_FILE" | docker exec -i "$CONTAINER" psql -U "$PG_USER" -d "$PG_DB" 2>&1 | grep -v "^$" || true

echo "  ✓ 恢复完成"

# ─── 验证 ───
echo ""
echo "[2/2] 验证表统计:"
docker exec "$CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -t -c "
SELECT schemaname||'.'||relname AS table_name, n_live_tup AS row_count
FROM pg_stat_user_tables
ORDER BY n_live_tup DESC;
" 2>/dev/null | while read -r line; do
    [ -n "$line" ] && echo "  $line"
done

echo ""
echo "═══════════════════════════════════════════════════"
echo "  恢复完成"
echo "  建议重启 Web 服务: docker-compose restart web"
echo "═══════════════════════════════════════════════════"
