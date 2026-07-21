#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# 项目迁移导出脚本 — 在源 VPS 上运行，导出完整项目状态
# ═══════════════════════════════════════════════════════════════
# 导出内容：
#   1. 数据库完整备份（表结构 + 数据）
#   2. .env 配置文件（含 SECRET_KEY、POSTGRES_PASSWORD 等）
#   3. relay-data 卷（HF 中继配置 relay_config.json）
#   4. output_data 卷（可选，生成的音频文件，可能很大）
#
# 用法：
#   bash scripts/migrate_export.sh                  # 不含输出文件
#   bash scripts/migrate_export.sh --with-output     # 含输出文件
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
CONTAINER_PG="audiobook_postgres"
CONTAINER_WEB="audiobook_web"
CONTAINER_RELAY="hf-vps-relay"
PG_USER="audiobook_app"
PG_DB="audiobook"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
TMP_DIR="/tmp/migration_export_${TIMESTAMP}"
BUNDLE_FILE="${PROJECT_ROOT}/migration_bundle_${TIMESTAMP}.tar.gz"

# ─── 解析参数 ───
WITH_OUTPUT=false
for arg in "$@"; do
    case "$arg" in
        --with-output) WITH_OUTPUT=true ;;
        *) error "未知参数: $arg"; exit 1 ;;
    esac
done

# ─── 辅助函数：获取 Docker 卷名 ───
get_volume_name() {
    local container="$1"
    local mount_dest="$2"
    docker inspect "$container" 2>/dev/null \
        --format "{{ range .Mounts }}{{ if eq .Destination \"${mount_dest}\" }}{{ .Name }}{{ end }}{{ end }}" \
        | tr -d '[:space:]'
}

# ─── 清理临时目录 ───
trap 'rm -rf "$TMP_DIR"' EXIT

# ─── 检查 Docker ───
if ! command -v docker >/dev/null 2>&1; then
    error "未找到 docker 命令，请先安装 Docker"
    exit 1
fi

# ─── 检查 PostgreSQL 容器 ───
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_PG}$"; then
    error "容器 ${CONTAINER_PG} 未运行"
    echo "  请先启动服务: docker-compose up -d"
    exit 1
fi

echo "═══════════════════════════════════════════════════"
echo "  项目迁移导出"
echo "  时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "  主机: $(hostname)"
if [ "$WITH_OUTPUT" = true ]; then
    echo "  含输出文件: 是"
else
    echo "  含输出文件: 否（使用 --with-output 启用）"
fi
echo "═══════════════════════════════════════════════════"

mkdir -p "$TMP_DIR"

# ─── 1. 数据库备份 ───
echo ""
echo "[1/5] 数据库备份..."
info "导出数据库 ${PG_DB}（表结构 + 数据）..."
docker exec "$CONTAINER_PG" pg_dump -U "$PG_USER" -d "$PG_DB" \
    --no-owner --no-privileges | gzip > "${TMP_DIR}/db_backup.sql.gz"
DB_SIZE=$(du -h "${TMP_DIR}/db_backup.sql.gz" | awk '{print $1}')
ok "数据库备份完成: ${DB_SIZE}"

# 记录表行数（写入清单文件）
docker exec "$CONTAINER_PG" psql -U "$PG_USER" -d "$PG_DB" -t -c "
    SELECT relname||' = '||n_live_tup
    FROM pg_stat_user_tables
    ORDER BY relname;
" 2>/dev/null > "${TMP_DIR}/table_counts.txt"

info "表行数统计:"
sed 's/^/  /' "${TMP_DIR}/table_counts.txt"

# ─── 2. .env 配置文件 ───
echo ""
echo "[2/5] 导出 .env 配置..."
if [ -f "${PROJECT_ROOT}/.env" ]; then
    cp "${PROJECT_ROOT}/.env" "${TMP_DIR}/env_backup"
    ok ".env 已导出"
else
    warn ".env 文件不存在，跳过（目标 VPS 需手动配置）"
fi

