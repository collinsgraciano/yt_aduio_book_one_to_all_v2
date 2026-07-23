"""定时任务 API — 管理定时执行 HF 外包任务的调度规则。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import scheduler_service

router = APIRouter(prefix="/api/scheduled-tasks", tags=["定时任务"])
logger = logging.getLogger(__name__)


class CreateScheduledTaskRequest(BaseModel):
    channel_name: str
    cron_expr: str
    name: str = ""
    category: str = ""
    is_enabled: bool = True


class UpdateScheduledTaskRequest(BaseModel):
    channel_name: str | None = None
    cron_expr: str | None = None
    name: str | None = None
    category: str | None = None


@router.get("")
def list_scheduled_tasks():
    """获取所有定时任务。"""
    tasks = scheduler_service.list_scheduled_tasks()
    return {"tasks": tasks, "total": len(tasks)}


@router.post("")
def create_scheduled_task(body: CreateScheduledTaskRequest):
    """创建定时任务。"""
    try:
        task = scheduler_service.create_scheduled_task(
            channel_name=body.channel_name,
            cron_expr=body.cron_expr,
            name=body.name,
            category=body.category,
            is_enabled=body.is_enabled,
        )
        return {"ok": True, "task": task}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{schedule_id}")
def update_scheduled_task(schedule_id: int, body: UpdateScheduledTaskRequest):
    """更新定时任务。"""
    try:
        task = scheduler_service.update_scheduled_task(
            schedule_id,
            channel_name=body.channel_name,
            cron_expr=body.cron_expr,
            name=body.name,
            category=body.category,
        )
        return {"ok": True, "task": task}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{schedule_id}")
def delete_scheduled_task(schedule_id: int):
    """删除定时任务。"""
    result = scheduler_service.delete_scheduled_task(schedule_id)
    if not result["deleted"]:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    return {"ok": True, "deleted": True}


@router.post("/{schedule_id}/toggle")
def toggle_scheduled_task(schedule_id: int):
    """启用/禁用定时任务。"""
    try:
        task = scheduler_service.toggle_scheduled_task(schedule_id)
        return {"ok": True, "task": task}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{schedule_id}/run-now")
def run_scheduled_task_now(schedule_id: int):
    """手动触发定时任务（不影响下次调度时间）。"""
    try:
        result = scheduler_service.run_scheduled_task_now(schedule_id)
        return {"ok": True, "result": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
