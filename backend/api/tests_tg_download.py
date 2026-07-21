from ._test_shared import (
    router, logger, _acquire_pipeline_lock, _build_test_config,
    _release_pipeline_lock, _capture_logs, _logs_text,
    AiTestRequest, UploadTestRequest, TgDownloadTestRequest,
    BgmDownloadRequest, BgmMixRequest,
)

@router.post("/tg-download")
def test_tg_download(body: TgDownloadTestRequest):
    """测试 Telegram 音频下载。

    使用全局 TG_BOT_TOKEN 配置，根据样本中的 bot_user_id / bot_id 匹配正确的 Bot Token
    （与 pipeline 正式下载逻辑完全一致）。
    1. getFile 验证：验证 Bot Token 能否访问指定 file_id
    2. 可选实际下载：将文件下载到临时目录，验证后自动清理
    """
    file_id = body.file_id.strip()

    # 如果未提供 file_id，尝试从数据库取一个样本
    if not file_id:
        from ..database import fetch_one
        from psycopg import sql

        row = fetch_one(
            sql.SQL(
                """SELECT telegram_file_id, telegram_bot_user_id,
                          telegram_bot_id, book_name, chapter_name
                   FROM public.audiobook_chapters
                   WHERE telegram_file_id IS NOT NULL
                     AND telegram_file_id != ''
                     AND upload_status = 'uploaded'
                   ORDER BY uploaded_at DESC NULLS LAST
                   LIMIT 1"""
            )
        )
        if row and row.get("telegram_file_id"):
            return {
                "success": False,
                "need_sample": True,
                "error": "未输入 file_id，已从数据库取到一条样本，点击「使用样本」按钮后重试",
                "sample": {
                    "file_id": row["telegram_file_id"],
                    "bot_user_id": row.get("telegram_bot_user_id"),
                    "bot_id": row.get("telegram_bot_id"),
                    "book_name": row.get("book_name", ""),
                    "chapter_name": row.get("chapter_name", ""),
                },
                "logs": "",
            }
        return {
            "success": False,
            "error": "未输入 file_id，且数据库中无已上传的 TG 缓存样本",
            "logs": "",
        }

    ok, err = _acquire_pipeline_lock()
    if not ok:
        return {"success": False, "error": err, "logs": ""}

    save_path = None  # 用于 finally 清理临时文件
    cap = None
    try:
        from pipeline.config import apply_runtime_config
        config = _build_test_config()
        apply_runtime_config(config)

        with _capture_logs() as cap:
            print(f"[测试] TG 音频下载测试开始（file_id: {file_id[:40]}...）", flush=True)
            from pipeline.tg_audio import (
                _get_tg_bot_tokens,
                _find_correct_bot_token,
                _tg_get_file_path,
                _try_all_tokens_get_file_path,
                download_audio_from_telegram,
            )

            bot_tokens = _get_tg_bot_tokens()
            if not bot_tokens:
                print("[测试] ✗ 全局 TG_BOT_TOKEN 未配置", flush=True)
                return {
                    "success": False,
                    "error": "全局 TG_BOT_TOKEN 未配置，请在「相关设置」中配置",
                    "logs": _logs_text(cap),
                }
            print(f"[测试] ✓ 已加载 {len(bot_tokens)} 个 Bot Token", flush=True)

            # 用 pipeline 的匹配逻辑找到正确的 token（与正式下载逻辑一致）
            matched_token, matched_idx = _find_correct_bot_token(
                file_id,
                bot_tokens,
                known_bot_id=body.bot_id,
                known_bot_user_id=body.bot_user_id,
            )
            if matched_token:
                print(f"[测试] → 匹配到 Bot Token #{matched_idx}（通过 bot_user_id={body.bot_user_id} 或 bot_id={body.bot_id}）", flush=True)
            else:
                print("[测试] → 未找到匹配的 Token，将全量尝试", flush=True)

            # 第一步：getFile 验证（先试匹配到的 token）
            file_path = None
            if matched_token:
                print("[测试] → 调用 getFile 验证...", flush=True)
                file_path = _tg_get_file_path(
                    file_id, matched_token, max_retries=2, suppress_invalid=True
                )

            used_token_idx = matched_idx if matched_idx is not None else 0

            if not file_path:
                # 全量尝试所有 token（跳过已试过的）
                skip = {matched_idx} if matched_idx is not None else None
                print("[测试] → 匹配的 Token 失败，全量尝试所有 Token...", flush=True)
                file_path, found_token, found_idx = _try_all_tokens_get_file_path(
                    file_id, bot_tokens, skip_indices=skip, max_retries=2
                )
                if found_token:
                    matched_token = found_token
                    used_token_idx = found_idx

            if not file_path:
                print("[测试] ✗ 所有 Bot Token 均无法获取此 file_id", flush=True)
                return {
                    "success": False,
                    "error": (
                        "getFile 失败：所有 Bot Token 均无法获取此 file_id。"
                        "可能原因：file_id 无效、文件已被删除、或 file_id 属于其他 Bot。"
                    ),
                    "file_id": file_id,
                    "token_count": len(bot_tokens),
                    "logs": _logs_text(cap),
                }

            print(f"[测试] ✓ getFile 成功！Token #{used_token_idx}，路径: {file_path}", flush=True)

            result = {
                "success": True,
                "file_id": file_id,
                "tg_file_path": file_path,
                "download_url": f"https://api.telegram.org/file/bot{matched_token}/{file_path}",
                "token_index": used_token_idx,
                "token_count": len(bot_tokens),
                "message": f"✅ getFile 成功！Bot Token #{used_token_idx}（共 {len(bot_tokens)} 个）可访问此文件。\n文件路径: {file_path}",
                "logs": _logs_text(cap),
            }

            # 第二步：可选实际下载
            if body.do_download:
                save_path = os.path.join(
                    tempfile.gettempdir(), f"tg_test_{int(time.time())}.mp3"
                )
                print("[测试] → 开始实际下载文件...", flush=True)
                dl_result = download_audio_from_telegram(
                    file_id, save_path, max_retries=2,
                    bot_id=body.bot_id, bot_user_id=body.bot_user_id,
                )
                result["download"] = {
                    "success": dl_result.get("ok", False),
                    "file_size": dl_result.get("file_size", 0),
                    "error": dl_result.get("error", ""),
                    "cleaned": True,
                }
                if dl_result.get("ok"):
                    size_kb = dl_result.get("file_size", 0) // 1024
                    print(f"[测试] ✓ 下载成功，文件大小: {size_kb} KB", flush=True)
                    result["message"] += f"\n📥 下载成功，文件大小: {size_kb} KB（测试文件已自动清理）"
                else:
                    print(f"[测试] ✗ 下载失败: {dl_result.get('error', '')}", flush=True)
                    result["message"] += f"\n❌ 下载失败: {dl_result.get('error', '')}"

            print("[测试] TG 音频下载测试完成", flush=True)
            return result
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
        # 清理临时下载文件（测试只需验证，不需保留文件）
        if save_path and os.path.exists(save_path):
            try:
                os.remove(save_path)
            except Exception:
                pass
        _release_pipeline_lock()


# ============================================================================
# BGM 混音测试 — 随机下载章节、缓存管理、混音测试
# ============================================================================
