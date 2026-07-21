#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# 项目迁移导入脚本 — 在目标 VPS 上运行，从迁移包恢复完整项目
# ═══════════════════════════════════════════════════════════════
# 用法：
#   bash scripts/migrate_import.sh <迁移包路径> [--force]
#   bash scripts/migrate_import.sh migration_bundle_20260721_120000.tar.gz
#   bash scripts/migrate_import.sh migration_bundle_20260721_120000.tar.gz --force
#
# 前置条件：
#   1. 已克隆项目仓库到目标 VPS
#   2. 已安装 Docker + Docker Compose
#   3. 迁移包已传输到目标 VPS
#
# 警告：此脚本会删除目标 VPS 上的所有现有数据！
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
TMP_DIR="/tmp/migration_import_${TIMESTAMP}"

# ─── 辅助函数：获取 Docker 卷名 ───
get_volume_name() {
    local container="$1"
    local mount_dest="$2"
    docker inspect "$container" 2>/dev/null \
        --format "{{ range .Mounts }}{{ if eq .Destination \"${mount_dest}\" }}{{ .Name }}{{ end }}{{ end }}" \
        | tr -d '[:space:]'
}

# ─── 解析参数 ───
BUNDLE_FILE=""
FORCE=false
while [ $# -gt 0 ]; do
    case "$1" in
        --force) FORCE=true; shift ;;
        *) BUNDLE_FILE="$1"; shift ;;
    esac
done

if [ -z "$BUNDLE_FILE" ]; then
    echo "用法: bash scripts/migrate_import.sh <迁移包路径> [--force]"
    echo "示例: bash scripts/migrate_import.sh migration_bundle_20260721_120000.tar.gz"
    echo "      bash scripts/migrate_import.sh migration_bundle_20260721_120000.tar.gz --force"
    exit 1
fi

