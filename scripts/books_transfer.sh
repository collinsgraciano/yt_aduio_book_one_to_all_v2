#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# books 表直传脚本 — 在源 VPS 上运行，直连目标 VPS PostgreSQL
# ═══════════════════════════════════════════════════════════════
# 用法：
#   bash scripts/books_transfer.sh --dsn "postgresql://USER:PASS@HOST:PORT/DB"
#   bash scripts/books_transfer.sh --dsn "postgresql://USER:PASS@HOST:PORT/DB" --force
#   bash scripts/books_transfer.sh --dsn "..." --container my_pg_container
#   bash scripts/books_transfer.sh --dsn "..." --bg
#   bash scripts/books_transfer.sh --dsn "..." --bg --force
#
# 原理：源容器 pg_dump books → 管道 → 源容器 psql 连目标库导入
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "${PROJECT_ROOT}"

# ─── 颜色输出 ───
info() { echo -e "\033[36m[INFO]\033[0m $*"; }
ok()   { echo -e "\033[32m[OK]\033[0m $*"; }
warn() { echo -e "\033[33m[WARN]\033[0m $*"; }
error(){ echo -e "\033[31m[ERROR]\033[0m $*"; }

# ─── 默认配置 ───
CONTAINER="audiobook_postgres"
PG_USER="audiobook_app"
PG_DB="audiobook"
TABLE="public.books"
TARGET_DSN=""
FORCE=false
BG_MODE=false

# ─── 解析参数 ───
while [ $# -gt 0 ]; do
    case "$1" in
        --dsn)       TARGET_DSN="$2"; shift 2 ;;
        --container) CONTAINER="$2"; shift 2 ;;
        --force)     FORCE=true; shift ;;
        --bg)        BG_MODE=true; shift ;;
        -h|--help)
            echo "用法: bash scripts/books_transfer.sh --dsn \"postgresql://USER:PASS@HOST:PORT/DB\" [选项]"
            echo ""
            echo "参数:"
            echo "  --dsn <连接串>      目标 VPS 的 PostgreSQL 连接字符串（必须）"
            echo "  --container <名称>   源 PostgreSQL 容器名（默认 audiobook_postgres）"
            echo "  --force              跳过确认提示"
            echo "  --bg                 后台运行，日志输出到 backups/transfer.log"
            echo ""
            echo "示例:"
            echo "  bash scripts/books_transfer.sh --dsn \"postgresql://audiobook_app:pass@1.2.3.4:5432/audiobook\""
            echo "  bash scripts/books_transfer.sh --dsn \"postgresql://audiobook_app:pass@1.2.3.4:5432/audiobook\" --force --bg"
            echo "  bash scripts/books_transfer.sh --dsn \"...\" --container my_pg --force"
            exit 0
            ;;
        *) error "未知参数: $1"; echo "  使用 --help 查看用法"; exit 1 ;;
    esac
done

if [ -z "$TARGET_DSN" ]; then
    error "必须指定 --dsn"
    echo '  用法: bash scripts/books_transfer.sh --dsn "postgresql://audiobook_app:PASSWORD@TARGET_IP:5432/audiobook"'
    exit 1
fi

# ─── 后台运行模式 ───
if [ "$BG_MODE" = true ]; then
    LOG_FILE="${PROJECT_ROOT}/backups/transfer.log"
    mkdir -p "$(dirname "$LOG_FILE")"
    # 去掉 --bg 参数，重新调用自身
    REEXEC_ARGS=()
    for arg in "$@"; do
        [ "$arg" != "--bg" ] && REEXEC_ARGS+=("$arg")
    done
    nohup bash "${BASH_SOURCE[0]}" "${REEXEC_ARGS[@]}" > "$LOG_FILE" 2>&1 &
    echo "═══════════════════════════════════════════════════"
    echo "  后台运行已启动"
    echo "  日志: ${LOG_FILE}"
    echo "  PID:  $!"
    echo "═══════════════════════════════════════════════════"
    echo ""
    echo "  查看进度: tail -f ${LOG_FILE}"
    echo "  确认运行: ps -p $!"
    exit 0
fi

# ─── 检查源容器 ───
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    error "源容器 ${CONTAINER} 未运行"
    echo "  可用 --container 指定其他容器名"
    echo "  当前运行的容器:"
    docker ps --format '    {{.Names}}' | grep -i postgres || echo "    （无 postgres 相关容器）"
    exit 1
fi

echo "═══════════════════════════════════════════════════"
echo "  books 表直传"
echo "  源:   本机 ${CONTAINER}"
echo "  目标: ${TARGET_DSN}"
echo "═══════════════════════════════════════════════════"

# ─── [1/5] 测试目标连接 ───
echo ""
echo "[1/5] 测试目标 PostgreSQL 连接..."
if ! docker exec "$CONTAINER" psql "$TARGET_DSN" -c "SELECT 1;" >/dev/null 2>&1; then
    error "无法连接目标 PostgreSQL"
    echo ""
    echo "  排查："
    echo "    1. 目标 VPS 防火墙是否放行 5432: ufw allow 5432/tcp"
    echo "    2. 连接串中的密码/IP/端口是否正确"
    echo "    3. 目标 VPS 的 postgres 容器是否在运行"
    exit 1
