#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════�?# 从云盘下载备份并恢复数据�?# ══════════════════════════════════════════════════════════════�?# 用法�?#   bash scripts/db_backup/db_restore_cloud.sh                          # 交互式选最新备份（默认 gdrive�?#   bash scripts/db_backup/db_restore_cloud.sh --list                    # 仅列出云端备�?#   bash scripts/db_backup/db_restore_cloud.sh --source mega             # �?Mega 下载
#   bash scripts/db_backup/db_restore_cloud.sh --file audiobook_backup_20260724_031700.sql.gz  # 指定文件
#   bash scripts/db_backup/db_restore_cloud.sh --source mega --file xxx.sql.gz --with-env
#
# 警告：恢复会覆盖当前数据库中的所有数据！
# ══════════════════════════════════════════════════════════════�?
set -euo pipefail

export TZ='Asia/Shanghai'
export HOME="${HOME:-/root}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "${PROJECT_ROOT}"

# ─── 颜色输出 ───
info() { echo -e "\033[36m[INFO]\033[0m $*"; }
ok()   { echo -e "\033[32m[OK]\033[0m $*"; }
warn() { echo -e "\033[33m[WARN]\033[0m $*"; }
error(){ echo -e "\033[31m[ERROR]\033[0m $*"; }

# ─── 配置�?───
CONTAINER="audiobook_postgres"
PG_USER="audiobook_app"
PG_DB="audiobook"
BACKUP_DIR="${PROJECT_ROOT}/backups"

RCLONE_GDRIVE="gdrive"
RCLONE_MEGA="mega"
RCLONE_DEST_DIR="audiobook_backup"
# ══════════════════════════════════════════════════════════════�?
# ─── 检�?docker compose 命令 ───
if docker compose version >/dev/null 2>&1; then
    DC="docker compose"
elif docker-compose version >/dev/null 2>&1; then
    DC="docker-compose"
else
    error "未找�?docker compose 命令"
    exit 1
fi

# ─── 解析参数 ───
SOURCE="gdrive"
LIST_ONLY=false
SPECIFIED_FILE=""
WITH_ENV=false

while [ $# -gt 0 ]; do
    case "$1" in
        --source)
            SOURCE="$2"; shift 2 ;;
        --list)
            LIST_ONLY=true; shift ;;
        --file)
            SPECIFIED_FILE="$2"; shift 2 ;;
        --with-env)
            WITH_ENV=true; shift ;;
        -h|--help)
            echo "用法: bash scripts/db_backup/db_restore_cloud.sh [选项]"
            echo ""
            echo "选项:"
            echo "  --source gdrive|mega   从指定云盘下载（默认 gdrive�?
            echo "  --list                 仅列出云端备份，不恢�?
            echo "  --file <文件�?        指定备份文件�?
            echo "  --with-env             同时从云端恢�?.env 配置"
            exit 0
            ;;
        *)
            error "未知参数: $1"
            exit 1
            ;;
    esac
done

# 确定远程名称
case "$SOURCE" in
    gdrive|google) REMOTE="$RCLONE_GDRIVE" ;;
    mega)          REMOTE="$RCLONE_MEGA" ;;
    *)
        error "不支持的�? ${SOURCE}，可�?gdrive �?mega"
        exit 1
        ;;
esac

REMOTE_PATH="${REMOTE}:${RCLONE_DEST_DIR}/"

# ─── 检�?rclone ───
if ! command -v rclone >/dev/null 2>&1; then
    error "未安�?rclone，请先安�? curl https://rclone.org/install.sh | bash"
    exit 1
fi

if ! rclone listremotes 2>/dev/null | grep -q "^${REMOTE}:$"; then
    error "rclone 远程 '${REMOTE}' 未配置，请运�?rclone config"
    exit 1
fi

echo "══════════════════════════════════════════════════�?
echo "  数据库云盘恢�?
echo "  云盘: ${SOURCE}"
echo "  时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "══════════════════════════════════════════════════�?