# 支持相对路径
if [[ "$BUNDLE_FILE" != /* ]]; then
    BUNDLE_FILE="${PROJECT_ROOT}/${BUNDLE_FILE}"
fi

if [ ! -f "$BUNDLE_FILE" ]; then
    error "迁移包不存在: ${BUNDLE_FILE}"
    exit 1
fi

# ─── 检查 Docker ───
if ! command -v docker >/dev/null 2>&1; then
    error "未找到 docker 命令，请先安装 Docker"
    exit 1
fi

# ─── 检测 docker compose 命令 ───
if docker compose version >/dev/null 2>&1; then
    DC="docker compose"
elif docker-compose version >/dev/null 2>&1; then
    DC="docker-compose"
else
    error "未找到 docker compose 命令，请安装 Docker Compose"
    exit 1
fi

echo "═══════════════════════════════════════════════════"
echo "  项目迁移导入"
echo "  时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "  主机: $(hostname)"
echo "  迁移包: ${BUNDLE_FILE}"
echo "  Docker Compose: ${DC}"
echo "═══════════════════════════════════════════════════"

# ─── 确认 ───
if [ "$FORCE" = false ]; then
    echo ""
    warn "此操作将删除目标 VPS 上的所有现有数据！"
    echo "  包括：数据库、输出文件、中继配置"
    echo ""
    read -p "  确认继续？输入 yes: " CONFIRM
    if [ "$CONFIRM" != "yes" ]; then
        echo "  已取消"
        exit 0
    fi
fi

mkdir -p "$TMP_DIR"

# ─── 1. 解包迁移包 ───
echo ""
echo "[1/9] 解包迁移包..."
tar xzf "$BUNDLE_FILE" -C "$TMP_DIR"
ok "解包完成"

# 读取清单
INCLUDE_OUTPUT=false
if [ -f "${TMP_DIR}/manifest.txt" ]; then
    if grep -q "INCLUDE_OUTPUT_DATA=true" "${TMP_DIR}/manifest.txt"; then
        INCLUDE_OUTPUT=true
    fi
    info "迁移包清单:"
    sed 's/^/  /' "${TMP_DIR}/manifest.txt"
fi

# ─── 2. 恢复 .env ───
echo ""
echo "[2/9] 恢复 .env 配置..."
if [ -f "${TMP_DIR}/env_backup" ]; then
    # 备份现有 .env
    if [ -f "${PROJECT_ROOT}/.env" ]; then
        cp "${PROJECT_ROOT}/.env" "${PROJECT_ROOT}/.env.bak_${TIMESTAMP}"
        info "已备份现有 .env → .env.bak_${TIMESTAMP}"
    fi
    cp "${TMP_DIR}/env_backup" "${PROJECT_ROOT}/.env"
    ok ".env 已恢复"
    # 显示 BASE_URL 提醒
    BASE_URL=$(grep "^BASE_URL=" "${PROJECT_ROOT}/.env" 2>/dev/null | cut -d'=' -f2- || echo "")
    if [ -n "$BASE_URL" ]; then
        warn "当前 BASE_URL: ${BASE_URL}"
        warn "如 VPS IP 已变更，迁移后需修改 .env 中的 BASE_URL"
    fi
else
    warn "迁移包中无 .env，将使用 .env.example"
    if [ ! -f "${PROJECT_ROOT}/.env" ]; then
        cp "${PROJECT_ROOT}/.env.example" "${PROJECT_ROOT}/.env"
        error "请编辑 .env 配置后重新运行: nano .env"
        exit 1
    fi
fi

# ─── 3. 清理旧环境 ───
echo ""
echo "[3/9] 清理旧环境..."
info "停止并删除现有容器和数据卷..."
$DC down -v 2>/dev/null || true
ok "旧环境已清理"

# ─── 4. 构建并启动服务 ───
echo ""
echo "[4/9] 构建并启动服务..."
info "首次部署可能需要 5-10 分钟（下载镜像 + 构建）..."
$DC up -d --build

# ─── 5. 等待 PostgreSQL 就绪 ───
echo ""
echo "[5/9] 等待 PostgreSQL 就绪..."
for i in $(seq 1 30); do
    if docker exec "$CONTAINER_PG" pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1; then
        ok "PostgreSQL 已就绪"
        break
    fi
    if [ $i -eq 30 ]; then
        error "PostgreSQL 未就绪，请检查: $DC logs postgres"
        echo "  临时目录保留供调试: ${TMP_DIR}"
        exit 1
    fi
    sleep 2
done

# ─── 6. 恢复数据库 ───
echo ""
echo "[6/9] 恢复数据库..."
if [ ! -f "${TMP_DIR}/db_backup.sql.gz" ]; then
    error "迁移包中无数据库备份文件"
    echo "  临时目录保留供调试: ${TMP_DIR}"
    exit 1
fi

# 停止 web 和 vps-relay（释放数据库连接）
info "停止 Web 和中继服务..."
$DC stop web vps-relay 2>/dev/null || true

# 断开所有数据库连接
info "断开数据库连接..."
docker exec "$CONTAINER_PG" psql -U "$PG_USER" -d postgres -c "
    SELECT pg_terminate_backend(pid) FROM pg_stat_activity
    WHERE datname = '${PG_DB}' AND pid <> pg_backend_pid();
" 2>/dev/null || true

# 删除并重建数据库
info "重建数据库..."
docker exec "$CONTAINER_PG" psql -U "$PG_USER" -d postgres -c "DROP DATABASE IF EXISTS ${PG_DB};" || {
    error "无法删除数据库（可能仍有活跃连接）"
    echo "  尝试: $DC stop web vps-relay && docker exec $CONTAINER_PG psql -U $PG_USER -d postgres -c 'DROP DATABASE audiobook;'"
    echo "  临时目录保留供调试: ${TMP_DIR}"
    exit 1
}
docker exec "$CONTAINER_PG" psql -U "$PG_USER" -d postgres -c "CREATE DATABASE ${PG_DB};" || {
    error "无法创建数据库"
    echo "  临时目录保留供调试: ${TMP_DIR}"
    exit 1
}

# 导入数据
info "导入数据库备份..."
DB_SIZE=$(du -h "${TMP_DIR}/db_backup.sql.gz" | awk '{print $1}')
info "备份大小: ${DB_SIZE}"
set +e
gunzip -c "${TMP_DIR}/db_backup.sql.gz" | docker exec -i "$CONTAINER_PG" psql -U "$PG_USER" -d "$PG_DB" 2>&1 | grep -v "^$"
IMPORT_STATUS=${PIPESTATUS[1]}
set -e

if [ "$IMPORT_STATUS" -ne 0 ]; then
    error "数据库导入过程中出现错误"
    echo "  临时目录保留供调试: ${TMP_DIR}"
    exit 1
fi
ok "数据库恢复完成"

# ─── 7. 恢复 relay-data 卷 ───
echo ""
echo "[7/9] 恢复 relay-data 卷..."
if [ -f "${TMP_DIR}/relay_data.tar.gz" ]; then
    RELAY_VOL=$(get_volume_name "$CONTAINER_RELAY" "/data")
    if [ -n "$RELAY_VOL" ]; then
        info "卷名: ${RELAY_VOL}"
        docker run --rm \
            -v "${RELAY_VOL}:/volume_data" \
            -v "${TMP_DIR}:/backup" \
            alpine sh -c "rm -rf /volume_data/* 2>/dev/null; tar xzf /backup/relay_data.tar.gz -C /volume_data"
        ok "relay-data 恢复完成"
    else
        warn "未找到 relay-data 卷（容器 ${CONTAINER_RELAY} 不存在），跳过"
    fi
else
    info "迁移包中无 relay_data.tar.gz，跳过"
fi

# ─── 8. 恢复 output_data 卷（如果包含）───
echo ""
echo "[8/9] 恢复 output_data 卷..."
if [ "$INCLUDE_OUTPUT" = true ] && [ -f "${TMP_DIR}/output_data.tar.gz" ]; then
    OUTPUT_VOL=$(get_volume_name "$CONTAINER_WEB" "/data/output")
    if [ -n "$OUTPUT_VOL" ]; then
        info "卷名: ${OUTPUT_VOL}"
        OUTPUT_SIZE=$(du -h "${TMP_DIR}/output_data.tar.gz" | awk '{print $1}')
        info "恢复大小: ${OUTPUT_SIZE}"
        warn "正在解压恢复，可能需要较长时间..."
        docker run --rm \
            -v "${OUTPUT_VOL}:/volume_data" \
            -v "${TMP_DIR}:/backup" \
            alpine sh -c "rm -rf /volume_data/* 2>/dev/null; tar xzf /backup/output_data.tar.gz -C /volume_data"
        ok "output_data 恢复完成"
    else
        warn "未找到 output_data 卷（容器 ${CONTAINER_WEB} 不存在），跳过"
    fi
else
    info "迁移包中无 output_data.tar.gz，跳过"
fi

# ─── 9. 启动服务并验证 ───
echo ""
echo "[9/9] 启动服务并验证..."
$DC up -d

# 等待 Web 服务就绪
info "等待 Web 服务就绪..."
for i in $(seq 1 20); do
    if curl -sf http://localhost:59386/ >/dev/null 2>&1; then
        ok "Web 服务就绪"
        break
    fi
    if [ $i -eq 20 ]; then
        warn "Web 服务未就绪，请检查: $DC logs web"
    fi
    sleep 2
done

# 等待 VPS 中继就绪
for i in $(seq 1 10); do
    if curl -sf http://localhost:38080/api/status >/dev/null 2>&1; then
        ok "HF 中继调度器就绪"
        break
    fi
    if [ $i -eq 10 ]; then
        warn "HF 中继调度器未就绪，请检查: $DC logs vps-relay"
    fi
    sleep 2
done

# 验证表行数
echo ""
info "数据库表行数:"
docker exec "$CONTAINER_PG" psql -U "$PG_USER" -d "$PG_DB" -t -c "
    SELECT relname||' = '||n_live_tup
    FROM pg_stat_user_tables
    ORDER BY n_live_tup DESC;
" 2>/dev/null | sed 's/^/  /'

# 服务状态
echo ""
info "服务状态:"
$DC ps

# 对比清单中的表行数
if [ -f "${TMP_DIR}/manifest.txt" ]; then
    echo ""
    info "源 VPS 表行数（来自清单）:"
    sed -n '/TABLE_COUNTS_BEGIN/,/TABLE_COUNTS_END/p' "${TMP_DIR}/manifest.txt" \
        | grep -v "TABLE_COUNTS_" | sed 's/^/  /'
fi

# 清理临时目录
rm -rf "$TMP_DIR"

echo ""
echo "═══════════════════════════════════════════════════"
echo "  迁移导入完成"
echo "═══════════════════════════════════════════════════"
echo ""
echo "  ─── 迁移后必做 ───"
echo ""
echo "  1. 修改 BASE_URL（如 VPS IP 已变更）:"
echo "     nano .env"
echo "     # 修改 BASE_URL=http://NEW_VPS_IP:59386"
echo ""
echo "  2. 更新 Google Cloud Console OAuth 重定向 URI:"
echo "     https://console.cloud.google.com/apis/credentials"
echo "     # 添加: http://NEW_VPS_IP:59386/api/oauth/callback"
echo ""
echo "  3. 重启服务使配置生效:"
echo "     ${DC} restart web"
echo ""
echo "  4. 验证 YouTube 频道 OAuth 状态"
echo "     # 在 Web 界面检查频道授权是否有效"
echo ""
echo "  ─── 服务地址 ───"
echo ""
echo "  Web 管理系统:  http://$(hostname -I | awk '{print $1}'):59386"
echo "  HF 中继面板:   http://$(hostname -I | awk '{print $1}'):38080"
echo ""
echo "═══════════════════════════════════════════════════"
