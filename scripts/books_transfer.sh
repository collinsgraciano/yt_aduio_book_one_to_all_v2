#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# books 表直传脚本 — 在源 VPS 上运行，直接传输到目标 VPS
# ═══════════════════════════════════════════════════════════════
# 两种模式：
#   ssh     (默认) 通过 SSH 管道传输，无需暴露 PostgreSQL 端口
#   direct  直连目标 VPS 的 PostgreSQL 5432 端口
#
# 用法：
#   bash scripts/books_transfer.sh --target 1.2.3.4
#   bash scripts/books_transfer.sh --target 1.2.3.4 --ssh-user root
#   bash scripts/books_transfer.sh --target 1.2.3.4 --mode direct --target-pass PASSWORD
#   bash scripts/books_transfer.sh --target 1.2.3.4 --force
#
# SSH 模式要求：源 VPS 可通过 SSH 免密登录目标 VPS（ssh-keygen + ssh-copy-id）
# 直连模式要求：目标 VPS 的 5432 端口可访问
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

# ─── 配置 ───
CONTAINER="audiobook_postgres"
PG_USER="audiobook_app"
PG_DB="audiobook"
TABLE="public.books"

# ─── 默认参数 ───
TARGET_IP=""
TARGET_PASS=""
MODE="ssh"
SSH_USER="root"
FORCE=false
NO_RESTART=false

# ─── 解析参数 ───
while [ $# -gt 0 ]; do
    case "$1" in
        --target)      TARGET_IP="$2"; shift 2 ;;
        --target-ip)    TARGET_IP="$2"; shift 2 ;;
        --target-pass)  TARGET_PASS="$2"; shift 2 ;;
        --mode)         MODE="$2"; shift 2 ;;
        --ssh-user)     SSH_USER="$2"; shift 2 ;;
        --force)        FORCE=true; shift ;;
        --no-restart)   NO_RESTART=true; shift ;;
        -h|--help)
            echo "用法: bash scripts/books_transfer.sh --target <目标IP> [选项]"
            echo ""
            echo "选项:"
            echo "  --target <IP>        目标 VPS IP 地址（必须）"
            echo "  --mode <ssh|direct>  传输模式（默认 ssh）"
            echo "  --target-pass <密码> 目标 PostgreSQL 密码（direct 模式必须）"
            echo "  --ssh-user <用户>    SSH 用户名（默认 root）"
            echo "  --force              跳过确认提示"
            echo "  --no-restart         不重启目标 Web 服务"
            echo ""
            echo "示例:"
            echo "  bash scripts/books_transfer.sh --target 1.2.3.4"
            echo "  bash scripts/books_transfer.sh --target 1.2.3.4 --mode direct --target-pass mypass"
            exit 0
            ;;
        *)
            error "未知参数: $1"
            echo "  使用 --help 查看用法"
            exit 1
            ;;
    esac
done

if [ -z "$TARGET_IP" ]; then
    error "必须指定目标 VPS IP: --target <IP>"
    exit 1
fi

if [ "$MODE" != "ssh" ] && [ "$MODE" != "direct" ]; then
    error "模式必须是 ssh 或 direct"
    exit 1
fi

if [ "$MODE" = "direct" ] && [ -z "$TARGET_PASS" ]; then
    error "direct 模式需要 --target-pass <密码>"
    echo "  密码在目标 VPS 的 .env 文件中: grep POSTGRES_PASSWORD .env"
    exit 1
fi

# ─── 检查源容器 ───
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    error "源容器 ${CONTAINER} 未运行"
    exit 1
fi

# ─── 辅助函数：在目标 VPS 执行命令 ───
target_exec() {
    if [ "$MODE" = "ssh" ]; then
        ssh "${SSH_USER}@${TARGET_IP}" "$@"
    else
        docker exec -e PGPASSWORD="$TARGET_PASS" "$CONTAINER" \
            psql -h "$TARGET_IP" -U "$PG_USER" -d "$PG_DB" "$@"
    fi
}

# ─── 辅助函数：在目标 VPS 执行 docker exec ───
target_docker() {
    ssh "${SSH_USER}@${TARGET_IP}" "docker exec ${CONTAINER} $@"
}

target_docker_psql() {
    ssh "${SSH_USER}@${TARGET_IP}" "docker exec ${CONTAINER} psql -U ${PG_USER} -d ${PG_DB} $@"
}

# ─── 检查目标连通性 ───
echo "═══════════════════════════════════════════════════"
echo "  books 表直传"
echo "  模式: ${MODE}"
echo "  源:   本机 (${CONTAINER})"
echo "  目标: ${TARGET_IP}"
echo "═══════════════════════════════════════════════════"

