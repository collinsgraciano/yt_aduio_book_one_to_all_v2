#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════�?# 数据库定时备�?+ 双云盘上传脚�?# ══════════════════════════════════════════════════════════════�?# 功能：pg_dump 全库 �?gzip 压缩 �?保留最�?7 �?�?上传 Google Drive + Mega
#
# 用法�?#   bash scripts/db_backup/db_backup_cloud.sh              # 手动执行
#
# 定时任务（crontab -e）：
#   17 3 * * * cd /root/audiobook && bash scripts/db_backup/db_backup_cloud.sh >> backups/cron.log 2>&1
#
# 前置条件�?#   1. 已安�?rclone 并配�?gdrive、mega 两个远程
#   2. 已运�?bash scripts/db_backup/db_cloud_test.sh 验证连通�?# ══════════════════════════════════════════════════════════════�?
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
KEEP_COUNT=7
LOG="${BACKUP_DIR}/cloud_sync.log"

# rclone 远程配置
RCLONE_GDRIVE="gdrive"
RCLONE_MEGA="mega"
RCLONE_DEST_DIR="audiobook_backup"
# ══════════════════════════════════════════════════════════════�?
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/audiobook_backup_${TIMESTAMP}.sql.gz"
ENV_BACKUP="${BACKUP_DIR}/env_backup"

mkdir -p "$BACKUP_DIR"

echo "══════════════════════════════════════════════════�?
echo "  数据库备�?+ 云盘同步"
echo "  时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "══════════════════════════════════════════════════�?

# ─── 日志函数 ───
log() {
    echo "[$(date '+%F %T')] $1" >> "$LOG"
}

# ─── 检�?rclone ───
RCLONE_OK=true
if ! command -v rclone >/dev/null 2>&1; then
    warn "未安�?rclone，将仅执行本地备份（跳过云盘上传�?
    RCLONE_OK=false
else
    for remote in "$RCLONE_GDRIVE" "$RCLONE_MEGA"; do
        if ! rclone listremotes 2>/dev/null | grep -q "^${remote}:$"; then
            warn "rclone 远程 '${remote}' 未配置，将跳过该云盘"
        fi
    done
fi

# ─── 1. 检查容�?───
echo ""
echo "[1/6] 检查容�?.."
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    error "容器 ${CONTAINER} 未运行，跳过备份"
    log "ERROR: 容器 ${CONTAINER} 未运行，备份失败"
    exit 1
fi
ok "容器运行�?

# ─── 2. 数据库备�?───
echo ""
echo "[2/6] 数据库备�?.."
info "导出数据�?${PG_DB}（表结构 + 全部数据�?.."
if ! docker exec "$CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" \
    --no-owner --no-privileges | gzip > "$BACKUP_FILE"; then
    rm -f "$BACKUP_FILE"
    error "备份失败，已删除不完整的备份文件"
    log "ERROR: 数据库备份失�?
    exit 1
fi

if [ ! -s "$BACKUP_FILE" ]; then
    rm -f "$BACKUP_FILE"
    error "备份文件为空，备份失�?
    log "ERROR: 备份文件为空"
    exit 1
fi

DB_SIZE=$(du -h "$BACKUP_FILE" | awk '{print $1}')
ok "备份完成: $(basename "$BACKUP_FILE") (${DB_SIZE})"
log "备份完成: ${BACKUP_FILE} (${DB_SIZE})"

# 表行数统�?docker exec "$CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -t -c "
    SELECT relname||' = '||n_live_tup
    FROM pg_stat_user_tables
    ORDER BY n_live_tup DESC;
" 2>/dev/null | sed 's/^/  /'

# ─── 3. 备份 .env 配置 ───
echo ""
echo "[3/6] 备份 .env 配置..."
if [ -f "${PROJECT_ROOT}/.env" ]; then
    cp "${PROJECT_ROOT}/.env" "$ENV_BACKUP"
    ok ".env 已备�?
    log ".env 备份完成"