# ─── 1. 列出云端备份 ───
echo ""
echo "[1/5] 获取云端备份列表..."
REMOTE_FILES=$(rclone lsf "${REMOTE_PATH}" --log-level ERROR 2>/dev/null | grep "audiobook_backup_.*\.sql\.gz$" | sort -r)

if [ -z "$REMOTE_FILES" ]; then
    error "云端 ${SOURCE} 上没有找到备份文�?
    echo "  路径: ${REMOTE_PATH}"
    exit 1
fi

echo "云端备份文件（按时间倒序�?"
echo "┌──────────────────────────────────────────────────────�?
i=1
while IFS= read -r line; do
    FILE_SIZE=$(rclone size "${REMOTE_PATH}${line}" --json 2>/dev/null | grep -o '"bytes":[0-9]*' | cut -d: -f2)
    FILE_SIZE_HR=$(numfmt --to=iec --suffix=B "$FILE_SIZE" 2>/dev/null || echo "${FILE_SIZE}B")
    echo "�?${i}. ${line} (${FILE_SIZE_HR})"
    echo "$line" > "/tmp/audiobook_backup_file_${i}"
    i=$((i + 1))
done <<< "$REMOTE_FILES"
echo "└──────────────────────────────────────────────────────�?
TOTAL_FILES=$((i - 1))

# 检查云端是否有 env_backup
CLOUD_HAS_ENV=false
if rclone lsf "${REMOTE_PATH}" --log-level ERROR 2>/dev/null | grep -q "^env_backup$"; then
    CLOUD_HAS_ENV=true
    info "云端包含 .env 配置备份（使�?--with-env 恢复�?
fi

# 仅列出模�?if [ "$LIST_ONLY" = true ]; then
    echo ""
    info "仅列出模式，退�?
    rm -f /tmp/audiobook_backup_file_*
    exit 0
fi

# ─── 2. 选择备份文件 ───
if [ -n "$SPECIFIED_FILE" ]; then
    TARGET_FILE="$SPECIFIED_FILE"
    info "使用指定文件: ${TARGET_FILE}"
else
    echo ""
    read -p "选择要恢复的文件编号（回车默认选第 1 �?最新）: " FILE_NUM
    FILE_NUM=${FILE_NUM:-1}
    TARGET_FILE=$(cat "/tmp/audiobook_backup_file_${FILE_NUM}" 2>/dev/null || echo "")
    if [ -z "$TARGET_FILE" ]; then
        error "无效的文件编�?
        rm -f /tmp/audiobook_backup_file_*
        exit 1
    fi
    info "选择: ${TARGET_FILE}"
fi
rm -f /tmp/audiobook_backup_file_*

# ─── 确认恢复 ───
echo ""
warn "恢复将覆盖容器内现有的数据库数据�?
read -p "确认恢复？输�?yes 继续: " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "已取�?
    exit 0
fi

# ─── 3. 检查容�?+ 下载备份 ───
echo ""
echo "[2/5] 检查容�?.."
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    error "容器 ${CONTAINER} 未运行，请先启动: ${DC} up -d postgres"
    exit 1
fi
ok "容器运行�?

echo ""
echo "[3/5] �?${SOURCE} 下载备份文件..."
mkdir -p "$BACKUP_DIR"
DOWNLOAD_FILE="${BACKUP_DIR}/${TARGET_FILE}"

rclone copy "${REMOTE_PATH}${TARGET_FILE}" "${BACKUP_DIR}/" \
    --transfers 4 \
    --checkers 8 \
    --log-level INFO \
    --progress 2>&1

if [ ! -f "$DOWNLOAD_FILE" ]; then
    error "下载失败，文件不存在"
    exit 1
