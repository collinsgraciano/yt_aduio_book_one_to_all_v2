"""定时任务调度服务 — 定时触发 HF 外包任务。

在后台线程中定期扫描 scheduled_tasks 表，
到期的任务自动执行 HF 外包投递（seed_jobs_direct 逻辑）。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from psycopg import sql

from ..database import fetch_one, fetch_all, execute

logger = logging.getLogger(__name__)

_CHECK_INTERVAL = 30  # 扫描间隔（秒）

_scheduler_thread: threading.Thread | None = None
_stop_event = threading.Event()


# ═══════════════════════════════════════════════════════════
# Cron 工具
# ═══════════════════════════════════════════════════════════

def _calc_next_run(cron_expr: str, from_time: datetime | None = None) -> datetime:
    """使用 croniter 计算下次运行时间（UTC）。"""
    from croniter import croniter
    if from_time is None:
        from_time = datetime.now(timezone.utc)
    cron = croniter(cron_expr, from_time)
    return cron.get_next(datetime)


def _validate_cron(cron_expr: str) -> bool:
    """校验 cron 表达式是否合法。"""
    from croniter import croniter
    return croniter.is_valid(cron_expr)


# ═══════════════════════════════════════════════════════════
# CRUD
# ═══════════════════════════════════════════════════════════

def list_scheduled_tasks() -> list[dict]:
    """获取所有定时任务。"""
    return fetch_all(
        sql.SQL("""
            SELECT st.*, c.display_name AS channel_display_name
            FROM public.scheduled_tasks st
            LEFT JOIN public.channels c ON c.channel_name = st.channel_name
            ORDER BY st.created_at DESC
        """)
    )


def create_scheduled_task(
    channel_name: str,
    cron_expr: str,
    name: str = "",
    category: str = "",
    is_enabled: bool = True,
) -> dict:
    """创建定时任务。"""
    channel_name = channel_name.strip()
    cron_expr = cron_expr.strip()
    if not channel_name:
        raise ValueError("频道名不能为空")
    if not _validate_cron(cron_expr):
        raise ValueError(f"无效的 cron 表达式: {cron_expr}")

    # 检查频道是否存在
    channel = fetch_one(
        sql.SQL("SELECT channel_name FROM public.channels WHERE channel_name = %s"),
        (channel_name,),
    )
    if not channel:
        raise ValueError(f"频道 {channel_name} 不存在")

    next_run = _calc_next_run(cron_expr) if is_enabled else None

    row = fetch_one(
        sql.SQL("""
            INSERT INTO public.scheduled_tasks
                (name, channel_name, cron_expr, category, is_enabled, next_run_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
        """),
        (name, channel_name, cron_expr, category, is_enabled, next_run),
    )
    logger.info("创建定时任务: channel=%s cron=%s enabled=%s", channel_name, cron_expr, is_enabled)
    return row


def update_scheduled_task(
    schedule_id: int,
    channel_name: str | None = None,
    cron_expr: str | None = None,
    name: str | None = None,
    category: str | None = None,
) -> dict:
    """更新定时任务。"""
    existing = fetch_one(
        sql.SQL("SELECT * FROM public.scheduled_tasks WHERE schedule_id = %s"),
        (schedule_id,),
    )
    if not existing:
        raise ValueError(f"定时任务 {schedule_id} 不存在")

    updates = {}
    if channel_name is not None:
        channel_name = channel_name.strip()
        if not channel_name:
            raise ValueError("频道名不能为空")
        ch = fetch_one(
            sql.SQL("SELECT channel_name FROM public.channels WHERE channel_name = %s"),
            (channel_name,),
        )
        if not ch:
            raise ValueError(f"频道 {channel_name} 不存在")
        updates["channel_name"] = channel_name

    if cron_expr is not None:
        cron_expr = cron_expr.strip()
        if not _validate_cron(cron_expr):
            raise ValueError(f"无效的 cron 表达式: {cron_expr}")
        updates["cron_expr"] = cron_expr

    if name is not None:
        updates["name"] = name

    if category is not None:
        updates["category"] = category

    if not updates:
        return existing

    # 如果 cron 改了，重新计算 next_run_at
    final_cron = updates.get("cron_expr", existing["cron_expr"])
    is_enabled = existing["is_enabled"]
    if is_enabled:
        updates["next_run_at"] = _calc_next_run(final_cron)
    else:
        updates["next_run_at"] = None

    updates["updated_at"] = datetime.now(timezone.utc)

    set_parts = sql.SQL(", ").join(
        sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder())
        for k in updates.keys()
    )
    execute(
        sql.SQL("UPDATE public.scheduled_tasks SET {} WHERE schedule_id = %s").format(set_parts),
        tuple(updates.values()) + (schedule_id,),
    )
    logger.info("更新定时任务 %s: %s", schedule_id, list(updates.keys()))
    return fetch_one(
        sql.SQL("SELECT * FROM public.scheduled_tasks WHERE schedule_id = %s"),
        (schedule_id,),
    )


def delete_scheduled_task(schedule_id: int) -> dict:
    """删除定时任务。"""
    count = execute(
        sql.SQL("DELETE FROM public.scheduled_tasks WHERE schedule_id = %s"),
        (schedule_id,),
    )
    return {"schedule_id": schedule_id, "deleted": count > 0}


def toggle_scheduled_task(schedule_id: int) -> dict:
    """启用/禁用定时任务。"""
    existing = fetch_one(
        sql.SQL("SELECT * FROM public.scheduled_tasks WHERE schedule_id = %s"),
        (schedule_id,),
    )
    if not existing:
        raise ValueError(f"定时任务 {schedule_id} 不存在")

    new_enabled = not existing["is_enabled"]
    next_run = _calc_next_run(existing["cron_expr"]) if new_enabled else None

    execute(
        sql.SQL("UPDATE public.scheduled_tasks SET is_enabled = %s, next_run_at = %s, updated_at = now() WHERE schedule_id = %s"),
        (new_enabled, next_run, schedule_id),
    )
    logger.info("定时任务 %s %s", schedule_id, "启用" if new_enabled else "禁用")
    return fetch_one(
        sql.SQL("SELECT * FROM public.scheduled_tasks WHERE schedule_id = %s"),
        (schedule_id,),
    )


def run_scheduled_task_now(schedule_id: int) -> dict:
    """手动触发定时任务（不影响下次调度时间）。"""
    existing = fetch_one(
        sql.SQL("SELECT * FROM public.scheduled_tasks WHERE schedule_id = %s"),
        (schedule_id,),
    )
    if not existing:
        raise ValueError(f"定时任务 {schedule_id} 不存在")

    result = _execute_scheduled_task(existing)
    return {
        "schedule_id": schedule_id,
        "channel_name": existing["channel_name"],
        "result": result,
    }


# ═══════════════════════════════════════════════════════════
# 执行逻辑
# ═══════════════════════════════════════════════════════════

def _execute_scheduled_task(task: dict) -> dict:
    """执行单个定时任务 — 调用 HF 外包投递。

    优先使用 VPS 中继（seed），失败时回退到直写数据库（seed-direct）。
    """
    schedule_id = task["schedule_id"]
    channel_name = task["channel_name"]
    category = task.get("category") or ""

    logger.info("[定时任务 %s] 执行: channel=%s category=%s", schedule_id, channel_name, category or "(全部)")

    try:
        # 尝试 VPS 中继路径
        from ..api.shared import get_vps_relay_url
        import requests

        relay_url = get_vps_relay_url()
        result = None

        if relay_url:
            try:
                resp = requests.post(
                    f"{relay_url}/api/seed-jobs",
                    json={"channel_name": channel_name, "category": category},
                    timeout=30,
                )
                result = resp.json()
                if not result.get("ok", False):
                    logger.warning("[定时任务 %s] VPS 中继返回失败: %s，回退到直写", schedule_id, result.get("error"))
                    result = None
            except Exception as e:
                logger.warning("[定时任务 %s] VPS 中继请求失败: %s，回退到直写", schedule_id, e)
                result = None

        # 回退：直写数据库
        if result is None:
            from ..api.tasks_hf import seed_jobs_direct, SeedJobsRequest
            result = seed_jobs_direct(SeedJobsRequest(channel_name=channel_name, category=category))

        # 记录执行结果
        status = "success" if result.get("ok", result.get("inserted", 0) >= 0) else "failed"
        message = f"投递 {result.get('inserted', 0)} 个任务" if result.get("ok") else result.get("error", "未知结果")

        _record_run_result(schedule_id, status, message)
        logger.info("[定时任务 %s] 完成: %s", schedule_id, message)
        return result

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        _record_run_result(schedule_id, "failed", error_msg[:500])
        logger.error("[定时任务 %s] 执行失败: %s", schedule_id, error_msg)
        return {"ok": False, "error": error_msg}


def _record_run_result(schedule_id: int, status: str, message: str):
    """记录执行结果并更新下次运行时间。"""
    now = datetime.now(timezone.utc)
    task = fetch_one(
        sql.SQL("SELECT cron_expr, is_enabled FROM public.scheduled_tasks WHERE schedule_id = %s"),
        (schedule_id,),
    )
    next_run = None
    if task and task["is_enabled"]:
        try:
            next_run = _calc_next_run(task["cron_expr"], now)
        except Exception:
            next_run = None

    execute(
        sql.SQL("""
            UPDATE public.scheduled_tasks
            SET last_run_at = %s,
                last_run_status = %s,
                last_run_message = %s,
                next_run_at = %s,
                updated_at = now()
            WHERE schedule_id = %s
        """),
        (now, status, message, next_run, schedule_id),
    )


# ═══════════════════════════════════════════════════════════
# 后台调度线程
# ═══════════════════════════════════════════════════════════

def _scheduler_loop():
    """后台调度循环 — 每 30 秒扫描到期任务。"""
    logger.info("定时任务调度器启动（扫描间隔 %ds）", _CHECK_INTERVAL)
    while not _stop_event.is_set():
        try:
            _check_and_run_due_tasks()
        except Exception as e:
            logger.error("调度器扫描异常: %s", e)

        _stop_event.wait(_CHECK_INTERVAL)
    logger.info("定时任务调度器已停止")


def _check_and_run_due_tasks():
    """查询到期任务并执行。"""
    now = datetime.now(timezone.utc)
    due_tasks = fetch_all(
        sql.SQL("""
            SELECT * FROM public.scheduled_tasks
            WHERE is_enabled = true AND next_run_at IS NOT NULL AND next_run_at <= %s
            ORDER BY next_run_at ASC
        """),
        (now,),
    )

    if not due_tasks:
        return

    logger.info("发现 %d 个到期定时任务", len(due_tasks))
    for task in due_tasks:
        try:
            _execute_scheduled_task(task)
        except Exception as e:
            logger.error("定时任务 %s 执行异常: %s", task.get("schedule_id"), e)


def start_scheduler():
    """启动后台调度线程。"""
    global _scheduler_thread
    if _scheduler_thread is not None and _scheduler_thread.is_alive():
        logger.warning("定时任务调度器已在运行")
        return

    _stop_event.clear()
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop,
        daemon=True,
        name="scheduled-tasks-scheduler",
    )
    _scheduler_thread.start()
    logger.info("定时任务调度线程已启动")


def stop_scheduler():
    """停止后台调度线程。"""
    global _scheduler_thread
    _stop_event.set()
    if _scheduler_thread is not None:
        _scheduler_thread.join(timeout=10)
        _scheduler_thread = None
    logger.info("定时任务调度线程已停止")


# ═══════════════════════════════════════════════════════════
# 启动时修复 next_run_at
# ═══════════════════════════════════════════════════════════

def refresh_next_run_times():
    """为所有缺少 next_run_at 的启用任务计算下次运行时间。

    在应用启动时调用，确保迁移后或重启后任务能正常调度。
    """
    tasks = fetch_all(
        sql.SQL("SELECT schedule_id, cron_expr FROM public.scheduled_tasks WHERE is_enabled = true AND next_run_at IS NULL")
    )
    if not tasks:
        return

    now = datetime.now(timezone.utc)
    for task in tasks:
        try:
            next_run = _calc_next_run(task["cron_expr"], now)
            execute(
                sql.SQL("UPDATE public.scheduled_tasks SET next_run_at = %s WHERE schedule_id = %s"),
                (next_run, task["schedule_id"]),
            )
        except Exception as e:
            logger.warning("定时任务 %s 计算 next_run_at 失败: %s", task["schedule_id"], e)

    logger.info("已为 %d 个定时任务补充 next_run_at", len(tasks))
