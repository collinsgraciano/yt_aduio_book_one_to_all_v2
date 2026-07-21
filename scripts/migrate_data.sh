#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# 数据迁移脚本 — 从旧 PostgreSQL 迁移核心表数据到本项目
# ═══════════════════════════════════════════════════════════════
# 用法：
#   bash scripts/migrate_data.sh
#
# 环境变量（在 .env 或命令行设置）：
#   OLD_PG_HOST     旧数据库地址（默认 127.0.0.1）
#   OLD_PG_PORT     旧数据库端口（默认 5432）
#   OLD_PG_USER     旧数据库用户（默认 postgres）
#   OLD_PG_DB       旧数据库名（默认 audiobook）
#   OLD_PG_PASSWORD 旧数据库密码（必填）
#
# 示例：
#   OLD_PG_PASSWORD=old_pass bash scripts/migrate_data.sh
#   OLD_PG_HOST=85.121.48.55 OLD_PG_PASSWORD=old_pass bash scripts/migrate_data.sh
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "${PROJECT_ROOT}"

# ─── 颜色 ───
info() { echo -e "\033[36m[INFO]\033[0m $*"; }
ok()   { echo -e "\033[32m[OK]\033[0m $*"; }
warn() { echo -e "\033[33m[WARN]\033[0m $*"; }
error(){ echo -e "\033[31m[ERROR]\033[0m $*"; }

# ─── 旧数据库配置 ───
OLD_PG_HOST="${OLD_PG_HOST:-127.0.0.1}"
OLD_PG_PORT="${OLD_PG_PORT:-5432}"
OLD_PG_USER="${OLD_PG_USER:-postgres}"
OLD_PG_DB="${OLD_PG_DB:-audiobook}"
OLD_PG_PASSWORD="${OLD_PG_PASSWORD:-}"

# ─── 新数据库配置 ───
NEW_PG_USER="audiobook_app"
NEW_PG_DB="audiobook"
NEW_CONTAINER="audiobook_postgres"

# ─── 核心表列表 ───
CORE_TABLES=(
    "books"
    "book_processing_states"
    "youtube_credentials"
    "modelscope_tokens"
    "channel_runtime_settings"
    "task_queue"
)

BACKUP_FILE="/tmp/audiobook_migration_$(date +%Y%m%d_%H%M%S).sql"

echo "═══════════════════════════════════════════════════"
echo "  数据迁移：旧 PostgreSQL → Docker PostgreSQL"
echo "═══════════════════════════════════════════════════"
echo "  源数据库: ${OLD_PG_HOST}:${OLD_PG_PORT}/${OLD_PG_DB} (用户: ${OLD_PG_USER})"
echo "  目标数据库: 容器 ${NEW_CONTAINER} / ${NEW_PG_DB} (用户: ${NEW_PG_USER})"
echo "  迁移表: ${CORE_TABLES[*]}"
echo ""
warn "仅迁移上述 ${#CORE_TABLES[@]} 张核心表，其余表（global_settings, channels, audiobook_chapters 等）不会迁移"
echo ""

# ─── 检查旧数据库密码 ───
if [ -z "$OLD_PG_PASSWORD" ]; then
    error "未设置 OLD_PG_PASSWORD"
    echo "  用法: OLD_PG_PASSWORD=your_password bash scripts/migrate_data.sh"
    exit 1
fi

# ─── 检查新数据库容器 ───
if ! docker ps --format '{{.Names}}' | grep -q "^${NEW_CONTAINER}$"; then
    error "容器 ${NEW_CONTAINER} 未运行，请先启动: docker-compose up -d postgres"
    exit 1
fi

# ─── 步骤 1：测试旧数据库连接 ───
echo "[1/5] 测试旧数据库连接..."
if PGPASSWORD="$OLD_PG_PASSWORD" psql -h "$OLD_PG_HOST" -p "$OLD_PG_PORT" -U "$OLD_PG_USER" -d "$OLD_PG_DB" -c "SELECT 1;" >/dev/null 2>&1; then
    ok "旧数据库连接成功"
else
    error "无法连接旧数据库 ${OLD_PG_HOST}:${OLD_PG_PORT}/${OLD_PG_DB}"
    echo "  请检查 OLD_PG_HOST / OLD_PG_PORT / OLD_PG_USER / OLD_PG_PASSWORD"
    exit 1
fi

# ─── 步骤 2：导出旧数据库 ───
echo ""
echo "[2/5] 导出旧数据库数据..."
info "导出到: ${BACKUP_FILE}"

# 逐表导出（--data-only --column-inserts 确保可读性和兼容性）
EXPORT_OPTS="--data-only --column-inserts --no-owner --no-privileges"
TABLE_OPTS=""
for table in "${CORE_TABLES[@]}"; do
    TABLE_OPTS="$TABLE_OPTS --table=public.${table}"
