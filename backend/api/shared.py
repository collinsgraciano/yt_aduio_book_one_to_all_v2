"""API 共享工具 — tasks_hf.py 和 tests_hf.py 复用。"""

from __future__ import annotations

import os


def get_vps_relay_url() -> str:
    """获取 VPS 中继地址。"""
    try:
        from ..services.config_service import get_global_setting
        url = get_global_setting("VPS_RELAY_URL") or ""
        if url:
            return url.rstrip("/")
    except Exception:
        pass
    return os.environ.get("VPS_RELAY_URL", "").rstrip("/")
