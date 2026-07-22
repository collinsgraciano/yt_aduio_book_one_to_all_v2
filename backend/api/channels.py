"""频道管理 API。"""

from __future__ import annotations

import time
import requests as _requests
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..models.channel import ChannelCreate, ChannelUpdate
from ..services import channel_service

router = APIRouter(prefix="/api/channels", tags=["频道管理"])


class ProxyTestBody(BaseModel):
    proxy: str


@router.get("")
def list_channels():
    """获取所有频道列表。"""
    channels = channel_service.list_channels()
    return {"channels": channels, "total": len(channels)}


@router.get("/{channel_name}")
def get_channel(channel_name: str):
    """获取单个频道详情。"""
    channel = channel_service.get_channel(channel_name)
    if not channel:
        raise HTTPException(status_code=404, detail="频道不存在")
    return channel


@router.post("")
def create_channel(body: ChannelCreate):
    """新增频道。"""
    existing = channel_service.get_channel(body.channel_name)
    if existing:
        raise HTTPException(status_code=409, detail="频道名已存在")
    channel = channel_service.create_channel(
        body.channel_name, body.display_name or "",
        body.description or "", body.oauth_client_secret, body.proxy or "",
    )
    return {"message": "频道创建成功", "channel": channel}


@router.put("/{channel_name}")
def update_channel(channel_name: str, body: ChannelUpdate):
    """更新频道信息。"""
    count = channel_service.update_channel(
        channel_name, body.display_name, body.description, body.is_active, body.proxy,
    )
    if count == 0:
        raise HTTPException(status_code=404, detail="频道不存在")
    return {"message": "更新成功"}


@router.delete("/{channel_name}")
def delete_channel(channel_name: str):
    """删除频道（级联清理）。"""
    success = channel_service.delete_channel(channel_name)
    if not success:
        raise HTTPException(status_code=404, detail="频道不存在")
    return {"message": "频道已删除"}


@router.get("/{channel_name}/config")
def get_channel_config(channel_name: str):
    """获取频道运行配置。"""
    config = channel_service.get_channel_config(channel_name)
    if not config:
        raise HTTPException(status_code=404, detail="频道配置不存在")
    return config


@router.put("/{channel_name}/config")
def save_channel_config(channel_name: str, body: dict):
    """保存频道运行配置。"""
    if not channel_service.get_channel(channel_name):
        raise HTTPException(status_code=404, detail="频道不存在")
    result = channel_service.save_channel_config(channel_name, body)
    return {"message": "配置已保存", **result}


@router.get("/{channel_name}/oauth-status")
def get_oauth_status(channel_name: str):
    """获取频道 OAuth 状态。"""
    return channel_service.get_channel_oauth_status(channel_name)


@router.post("/test-proxy")
def test_proxy(body: ProxyTestBody):
    """测试代理连通性：通过代理请求 YouTube，返回延迟和状态。"""
    proxy = (body.proxy or "").strip()
    if not proxy:
        raise HTTPException(status_code=400, detail="代理地址不能为空")

    proxies = {"http": proxy, "https": proxy}
    start = time.time()
    try:
        resp = _requests.get("https://www.youtube.com", proxies=proxies, timeout=15)
        latency_ms = int((time.time() - start) * 1000)
        if resp.status_code == 200:
            return {"ok": True, "latency_ms": latency_ms, "status_code": resp.status_code,
                    "message": f"代理连通成功，延迟 {latency_ms}ms"}
        return {"ok": False, "latency_ms": latency_ms, "status_code": resp.status_code,
                "message": f"YouTube 返回 HTTP {resp.status_code}"}
    except _requests.exceptions.ConnectTimeout:
        return {"ok": False, "message": "连接超时（15s），代理不可用或地址错误"}
    except _requests.exceptions.ProxyError as e:
        return {"ok": False, "message": f"代理连接失败: {e}"}
    except _requests.exceptions.ReadTimeout:
        return {"ok": False, "message": "代理已连接但读取超时，带宽可能过小"}
    except Exception as e:
        return {"ok": False, "message": f"测试失败: {e}"}