fi
DL_SIZE=$(du -h "$DOWNLOAD_FILE" | awk '{print $1}')
ok "下载完成: ${DOWNLOAD_FILE} (${DL_SIZE})"

# ─── 4. 恢复数据�?───
echo ""
echo "[4/5] 恢复数据�?.."

info "停止 Web 和中继服务（释放数据库连接）..."
$DC stop web vps-relay 2>/dev/null || true

info "断开所有数据库连接..."
docker exec "$CONTAINER" psql -U "$PG_USER" -d postgres -c "
    SELECT pg_terminate_backend(pid) FROM pg_stat_activity
    WHERE datname = '${PG_DB}' AND pid <> pg_backend_pid();
" 2>/dev/null || true

info "重建数据�?.."
docker exec "$CONTAINER" psql -U "$PG_USER" -d postgres -c "DROP DATABASE IF EXISTS ${PG_DB};" || {
    error "无法删除数据库（可能仍有活跃连接�?
    echo "  尝试: ${DC} stop web vps-relay"
    exit 1
}
docker exec "$CONTAINER" psql -U "$PG_USER" -d postgres -c "CREATE DATABASE ${PG_DB};"

info "导入数据（可能需要一些时间）..."
set +e
gunzip -c "$DOWNLOAD_FILE" | docker exec -i "$CONTAINER" psql -U "$PG_USER" -d "$PG_DB" 2>&1 | grep -v "^$"
IMPORT_STATUS=${PIPESTATUS[1]}
set -e

if [ "$IMPORT_STATUS" -ne 0 ]; then
    error "数据库导入过程中出现错误"
    exit 1
fi
ok "数据库恢复完�?

# 更新统计信息
docker exec "$CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -c "ANALYZE;" >/dev/null 2>&1 || true

# ─── 4.5 恢复 .env（可选）───
if [ "$WITH_ENV" = true ] && [ "$CLOUD_HAS_ENV" = true ]; then
    echo ""
    info "从云端下�?.env 备份..."
    if [ -f "${PROJECT_ROOT}/.env" ]; then
        cp "${PROJECT_ROOT}/.env" "${PROJECT_ROOT}/.env.bak_$(date +%Y%m%d_%H%M%S)"
        info "已备份现�?.env"
    fi
    rclone copy "${REMOTE_PATH}env_backup" "${BACKUP_DIR}/" \
        --log-level ERROR 2>&1
    if [ -f "${BACKUP_DIR}/env_backup" ]; then
        cp "${BACKUP_DIR}/env_backup" "${PROJECT_ROOT}/.env"
        ok ".env 已恢�?
        warn "�?VPS IP 已变更，需修改 .env 中的 BASE_URL"
    else
        warn "云端 .env 下载失败"
    fi
fi

# ─── 5. 启动服务并验�?───
echo ""
echo "[5/5] 启动服务并验�?.."
$DC up -d

info "等待 Web 服务就绪..."
for i in $(seq 1 20); do
    if curl -sf http://localhost:59386/ >/dev/null 2>&1; then
        ok "Web 服务就绪"
        break
    fi
    if [ $i -eq 20 ]; then
        warn "Web 服务未就�?
        echo "  查看日志: ${DC} logs web"
    fi
    sleep 2
done

# 验证表行�?echo ""
info "数据库表行数:"
docker exec "$CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -t -c "
    SELECT relname||' = '||n_live_tup
    FROM pg_stat_user_tables
    ORDER BY n_live_tup DESC;
" 2>/dev/null | sed 's/^/  /'

echo ""
echo "══════════════════════════════════════════════════�?
echo "  恢复完成"
echo "  备份文件: ${TARGET_FILE} (${DL_SIZE})"
echo "  来源: ${SOURCE}"
echo ""
echo "  建议检�?"
echo "    - YouTube 频道 OAuth 状�?
echo "    - 全局设置是否完整"
echo "    - HF 中继 Worker 配置"
echo "══════════════════════════════════════════════════�?