done

PGPASSWORD="$OLD_PG_PASSWORD" pg_dump -h "$OLD_PG_HOST" -p "$OLD_PG_PORT" -U "$OLD_PG_USER" -d "$OLD_PG_DB" \
    $EXPORT_OPTS $TABLE_OPTS > "$BACKUP_FILE"

# 统计每张表的行数
info "旧数据库表统计:"
for table in "${CORE_TABLES[@]}"; do
    COUNT=$(PGPASSWORD="$OLD_PG_PASSWORD" psql -h "$OLD_PG_HOST" -p "$OLD_PG_PORT" -U "$OLD_PG_USER" -d "$OLD_PG_DB" -t -c "SELECT COUNT(*) FROM public.${table};" 2>/dev/null | tr -d '[:space:]')
    echo "  ${table}: ${COUNT} 行"
done

BACKUP_SIZE=$(du -h "$BACKUP_FILE" | awk '{print $1}')
ok "导出完成: ${BACKUP_SIZE}"

# ─── 步骤 3：验证新数据库表结构 ───
echo ""
echo "[3/5] 验证新数据库表结构..."
for table in "${CORE_TABLES[@]}"; do
    EXISTS=$(docker exec "$NEW_CONTAINER" psql -U "$NEW_PG_USER" -d "$NEW_PG_DB" -t -c \
        "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema='public' AND table_name='${table}');" 2>/dev/null | tr -d '[:space:]')
    if [ "$EXISTS" = "t" ]; then
        echo "  ✓ ${table}"
    else
        error "表 ${table} 不存在，请确认 docker/init-db.sql 已执行"
        exit 1
    fi
done
ok "所有表结构验证通过"

# ─── 步骤 4：导入数据 ───
echo ""
echo "[4/5] 导入数据..."

# 清空目标表（避免主键冲突）
info "清空目标表..."
docker exec "$NEW_CONTAINER" psql -U "$NEW_PG_USER" -d "$NEW_PG_DB" -c \
    "SET session_replication_role = replica; TRUNCATE $(IFS=,; echo "${CORE_TABLES[*]}") CASCADE; SET session_replication_role = DEFAULT;" 2>/dev/null

# 导入（禁用外键检查，避免依赖顺序问题）
info "导入数据..."
set +e
{
    echo "SET session_replication_role = replica;"
    cat "$BACKUP_FILE"
    echo "SET session_replication_role = DEFAULT;"
} | docker exec -i "$NEW_CONTAINER" psql -U "$NEW_PG_USER" -d "$NEW_PG_DB" 2>&1 | grep -v "^$"
IMPORT_PSQL_STATUS=${PIPESTATUS[1]}
set -e

if [ "$IMPORT_PSQL_STATUS" -ne 0 ]; then
    error "导入过程中出现错误"
    exit 1
fi

ok "导入完成"

# ─── 步骤 5：验证 ───
echo ""
echo "[5/5] 验证迁移结果..."
ALL_OK=true
for table in "${CORE_TABLES[@]}"; do
    OLD_COUNT=$(PGPASSWORD="$OLD_PG_PASSWORD" psql -h "$OLD_PG_HOST" -p "$OLD_PG_PORT" -U "$OLD_PG_USER" -d "$OLD_PG_DB" -t -c \
        "SELECT COUNT(*) FROM public.${table};" 2>/dev/null | tr -d '[:space:]')
    NEW_COUNT=$(docker exec "$NEW_CONTAINER" psql -U "$NEW_PG_USER" -d "$NEW_PG_DB" -t -c \
        "SELECT COUNT(*) FROM public.${table};" 2>/dev/null | tr -d '[:space:]')

    if [ "$OLD_COUNT" = "$NEW_COUNT" ]; then
        echo "  ✓ ${table}: ${OLD_COUNT} → ${NEW_COUNT} 行"
    else
        warn "${table}: 行数不一致 (旧: ${OLD_COUNT}, 新: ${NEW_COUNT})"
        ALL_OK=false
    fi
done

# 清理临时文件
rm -f "$BACKUP_FILE"

echo ""
echo "═══════════════════════════════════════════════════"
if [ "$ALL_OK" = true ]; then
    ok "迁移完成！所有表行数一致"
else
    warn "迁移完成，但部分表行数不一致，请检查日志"
fi
echo ""
echo "  下一步:"
echo "    1. 在 Web 界面配置全局设置（MODELSCOPE_TOKEN、TG_BOT_TOKEN 等）"
echo "    2. 添加频道并完成 OAuth 授权"
echo "    3. 验证 YouTube 凭证可用"
echo "    4. 重启 Web 服务: docker-compose restart web"
echo "═══════════════════════════════════════════════════"