fi
ok "目标 PostgreSQL 连接成功"

# ─── [2/5] 获取行数 ───
echo ""
echo "[2/5] 获取数据统计..."
SOURCE_COUNT=$(docker exec "$CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -t -c \
    "SELECT COUNT(*) FROM ${TABLE};" 2>/dev/null | tr -d '[:space:]')
TARGET_COUNT=$(docker exec "$CONTAINER" psql "$TARGET_DSN" -t -c \
    "SELECT COUNT(*) FROM ${TABLE};" 2>/dev/null | tr -d '[:space:]')

info "源 VPS   books 行数: ${SOURCE_COUNT}"
info "目标 VPS books 行数: ${TARGET_COUNT}"

# ─── 确认 ───
if [ "$FORCE" = false ]; then
    echo ""
    warn "此操作将清空并替换目标 VPS 的 books 表！"
    echo "  目标当前: ${TARGET_COUNT} 行 → 替换为: ${SOURCE_COUNT} 行"
    echo ""
    read -p "  确认继续？输入 yes: " CONFIRM
    if [ "$CONFIRM" != "yes" ]; then
        echo "  已取消"
        exit 0
    fi
fi

# ─── [3/5] 备份目标 books 表 ───
echo ""
echo "[3/5] 备份目标 VPS 的 books 表..."
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${PROJECT_ROOT}/backups/target_books_backup_${TIMESTAMP}.sql.gz"
mkdir -p "$(dirname "$BACKUP_FILE")"

docker exec "$CONTAINER" pg_dump "$TARGET_DSN" \
    --data-only --column-inserts --no-owner --no-privileges \
    --table="$TABLE" | gzip > "$BACKUP_FILE"

BACKUP_SIZE=$(du -h "$BACKUP_FILE" | awk '{print $1}')
ok "已备份到: ${BACKUP_FILE} (${BACKUP_SIZE})"
echo "  回滚: gunzip -c ${BACKUP_FILE} | docker exec -i audiobook_postgres psql -U audiobook_app -d audiobook"

# ─── [4/5] 传输数据 ───
echo ""
echo "[4/5] 传输数据（TRUNCATE + 导入）..."
info "正在从源导出并直连目标导入..."

set +e
{
    echo "TRUNCATE ${TABLE} CASCADE;"
    docker exec "$CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" \
        --data-only --column-inserts --no-owner --no-privileges \
        --table="$TABLE"
} | docker exec -i "$CONTAINER" psql "$TARGET_DSN" 2>&1 | grep -v "^$"
TRANSFER_STATUS=${PIPESTATUS[1]}
set -e

if [ "$TRANSFER_STATUS" -ne 0 ]; then
    error "传输过程中出现错误"
    echo "  回滚: gunzip -c ${BACKUP_FILE} | docker exec -i audiobook_postgres psql -U audiobook_app -d audiobook"
    exit 1
fi
ok "数据传输完成"

# ─── [5/5] 验证 ───
echo ""
echo "[5/5] 验证..."
TARGET_AFTER=$(docker exec "$CONTAINER" psql "$TARGET_DSN" -t -c \
    "SELECT COUNT(*) FROM ${TABLE};" 2>/dev/null | tr -d '[:space:]')

if [ "$TARGET_AFTER" = "$SOURCE_COUNT" ]; then
    ok "行数匹配: ${TARGET_AFTER} 行"
else
    warn "行数不完全匹配（源 ${SOURCE_COUNT} → 目标 ${TARGET_AFTER}）"
fi

echo ""
info "目标 VPS books 表统计:"
docker exec "$CONTAINER" psql "$TARGET_DSN" -c "
    SELECT
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE book_status = 'success') AS success,
        COUNT(*) FILTER (WHERE book_status = 'pending') AS pending,
        COUNT(DISTINCT category) AS categories
    FROM ${TABLE};" 2>/dev/null

echo ""
info "前 5 条记录:"
docker exec "$CONTAINER" psql "$TARGET_DSN" -c \
    "SELECT book_id, book_name, category, book_status FROM ${TABLE} ORDER BY updated_at DESC LIMIT 5;" \
    2>/dev/null

echo ""
echo "═══════════════════════════════════════════════════"
echo "  传输完成"
echo "═══════════════════════════════════════════════════"
echo "  源 VPS   books: ${SOURCE_COUNT} 行"
echo "  目标 VPS books: ${TARGET_AFTER} 行（替换前: ${TARGET_COUNT} 行）"
echo ""
echo "  目标 VPS 备份文件（本机）:"
echo "    ${BACKUP_FILE}"
echo ""
echo "  回滚命令（在目标 VPS 上执行）:"
echo "    gunzip -c $(basename "$BACKUP_FILE") | docker exec -i audiobook_postgres psql -U audiobook_app -d audiobook"
echo ""
echo "  建议在目标 VPS 上重启 Web 服务:"
echo "    docker-compose restart web"
echo "═══════════════════════════════════════════════════"