else
    warn ".env 文件不存在，跳过"
fi

# ─── 4. 清理本地旧备份（保留最�?KEEP_COUNT 个）───
echo ""
echo "[4/6] 清理本地旧备份（保留最�?${KEEP_COUNT} 个）..."
mapfile -t LOCAL_OLD < <(ls -1t "${BACKUP_DIR}"/audiobook_backup_*.sql.gz 2>/dev/null | tail -n +$((KEEP_COUNT + 1)))
if [ ${#LOCAL_OLD[@]} -gt 0 ]; then
    for file in "${LOCAL_OLD[@]}"; do
        rm -f "$file"
        info "已删�? $(basename "$file")"
    done
else
    info "无需清理"
fi
LOCAL_COUNT=$(ls -1 "${BACKUP_DIR}"/audiobook_backup_*.sql.gz 2>/dev/null | wc -l)
LOCAL_SIZE=$(du -sh "$BACKUP_DIR" | awk '{print $1}')
ok "本地: ${LOCAL_COUNT} 个备�? 总占�?${LOCAL_SIZE}"

# ─── 5. 上传到云�?───
echo ""
echo "[5/6] 云盘上传..."

upload_to_cloud() {
    local remote_name="$1"
    local remote_path="${remote_name}:${RCLONE_DEST_DIR}/"

    if ! rclone listremotes 2>/dev/null | grep -q "^${remote_name}:$"; then
        warn "远程 '${remote_name}' 未配置，跳过"
        return 0
    fi

    info "上传�?${remote_name}..."

    # 上传最新备份文�?    rclone copy "$BACKUP_FILE" "$remote_path" \
        --transfers 4 \
        --checkers 8 \
        --log-level ERROR 2>&1 || {
        warn "${remote_name}: 备份文件上传失败"
        log "WARN: ${remote_name} 上传失败: ${BACKUP_FILE}"
        return 1
    }

    # 上传 .env 备份
    if [ -f "$ENV_BACKUP" ]; then
        rclone copy "$ENV_BACKUP" "$remote_path" \
            --transfers 4 \
            --checkers 8 \
            --log-level ERROR 2>&1 || {
            warn "${remote_name}: .env 上传失败"
        }
    fi

    ok "${remote_name} 上传完成"
    log "${remote_name} 上传完成"

    # 清理云端旧备份（保留最�?KEEP_COUNT 个）
    info "清理 ${remote_name} 旧备份（保留最�?${KEEP_COUNT} 个）..."
    mapfile -t CLOUD_OLD < <(
        rclone lsf "${remote_path}" --log-level ERROR 2>/dev/null \
        | grep "audiobook_backup_.*\.sql\.gz$" \
        | sort -r \
        | tail -n +$((KEEP_COUNT + 1))
    )
    if [ ${#CLOUD_OLD[@]} -gt 0 ]; then
        for old_file in "${CLOUD_OLD[@]}"; do
            rclone deletefile "${remote_path}${old_file}" --log-level ERROR 2>/dev/null || true
            info "云端已删�? ${old_file}"
        done
    else
        info "云端无需清理"
    fi
}

if [ "$RCLONE_OK" = true ]; then
    upload_to_cloud "$RCLONE_GDRIVE"
    upload_to_cloud "$RCLONE_MEGA"
else
    warn "rclone 不可用，跳过云盘上传"
    log "WARN: rclone 未安装，跳过云盘上传"
fi

# ─── 6. 完成 ───
echo ""
echo "[6/6] 完成"
echo ""
echo "══════════════════════════════════════════════════�?
echo "  备份完成 �?$(date '+%H:%M:%S')"
echo "  本地文件: ${BACKUP_FILE} (${DB_SIZE})"
echo "  本地保留: ${LOCAL_COUNT} �?
echo "  云盘目录: ${RCLONE_DEST_DIR}/"
echo "  同步日志: ${LOG}"
echo "══════════════════════════════════════════════════�?
log "全部完成"