# ─── 3. relay-data 卷 ───
echo ""
echo "[3/5] 导出 relay-data 卷（HF 中继配置）..."
RELAY_VOL=$(get_volume_name "$CONTAINER_RELAY" "/data")
if [ -n "$RELAY_VOL" ]; then
    info "卷名: ${RELAY_VOL}"
    docker run --rm \
        -v "${RELAY_VOL}:/volume_data:ro" \
        -v "${TMP_DIR}:/backup" \
        alpine tar czf /backup/relay_data.tar.gz -C /volume_data .
    RELAY_SIZE=$(du -h "${TMP_DIR}/relay_data.tar.gz" | awk '{print $1}')
    ok "relay-data 导出完成: ${RELAY_SIZE}"
else
    warn "未找到 relay-data 卷（容器 ${CONTAINER_RELAY} 可能未启动），跳过"
fi

# ─── 4. output_data 卷（可选）───
echo ""
if [ "$WITH_OUTPUT" = true ]; then
    echo "[4/5] 导出 output_data 卷（音频输出文件）..."
    OUTPUT_VOL=$(get_volume_name "$CONTAINER_WEB" "/data/output")
    if [ -n "$OUTPUT_VOL" ]; then
        info "卷名: ${OUTPUT_VOL}"
        # 显示卷大小
        VOL_SIZE=$(docker run --rm -v "${OUTPUT_VOL}:/data:ro" alpine du -sh /data 2>/dev/null | awk '{print $1}')
        info "卷大小: ${VOL_SIZE}"
        warn "正在压缩导出，可能需要较长时间..."
        docker run --rm \
            -v "${OUTPUT_VOL}:/volume_data:ro" \
            -v "${TMP_DIR}:/backup" \
            alpine tar czf /backup/output_data.tar.gz -C /volume_data .
        OUTPUT_SIZE=$(du -h "${TMP_DIR}/output_data.tar.gz" | awk '{print $1}')
        ok "output_data 导出完成: ${OUTPUT_SIZE}"
    else
        warn "未找到 output_data 卷（容器 ${CONTAINER_WEB} 可能未启动），跳过"
    fi
else
    echo "[4/5] 跳过 output_data 卷（使用 --with-output 启用）"
fi

# ─── 5. 创建清单和打包 ───
echo ""
echo "[5/5] 创建迁移包..."

cat > "${TMP_DIR}/manifest.txt" << MANIFEST_EOF
MIGRATION_VERSION=1.0
EXPORT_TIMESTAMP=${TIMESTAMP}
SOURCE_HOSTNAME=$(hostname)
INCLUDE_OUTPUT_DATA=${WITH_OUTPUT}
MANIFEST_EOF
echo "" >> "${TMP_DIR}/manifest.txt"
echo "TABLE_COUNTS_BEGIN" >> "${TMP_DIR}/manifest.txt"
cat "${TMP_DIR}/table_counts.txt" >> "${TMP_DIR}/manifest.txt"
echo "TABLE_COUNTS_END" >> "${TMP_DIR}/manifest.txt"
rm -f "${TMP_DIR}/table_counts.txt"

tar czf "$BUNDLE_FILE" -C "$TMP_DIR" .
BUNDLE_SIZE=$(du -h "$BUNDLE_FILE" | awk '{print $1}')

echo ""
echo "═══════════════════════════════════════════════════"
echo "  导出完成"
echo "═══════════════════════════════════════════════════"
echo ""
echo "  迁移包: ${BUNDLE_FILE}"
echo "  大小:   ${BUNDLE_SIZE}"
echo ""
echo "  ─── 传输到目标 VPS ───"
echo ""
echo "  方法 1（从源 VPS 推送）:"
echo "    scp ${BUNDLE_FILE} root@TARGET_VPS_IP:/root/audiobook/"
echo ""
echo "  方法 2（在目标 VPS 拉取）:"
echo "    scp root@$(hostname -I | awk '{print $1}'):${BUNDLE_FILE} /root/audiobook/"
echo ""
echo "  ─── 在目标 VPS 上导入 ───"
echo ""
echo "    cd /root/audiobook"
echo "    bash scripts/migrate_import.sh $(basename "$BUNDLE_FILE")"
echo ""
echo "═══════════════════════════════════════════════════"
