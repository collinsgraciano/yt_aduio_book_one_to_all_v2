"""共享日志捕获工具 — 捕获 stdout + logging 输出。

供 backend/api/tests.py 和 hf_workers/unified_worker/runner.py 复用。
"""

from __future__ import annotations

import logging
import sys
import contextlib


class LogCapture:
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


class CapturingHandler(logging.Handler):
    """同时捕获标准 logging 模块输出的 Handler。"""

    def __init__(self, capture: LogCapture):
        super().__init__()
        self._capture = capture

    def emit(self, record):
        try:
            self._capture.lines.append(self.format(record) + "\n")
        except Exception:
            pass


@contextlib.contextmanager
def capture_logs():
    """上下文管理器：捕获 stdout + logging 输出。"""
    real_stdout = sys.stdout
    cap = LogCapture(real_stdout)
    handler = CapturingHandler(cap)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
    )
    root = logging.getLogger()

    sys.stdout = cap
    root.addHandler(handler)
    try:
        yield cap
    finally:
        sys.stdout = real_stdout
        root.removeHandler(handler)


def logs_text(cap, max_chars: int = 12000) -> str:
    """从 LogCapture 提取日志文本，截断到 max_chars。"""
    if not cap or not cap.text:
        return ""
    return cap.text[-max_chars:] if len(cap.text) > max_chars else cap.text