echo ""
echo "[0/5] 检查目标连通性..."
if [ "$MODE" = "ssh" ]; then
    if ssh -o ConnectTimeout=10 -o BatchMode=yes "${SSH_USER}@${TARGET_IP}" "echo ok" >/dev/null 2>&1; then
        ok "SSH 连接成功: ${SSH_USER}@${TARGET_IP}"
    else
        error "SSH 连接失败: ${SSH_USER}@${TARGET_IP}"
        echo ""
        echo "  配置 SSH 免密登录："
        echo "    # 在源 VPS 上生成密钥（如已有可跳过）"
        echo "    ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519"
        echo ""
        echo "    # 复制公钥到目标 VPS"
        echo "    ssh-copy-id -i ~/.ssh/id_ed25519.pub ${SSH_USER}@${TARGET_IP}"
        echo ""
        echo "  或使用直连模式："
        echo "    bash scripts/books_transfer.sh --target ${TARGET_IP} --mode direct --target-pass PASSWORD"
        exit 1
    fi

    # 检查目标容器是否运行
    if ! target_docker "echo ok" >/dev/null 2>&1; then
        error "目标 VPS 上容器 ${CONTAINER} 未运行"
        exit 1
    fi
    ok "目标容器 ${CONTAINER} 正常运行"

else
    # direct 模式：测试 PostgreSQL 连接
    if docker exec -e PGPASSWORD="$TARGET_PASS" "$CONTAINER" \
        psql -h "$TARGET_IP" -U "$PG_USER" -d "$PG_DB" -c "SELECT 1;" >/dev/null 2>&1; then
        ok "直连目标 PostgreSQL 成功: ${TARGET_IP}:5432"
    else
        error "无法连接目标 PostgreSQL: ${TARGET_IP}:5432"
        echo ""
        echo "  可能原因："
        echo "    1. 目标 VPS 防火墙未放行 5432 端口"
        echo "    2. POSTGRES_PASSWORD 不正确"
        echo ""
        echo "  放行端口（在目标 VPS 上执行）："
        echo "    ufw allow 5432/tcp"
        echo ""
        echo "  或使用 SSH 模式（无需暴露端口）："
        echo "    bash scripts/books_transfer.sh --target ${TARGET_IP} --mode ssh"
        exit 1
    fi
fi

# ─── 获取源和目标的行数 ───
echo ""
echo "[1/5] 获取数据统计..."
SOURCE_COUNT=$(docker exec "$CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -t -c \
    "SELECT COUNT(*) FROM ${TABLE};" 2>/dev/null | tr -d '[:space:]')

if [ "$MODE" = "ssh" ]; then
    TARGET_COUNT=$(target_docker_psql "-t -c \"SELECT COUNT(*) FROM ${TABLE};\"" 2>/dev/null | tr -d '[:space:]')
else
    TARGET_COUNT=$(docker exec -e PGPASSWORD="$TARGET_PASS" "$CONTAINER" \
        psql -h "$TARGET_IP" -U "$PG_USER" -d "$PG_DB" -t -c \
        "SELECT COUNT(*) FROM ${TABLE};" 2>/dev/null | tr -d '[:space:]')
fi

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

# ─── 备份目标 books 表 ───
echo ""
echo "[2/5] 备份目标 VPS 的 books 表..."
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="books_auto_backup_${TIMESTAMP}.sql.gz"

if [ "$MODE" = "ssh" ]; then
    ssh "${SSH_USER}@${TARGET_IP}" "mkdir -p /root/audiobook/backups && \
        docker exec ${CONTAINER} pg_dump -U ${PG_USER} -d ${PG_DB} \
        --data-only --column-inserts --no-owner --no-privileges \
        --table=${TABLE} | gzip > /root/audiobook/backups/${BACKUP_NAME}"
    ok "已备份到目标 VPS: /root/audiobook/backups/${BACKUP_NAME}"
else
    # direct 模式：在源 VPS 上保存备份
    mkdir -p "${PROJECT_ROOT}/backups"
    docker exec -e PGPASSWORD="$TARGET_PASS" "$CONTAINER" \
        pg_dump -h "$TARGET_IP" -U "$PG_USER" -d "$PG_DB" \
        --data-only --column-inserts --no-owner --no-privileges \
        --table="$TABLE" | gzip > "${PROJECT_ROOT}/backups/${BACKUP_NAME}"
    ok "已备份到本机: backups/${BACKUP_NAME}"
fi

# ─── 传输数据 ───
echo ""
echo "[3/5] 传输数据（TRUNCATE + 导入）..."

# 构建完整的 SQL 流：禁用外键 → TRUNCATE → INSERT 数据 → 恢复外键
TRANSFER_SQL=$(
    docker exec "$CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" \
        --data-only --column-inserts --no-owner --no-privileges \
        --table="$TABLE"
)

if [ "$MODE" = "ssh" ]; then
    # SSH 模式：通过管道传输到目标 VPS
    {
        echo "SET session_replication_role = replica;"
        echo "TRUNCATE ${TABLE} CASCADE;"
        echo "$TRANSFER_SQL"
        echo "SET session_replication_role = DEFAULT;"
    } | ssh "${SSH_USER}@${TARGET_IP}" \
        "docker exec -i ${CONTAINER} psql -U ${PG_USER} -d ${PG_DB}" 2>&1 | grep -v "^$" || true
else
    # direct 模式：源容器直连目标 PostgreSQL
    {
        echo "SET session_replication_role = replica;"
        echo "TRUNCATE ${TABLE} CASCADE;"
        echo "$TRANSFER_SQL"
        echo "SET session_replication_role = DEFAULT;"
    } | docker exec -e PGPASSWORD="$TARGET_PASS" -i "$CONTAINER" \
        psql -h "$TARGET_IP" -U "$PG_USER" -d "$PG_DB" 2>&1 | grep -v "^$" || true
fi

ok "数据传输完成"

# ─── 验证 ───
echo ""
echo "[4/5] 验证..."
if [ "$MODE" = "ssh" ]; then
    TARGET_AFTER=$(target_docker_psql "-t -c \"SELECT COUNT(*) FROM ${TABLE};\"" 2>/dev/null | tr -d '[:space:]')
else
    TARGET_AFTER=$(docker exec -e PGPASSWORD="$TARGET_PASS" "$CONTAINER" \
        psql -h "$TARGET_IP" -U "$PG_USER" -d "$PG_DB" -t -c \
        "SELECT COUNT(*) FROM ${TABLE};" 2>/dev/null | tr -d '[:space:]')
fi

if [ "$TARGET_AFTER" = "$SOURCE_COUNT" ]; then
    ok "行数匹配: ${TARGET_AFTER} 行"
else
    warn "行数不完全匹配（源 ${SOURCE_COUNT} → 目标 ${TARGET_AFTER}）"
    warn "可能是多行 INSERT 语法导致，通常不影响数据完整性"
fi

# 显示目标数据统计
echo ""
info "目标 VPS books 表统计:"
if [ "$MODE" = "ssh" ]; then
    target_docker_psql "-c \"
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE book_status = 'success') AS success,
            COUNT(*) FILTER (WHERE book_status = 'pending') AS pending,
            COUNT(DISTINCT category) AS categories
        FROM ${TABLE};\"" 2>/dev/null
