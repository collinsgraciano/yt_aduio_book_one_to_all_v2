"""测试实验 API — AI 生成、YouTube 上传、TG 音频下载的可视化测试。

每个测试端点：
1. 从当前全局设置读取配置 → 构建 runtime config
2. 获取 pipeline 串行锁（避免与运行中的任务冲突）
3. 应用 runtime config（pipeline 模块用模块级全局读取配置）
4. 捕获 stdout 日志（pipeline 的 SimpleLogger 基于 print）
5. 执行测试并返回结果、日志、错误信息
"""

from __future__ import annotations

import io
import os
import sys
import json
import time
import base64
import logging
import tempfile
import traceback
import contextlib
import threading
import uuid

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pipeline.log_capture import capture_logs as _capture_logs, logs_text as _logs_text
from .test_models import (
    AiTestRequest,
    UploadTestRequest,
    TgDownloadTestRequest,
    BgmDownloadRequest,
    BgmMixRequest,
)

router = APIRouter(prefix="/api/tests", tags=["测试实验"])

logger = logging.getLogger(__name__)


# ============================================================================
# ============================================================================

class _LogCapture:
    """捕获 stdout 输出，同时保留原始输出（让 docker logs 可见）。"""

    def __init__(self, real_stdout):
        self._real = real_stdout
        self.lines: list[str] = []

    def write(self, text):
        try:
            self._real.write(text)
        except Exception:
            pass
        self.lines.append(text)

    def flush(self):
        try:
            self._real.flush()
        except Exception:
            pass

    @property
    def text(self) -> str:
        return "".join(self.lines)


class _CapturingHandler(logging.Handler):
    """同时捕获标准 logging 模块输出的 Handler。"""

    def __init__(self, capture: _LogCapture):
        super().__init__()
        self._capture = capture

    def emit(self, record):
        try:
            self._capture.lines.append(self.format(record) + "\n")
        except Exception:
            pass


@contextlib.contextmanager
def _capture_logs():
    """上下文管理器：捕获 stdout + logging 输出。"""
    real_stdout = sys.stdout
    capture = _LogCapture(real_stdout)
    handler = _CapturingHandler(capture)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
    )
    root = logging.getLogger()

    sys.stdout = capture
    root.addHandler(handler)
    try:
        yield capture
    finally:
        sys.stdout = real_stdout
        root.removeHandler(handler)


# ============================================================================
# Pipeline 准备：导入路径 + 串行锁 + runtime config

def _acquire_pipeline_lock() -> tuple[bool, str | None]:
    """尝试获取 pipeline 串行锁（非阻塞）。

    如果锁已被占用（有 pipeline 任务正在运行），立即返回失败。
    """
    from ..services.task_service import _pipeline_lock

    ok = _pipeline_lock.acquire(blocking=False)
    if ok:
        return True, None
    return False, "Pipeline 正在运行中，请等待任务完成后再测试"


def _release_pipeline_lock():
    """释放 pipeline 串行锁。"""
    from ..services.task_service import _pipeline_lock

    _pipeline_lock.release()


def _build_test_config(channel_name: str = "") -> dict:
    """构建测试用 runtime config（global_settings + 频道覆盖）。"""
    from ..services.config_service import build_runtime_config, get_global_setting

    if not channel_name:
        channel_name = get_global_setting("YOUTUBE_CHANNEL_NAME") or ""

    return build_runtime_config(channel_name)