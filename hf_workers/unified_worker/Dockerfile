# ═══════════════════════════════════════════════════════════
# 统一 Worker Dockerfile — Hugging Face Space 部署
# ═══════════════════════════════════════════════════════════
# 合并流水线 Worker + 测试 Worker，通过双槽位组隔离资源：
#   pipeline_slots: 队列认领，重型任务（TG下载→混音→AI→封装→上传）
#   test_slots:     同步执行，轻量测试（AI/上传/TG下载/BGM混音）
#
# HF Space 约定：
#   - 监听 0.0.0.0:7860
#   - 持久目录 /data（BGM 音乐缓存）
#   - 临时输出 /tmp/output（HF 重启后清空，中间文件无需持久化）
#
# 构建上下文：HF Space 仓库根目录（仅需此 Dockerfile 一个文件）
#   docker build -f Dockerfile -t hf-unified-worker .
#
# 全部代码从 GitHub 仓库自动拉取，无需本地 COPY：
#   - pipeline/         → 核心流水线
#   - app.py / runner.py / shared.py / requirements.txt → Worker 应用
#   https://github.com/collinsgraciano/yt_aduio_book_one_to_all_v2
# ═══════════════════════════════════════════════════════════

FROM python:3.11-slim

# ─── 系统依赖 ───
# ffmpeg: 视频封装 + 音频处理
# wget/curl: DeepFilter 下载 + GitHub 拉取
# libpq5: psycopg 运行时依赖
# libsndfile1: librosa 读取音频文件
RUN apt-get update -o Acquire::Retries=5 -o Acquire::Timeout=30 \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        wget \
        curl \
        libpq5 \
        libsndfile1 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ─── 从 GitHub 拉取全部代码（构建时，作为启动时拉取失败的后备）───
RUN wget -q -O /tmp/repo.tar.gz \
        "https://github.com/collinsgraciano/yt_aduio_book_one_to_all_v2/archive/refs/heads/main.tar.gz" \
    && tar -xzf /tmp/repo.tar.gz -C /tmp/ \
    && mv /tmp/yt_aduio_book_one_to_all_v2-main/pipeline/ /app/pipeline/ \
    && cp /tmp/yt_aduio_book_one_to_all_v2-main/hf_workers/unified_worker/app.py        /app/app.py \
    && cp /tmp/yt_aduio_book_one_to_all_v2-main/hf_workers/unified_worker/runner.py     /app/runner.py \
    && cp /tmp/yt_aduio_book_one_to_all_v2-main/hf_workers/unified_worker/shared.py     /app/shared.py \
    && cp /tmp/yt_aduio_book_one_to_all_v2-main/hf_workers/unified_worker/requirements.txt /tmp/requirements.txt \
    && rm -rf /tmp/repo.tar.gz /tmp/yt_aduio_book_one_to_all_v2-main

# ─── Python 依赖 ───
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# ─── 启动脚本：每次容器启动时从 GitHub 拉取最新代码 ───
# 这样"重新部署"只需让容器退出(os._exit(1))，HF Space 自动重启，
# 启动脚本会拉取最新代码覆盖镜像中的旧版本，无需 HF_TOKEN。
RUN cat > /app/start.sh << 'SCRIPT'
#!/bin/bash
set -e
echo "[启动] 从 GitHub 拉取最新代码..."
if wget -q -O /tmp/repo.tar.gz \
    "https://github.com/collinsgraciano/yt_aduio_book_one_to_all_v2/archive/refs/heads/main.tar.gz"; then
    tar -xzf /tmp/repo.tar.gz -C /tmp/
    cp -r /tmp/yt_aduio_book_one_to_all_v2-main/pipeline/* /app/pipeline/
    cp /tmp/yt_aduio_book_one_to_all_v2-main/hf_workers/unified_worker/app.py        /app/app.py
    cp /tmp/yt_aduio_book_one_to_all_v2-main/hf_workers/unified_worker/runner.py     /app/runner.py
    cp /tmp/yt_aduio_book_one_to_all_v2-main/hf_workers/unified_worker/shared.py     /app/shared.py
    cp /tmp/yt_aduio_book_one_to_all_v2-main/hf_workers/unified_worker/requirements.txt /tmp/requirements.txt
    rm -rf /tmp/repo.tar.gz /tmp/yt_aduio_book_one_to_all_v2-main
    pip install --no-cache-dir -q -r /tmp/requirements.txt 2>&1 | tail -1
    echo "[启动] 代码更新完成"
else
    echo "[启动] ⚠️ GitHub 拉取失败，使用镜像内置代码"
    rm -f /tmp/repo.tar.gz
fi
exec python app.py
SCRIPT
RUN chmod +x /app/start.sh

# ─── DeepFilter 二进制 ───
# TG 缓存模式默认 ENABLE_DEEPFILTER=False，但作为断点续跑兜底。
RUN mkdir -p /opt/deepfilter \
    && wget -q -O /opt/deepfilter/deep-filter-0.5.6-x86_64-unknown-linux-musl \
        "https://github.com/Rikorose/DeepFilterNet/releases/download/v0.5.6/deep-filter-0.5.6-x86_64-unknown-linux-musl" \
    && chmod +x /opt/deepfilter/deep-filter-0.5.6-x86_64-unknown-linux-musl \
    && echo "DeepFilter 下载完成" \
    || echo "⚠️ DeepFilter 下载失败（TG缓存模式不受影响）"

# ─── 输出目录 + 音乐目录 ───
RUN mkdir -p /tmp/output /data/music

# ─── 环境变量 ───
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MALLOC_ARENA_MAX=2 \
    PORT=7860 \
    OUTPUT_ROOT=/tmp/output \
    MUSIC_DIR=/data/music \
    PIPELINE_SLOTS=1 \
    TEST_SLOTS=1 \
    DEEP_FILTER_PATH=/opt/deepfilter/deep-filter-0.5.6-x86_64-unknown-linux-musl

EXPOSE 7860

# ─── 健康检查 ───
HEALTHCHECK --interval=60s --timeout=10s --retries=3 --start-period=30s \
    CMD curl -f http://localhost:7860/health || exit 1

CMD ["/app/start.sh"]