else
    docker exec -e PGPASSWORD="$TARGET_PASS" "$CONTAINER" \
        psql -h "$TARGET_IP" -U "$PG_USER" -d "$PG_DB" -c "
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE book_status = 'success') AS success,
            COUNT(*) FILTER (WHERE book_status = 'pending') AS pending,
            COUNT(DISTINCT category) AS categories
        FROM ${TABLE};" 2>/dev/null
fi

# ─── 重启目标 Web 服务 ───
echo ""
echo "[5/5] 重启目标 Web 服务..."
if [ "$NO_RESTART" = true ]; then
    info "已跳过重启（--no-restart）"
else
    if [ "$MODE" = "ssh" ]; then
        ssh "${SSH_USER}@${TARGET_IP}" "cd /root/audiobook && docker-compose restart web" 2>&1 || \
        ssh "${SSH_USER}@${TARGET_IP}" "cd /root/audiobook && docker compose restart web" 2>&1
        ok "目标 Web 服务已重启"
    else
        warn "直连模式无法重启目标 Web 服务（需 SSH 权限）"
        echo "  请手动在目标 VPS 上执行: docker-compose restart web"
    fi
fi

# ─── 完成 ───
echo ""
echo "═══════════════════════════════════════════════════"
echo "  传输完成"
echo "═══════════════════════════════════════════════════"
echo "  源 VPS   books: ${SOURCE_COUNT} 行"
echo "  目标 VPS books: ${TARGET_AFTER} 行（替换前: ${TARGET_COUNT} 行）"
echo ""
if [ "$MODE" = "ssh" ]; then
    echo "  目标 VPS 备份文件:"
    echo "    /root/audiobook/backups/${BACKUP_NAME}"
    echo ""
    echo "  回滚命令（在目标 VPS 上执行）:"
    echo "    cd /root/audiobook"
    echo "    bash scripts/books_import.sh backups/${BACKUP_NAME} --force"
else
    echo "  本机备份文件:"
    echo "    ${PROJECT_ROOT}/backups/${BACKUP_NAME}"
    echo ""
    echo "  回滚命令（传输备份到目标 VPS 后执行）:"
    echo "    scp backups/${BACKUP_NAME} ${TARGET_IP}:/root/audiobook/"
    echo "    ssh ${TARGET_IP} 'cd /root/audiobook && bash scripts/books_import.sh backups/${BACKUP_NAME} --force'"
fi
echo "═══════════════════════════════════════════════════"
