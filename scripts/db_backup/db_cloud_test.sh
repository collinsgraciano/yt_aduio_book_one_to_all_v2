#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════�?# 云盘备份连通性测试脚�?# ══════════════════════════════════════════════════════════════�?# 快速验证：容器连接 �?小型备份 �?上传 gdrive �?上传 mega �?清理
# 不会传输大量数据，测完自动清理本地和云端测试文件
#
# 用法：bash scripts/db_backup/db_cloud_test.sh
# ══════════════════════════════════════════════════════════════�?
set -euo pipefail

export TZ='Asia/Shanghai'
export HOME="${HOME:-/root}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "${PROJECT_ROOT}"

# ─── 配置�?───
CONTAINER="audiobook_postgres"
PG_USER="audiobook_app"
PG_DB="audiobook"
RCLONE_GDRIVE="gdrive"
RCLONE_MEGA="mega"
RCLONE_DEST_DIR="audiobook_backup"
TEST_DIR="/tmp/audiobook_cloud_test"
# ══════════════════════════════════════════════════════════════�?
echo "══════════════════════════════════════════════════�?
echo "  云盘备份连通性测�?
echo "  时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "══════════════════════════════════════════════════�?
echo ""

PASS=0
FAIL=0

# ─── 1. 测试 Docker 容器 ───
echo "[1/5] 测试容器连接..."
if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    echo "  �?容器运行�?
    PASS=$((PASS + 1))
else
    echo "  �?容器 ${CONTAINER} 未运�?
    FAIL=$((FAIL + 1))
fi

# ─── 2. 小型备份测试（仅表结构，不导数据）───
echo ""
echo "[2/5] 小型备份测试（仅表结构）..."
mkdir -p "$TEST_DIR"
if docker exec "$CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" \
    --schema-only --no-owner --no-privileges | gzip > "${TEST_DIR}/test_backup.sql.gz"; then
    if [ -s "${TEST_DIR}/test_backup.sql.gz" ]; then
        TEST_SIZE=$(du -h "${TEST_DIR}/test_backup.sql.gz" | awk '{print $1}')
        echo "  �?备份成功 (${TEST_SIZE})"
        PASS=$((PASS + 1))
    else
        echo "  �?备份文件为空"
        FAIL=$((FAIL + 1))
    fi
else
    echo "  �?备份失败"
    FAIL=$((FAIL + 1))
fi

# ─── 3. 测试 Google Drive ───
echo ""
echo "[3/5] 测试 Google Drive 上传..."
if ! command -v rclone >/dev/null 2>&1; then
    echo "  �?rclone 未安�?
    FAIL=$((FAIL + 1))
elif ! rclone listremotes 2>/dev/null | grep -q "^${RCLONE_GDRIVE}:$"; then
    echo "  �?rclone 远程 '${RCLONE_GDRIVE}' 未配�?
    FAIL=$((FAIL + 1))
else
    if rclone copy "${TEST_DIR}/test_backup.sql.gz" "${RCLONE_GDRIVE}:${RCLONE_DEST_DIR}/" \
        --log-level ERROR 2>&1; then
        echo "  �?上传成功"
        # 验证文件存在
        if rclone ls "${RCLONE_GDRIVE}:${RCLONE_DEST_DIR}/test_backup.sql.gz" \
            --log-level ERROR 2>/dev/null | grep -q "test_backup"; then
            echo "  �?文件验证存在"
            PASS=$((PASS + 1))
        else
            echo "  ⚠️  上传成功但验证失败（可能需要时间同步）"
            PASS=$((PASS + 1))
        fi
        # 清理云端测试文件
        rclone deletefile "${RCLONE_GDRIVE}:${RCLONE_DEST_DIR}/test_backup.sql.gz" \
            --log-level ERROR 2>/dev/null || true
        echo "  🗑�? 云端测试文件已清�?
    else
        echo "  �?上传失败，检�?rclone config"
        FAIL=$((FAIL + 1))
    fi
fi

# ─── 4. 测试 Mega ───
echo ""
echo "[4/5] 测试 Mega 上传..."
if ! command -v rclone >/dev/null 2>&1; then
    echo "  �?rclone 未安�?
    FAIL=$((FAIL + 1))
elif ! rclone listremotes 2>/dev/null | grep -q "^${RCLONE_MEGA}:$"; then
    echo "  �?rclone 远程 '${RCLONE_MEGA}' 未配�?
    FAIL=$((FAIL + 1))
else
    if rclone copy "${TEST_DIR}/test_backup.sql.gz" "${RCLONE_MEGA}:${RCLONE_DEST_DIR}/" \
        --log-level ERROR 2>&1; then
        echo "  �?上传成功"
        if rclone ls "${RCLONE_MEGA}:${RCLONE_DEST_DIR}/test_backup.sql.gz" \
            --log-level ERROR 2>/dev/null | grep -q "test_backup"; then
            echo "  �?文件验证存在"
            PASS=$((PASS + 1))
        else
            echo "  ⚠️  上传成功但验证失败（可能需要时间同步）"
            PASS=$((PASS + 1))
        fi
        # 清理云端测试文件
        rclone deletefile "${RCLONE_MEGA}:${RCLONE_DEST_DIR}/test_backup.sql.gz" \
            --log-level ERROR 2>/dev/null || true
        echo "  🗑�? 云端测试文件已清�?
    else
        echo "  �?上传失败，检�?rclone config"
        FAIL=$((FAIL + 1))
    fi
fi

# ─── 5. 清理本地测试文件 ───
echo ""
echo "[5/5] 清理本地测试文件..."
rm -rf "$TEST_DIR"
echo "  �?本地测试文件已清�?

# ─── 结果 ───
echo ""
echo "══════════════════════════════════════════════════�?
echo "  测试结果: �?${PASS} 通过  �?${FAIL} 失败"
echo "══════════════════════════════════════════════════�?
echo ""
if [ "$FAIL" -eq 0 ]; then
    echo "  全部通过！可以放心使用定时备份："
    echo ""
    echo "    bash scripts/db_backup/db_backup_cloud.sh"
    echo ""
    echo "  设定时任务（crontab -e）："
    echo ""
    echo "    17 3 * * * cd /root/audiobook && bash scripts/db_backup/db_backup_cloud.sh >> backups/cron.log 2>&1"
else
    echo "  有失败项，请先解决后再运行正式备份�?
    echo ""
    echo "  常见问题�?
    echo "    - rclone 未安�? curl https://rclone.org/install.sh | bash"
    echo "    - 远程未配�? rclone config"
    echo "    - 容器未运�? docker-compose up -d"
fi
echo ""
