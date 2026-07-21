from ._test_shared import (
    router, logger, _acquire_pipeline_lock, _build_test_config,
    _release_pipeline_lock, _capture_logs, _logs_text,
    AiTestRequest, UploadTestRequest, TgDownloadTestRequest,
    BgmDownloadRequest, BgmMixRequest,
)

@router.post("/upload")
def test_upload(body: UploadTestRequest):
    """测试 YouTube 上传凭证是否有效。

    通过认证 + 获取频道信息 + 读取上传列表来验证上传能力。
    凭证有效即代表上传功能可用（上传使用同一套 OAuth 凭证）。
    """
    channel_name = body.channel_name.strip()
    if not channel_name:
        from ..services.config_service import get_global_setting
        channel_name = get_global_setting("YOUTUBE_CHANNEL_NAME") or ""
    if not channel_name:
        return {
            "success": False,
            "error": "未指定频道名，请在下方设置中配置 YOUTUBE_CHANNEL_NAME",
            "logs": "",
        }

    ok, err = _acquire_pipeline_lock()
    if not ok:
        return {"success": False, "error": err, "logs": ""}

    cap = None
    try:
        from pipeline.config import apply_runtime_config
        config = _build_test_config(channel_name)
        apply_runtime_config(config)

        with _capture_logs() as cap:
            print(f"[测试] YouTube 上传测试开始（频道: {channel_name}）", flush=True)
            from pipeline.youtube import (
                authenticate_youtube_from_supabase,
                MissingYouTubeCredentialsError,
            )

            print("[测试] → 正在初始化 YouTube 客户端...", flush=True)
            youtube = authenticate_youtube_from_supabase(channel_name)
            if not youtube:
                print("[测试] ✗ YouTube 客户端初始化失败", flush=True)
                return {
                    "success": False,
                    "error": f"无法初始化 YouTube 客户端（频道「{channel_name}」凭证无效或缺失）。"
                             f"请先在频道管理中完成 OAuth 授权。",
                    "logs": _logs_text(cap),
                }
            print("[测试] ✓ YouTube 客户端初始化成功", flush=True)

            # 获取频道信息
            print("[测试] → 获取频道信息...", flush=True)
            channel_info = {}
            try:
                resp = youtube.channels().list(
                    part="snippet,statistics,contentDetails",
                    mine=True,
                    maxResults=1,
                ).execute()
                items = resp.get("items", [])
                if items:
                    item = items[0]
                    snippet = item.get("snippet", {}) or {}
                    stats = item.get("statistics", {}) or {}
                    content = item.get("contentDetails", {}) or {}
                    related = content.get("relatedPlaylists", {}) or {}
                    channel_info = {
                        "channel_id": item.get("id", ""),
                        "title": snippet.get("title", ""),
                        "description": (snippet.get("description", "") or "")[:300],
                        "subscriber_count": stats.get("subscriberCount", "隐藏"),
                        "video_count": stats.get("videoCount", "0"),
                        "view_count": stats.get("viewCount", "0"),
                        "uploads_playlist_id": related.get("uploads", ""),
                    }
                    print(f"[测试] ✓ 频道: {channel_info.get('title', '')}，视频: {channel_info.get('video_count', '0')} 个", flush=True)
            except Exception as e:
                print(f"[测试] ✗ 获取频道信息失败: {type(e).__name__}: {e}", flush=True)
                return {
                    "success": False,
                    "error": f"获取频道信息失败: {type(e).__name__}: {e}",
                    "logs": _logs_text(cap),
                }

            # 获取最近上传的视频（验证 uploads 列表可读）
            print("[测试] → 读取最近上传列表...", flush=True)
            recent_uploads = []
            try:
                uploads_pid = channel_info.get("uploads_playlist_id", "")
                if uploads_pid:
                    resp = youtube.playlistItems().list(
                        part="contentDetails,snippet",
                        playlistId=uploads_pid,
                        maxResults=5,
                    ).execute()
                    for it in resp.get("items", []):
                        cd = it.get("contentDetails", {}) or {}
                        sn = it.get("snippet", {}) or {}
                        vid = cd.get("videoId", "")
                        if vid:
                            recent_uploads.append({
                                "video_id": vid,
                                "title": sn.get("title", ""),
                                "url": f"https://youtu.be/{vid}",
                            })
                    print(f"[测试] ✓ 获取到 {len(recent_uploads)} 个最近上传视频", flush=True)
            except Exception as e:
                print(f"[测试] ⚠ 读取上传列表失败（非致命）: {e}", flush=True)
                logger.warning("读取上传列表失败（非致命）: %s", e)

            print("[测试] YouTube 上传测试完成", flush=True)

        return {
            "success": True,
            "channel_name": channel_name,
            "channel_info": channel_info,
            "recent_uploads": recent_uploads,
            "message": (
                f"✅ 频道「{channel_name}」凭证有效，上传功能可用。\n"
                f"频道: {channel_info.get('title', '')}\n"
                f"视频总数: {channel_info.get('video_count', '0')}\n"
                f"订阅数: {channel_info.get('subscriber_count', '隐藏')}"
            ),
            "logs": _logs_text(cap),
        }
    except MissingYouTubeCredentialsError as e:
        return {"success": False, "error": str(e), "logs": _logs_text(cap)}
    except ImportError as e:
        return {
            "success": False,
            "error": f"Pipeline 模块导入失败（依赖可能未安装）: {e}",
            "logs": _logs_text(cap),
        }
    except Exception as e:
        tb = traceback.format_exc()
        return {
            "success": False,
            "error": f"{type(e).__name__}: {e}",
            "traceback": tb,
            "logs": _logs_text(cap),
        }
    finally:
        _release_pipeline_lock()
