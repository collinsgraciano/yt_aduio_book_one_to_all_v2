"""HF Workers 共享工具 — vps_relay 和 unified_worker 复用。"""

from __future__ import annotations

import csv


def normalize_text_items(value):
    """兼容历史云端返回的文本集合格式。

    与 pipeline.runtime.normalize_text_items 逻辑一致：
    - None / 空值
    - Python list/tuple/set
    - 普通逗号分隔字符串: "a,b"
    - PostgreSQL array literal: {"a","b"}
    """
    if value is None:
        return []

    raw_items = []

    if isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return []

        if text.startswith("{") and text.endswith("}"):
            inner = text[1:-1].strip()
            if not inner:
                return []
            try:
                raw_items = next(
                    csv.reader(
                        [inner],
                        skipinitialspace=True,
                        quotechar='"',
                        escapechar="\\",
                    )
                )
            except Exception:
                raw_items = inner.split(",")
        else:
            raw_items = text.split(",")
    else:
        raw_items = [value]

    normalized = []
    seen = set()
    for item in raw_items:
        text = str(item).strip().strip('"').strip()
        if not text or text in seen:
            continue
        normalized.append(text)
        seen.add(text)
    return normalized


def upsert_worker_stats(cur, worker_id: str, worker_type: str, success: bool, duration_seconds: int):
    """更新 Worker 业绩统计表（需传入已打开的 cursor）。

    供 vps_relay._update_worker_stats 和 unified_worker._update_worker_stats 共用，
    各自管理 cursor 的获取和提交。
    """
    cur.execute(
        """INSERT INTO public.hf_worker_stats (worker_id, worker_type, total_jobs, success_jobs, failed_jobs, total_seconds, last_job_at, last_seen_at, updated_at)
           VALUES (%s, %s, 1, %s, %s, %s, now(), now(), now())
           ON CONFLICT (worker_id) DO UPDATE SET
             total_jobs = public.hf_worker_stats.total_jobs + 1,
             success_jobs = public.hf_worker_stats.success_jobs + %s,
             failed_jobs = public.hf_worker_stats.failed_jobs + %s,
             total_seconds = public.hf_worker_stats.total_seconds + %s,
             last_job_at = now(),
             last_seen_at = now(),
             updated_at = now()
        """,
        (worker_id, worker_type, 1 if success else 0, 0 if success else 1, duration_seconds,
         1 if success else 0, 0 if success else 1, duration_seconds),
    )
