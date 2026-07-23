from ._test_shared import (
    router, logger, _acquire_pipeline_lock, _build_test_config,
    _release_pipeline_lock, _capture_logs, _logs_text,
    AiTestRequest, UploadTestRequest, TgDownloadTestRequest,
    BgmDownloadRequest, BgmMixRequest,
)

def _bgm_test_dir() -> str:
    """BGM 测试专用目录（持久保留下载的章节音频）。"""
    from ..settings import settings as app_settings
    return os.path.join(app_settings.output_root, "_bgm_test")


@router.get("/bgm/cache")
def bgm_list_cache():
    """列出 BGM 测试缓存的音频文件 + 音乐池信息。

    返回两个分类：
    - test_files: 源音频（下载的章节）
    - output_files: 混音结果（bgm_output_ 前缀）
    """
    import glob as _glob
    from ..settings import settings as app_settings

    test_dir = _bgm_test_dir()
    os.makedirs(test_dir, exist_ok=True)

    supported_exts = (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac")
    test_files = []
    output_files = []
    for name in sorted(os.listdir(test_dir), reverse=True):
        path = os.path.join(test_dir, name)
        if not os.path.isfile(path):
            continue
        if os.path.splitext(name)[1].lower() not in supported_exts:
            continue
        stat = os.stat(path)
        entry = {
            "name": name,
            "size_mb": round(stat.st_size / (1024 * 1024), 2),
            "modified": stat.st_mtime,
        }
        if name.startswith("bgm_output_"):
            output_files.append(entry)
        else:
            test_files.append(entry)

    # 音乐池信息
    music_dir = app_settings.music_dir
    music_files_set = set()
    if os.path.isdir(music_dir):
        for ext in ("*.mp3", "*.wav", "*.flac", "*.ogg", "*.m4a", "*.aac", "*.wma"):
            music_files_set.update(_glob.glob(os.path.join(music_dir, ext)))
            music_files_set.update(_glob.glob(os.path.join(music_dir, ext.upper())))

    # 兼容旧前端的 files 字段（合并两个列表）
    return {
        "files": test_files + output_files,
        "test_files": test_files,
        "output_files": output_files,
        "music_dir": music_dir,
        "music_count": len(music_files_set),
        "test_dir": test_dir,
    }


@router.post("/bgm/download")
def bgm_download(body: BgmDownloadRequest):
    """随机下载章节音频用于 BGM 测试。

    从数据库中随机选取有章节的书，下载指定数量的章节音频到测试目录。
    文件保留在测试目录中，可反复使用，也可手动清理后重新下载。
    """
    from ..database import fetch_one
    from psycopg import sql

    test_dir = _bgm_test_dir()
    os.makedirs(test_dir, exist_ok=True)

    count = max(1, min(body.count, 20))

    # ── 从数据库取一本有章节 URL 的书 ──
    if body.book_id:
        row = fetch_one(
            sql.SQL("SELECT book_id, book_name, book_data FROM public.books WHERE book_id = %s"),
            (body.book_id,),
        )
    else:
        row = fetch_one(
            sql.SQL("""
                SELECT book_id, book_name, book_data
                FROM public.books
                WHERE book_data IS NOT NULL
                  AND book_data::text != 'null'
                  AND book_data::text LIKE %s
                ORDER BY RANDOM()
                LIMIT 1
            """),
            ('%mp3Url%',),
        )

    if not row:
        return {
            "success": False,
            "error": "数据库中无可用书籍（需要有包含 mp3Url 的 book_data）",
            "logs": "",
        }

    book_id = row["book_id"]
    book_name = row.get("book_name", "")
    raw = row.get("book_data")

    try:
        book_data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:
        return {"success": False, "error": f"book_data JSON 解析失败: {e}", "logs": ""}

    # ── 用 pipeline 的提取函数解析章节列表 ──
    try:
        from pipeline.pipeline import _extract_chapters_from_book_data
        chapters = _extract_chapters_from_book_data(book_data)
    except ImportError:
        # 回退：简单解析 chapters_data
        chapters = []
        for key in ("chapters_data", "tingChapterList", "chapterList", "chapters"):
            val = book_data.get(key) if isinstance(book_data, dict) else None
            if isinstance(val, list) and val:
                chapters = val
                break

    if not chapters:
        return {"success": False, "error": f"书籍「{book_name}」未提取到章节列表", "logs": ""}

    # ── 随机选取章节 ──
    import random as _random
    _random.shuffle(chapters)
    selected = chapters[:count]

    # ── 获取串行锁 ──
    ok, err = _acquire_pipeline_lock()
    if not ok:
        return {"success": False, "error": err, "logs": ""}

    cap = None
    downloaded = []
    try:
        from pipeline.config import apply_runtime_config
        config = _build_test_config()
        apply_runtime_config(config)

        with _capture_logs() as cap:
            from pipeline.audio import download_audio_file
            from pipeline.runtime import sanitize_filename

            print(f"[BGM测试] 从书「{book_name}」({book_id}) 随机选取 {len(selected)} 个章节", flush=True)

            for i, ch in enumerate(selected, 1):
                mp3_url = ch.get("mp3Url", ch.get("playUrl", ch.get("url", "")))
                title = ch.get("title", ch.get("chapterName", ch.get("name", f"chapter_{i:04d}")))

                if not mp3_url:
                    print(f"[BGM测试] 跳过章节 {i}（无 URL）: {title}", flush=True)
                    continue

                safe_title = sanitize_filename(str(title))
                # 文件名: 书名_章节名_bookID前8位.mp3，限制长度
                filename = f"{sanitize_filename(book_name)}_{safe_title}_{str(book_id)[:8]}.mp3"[:120]
                save_path = os.path.join(test_dir, filename)

                if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
                    size_mb = round(os.path.getsize(save_path) / (1024 * 1024), 2)
                    print(f"[BGM测试] 复用已存在: {filename} ({size_mb} MB)", flush=True)
                    downloaded.append({
                        "name": filename,
                        "size_mb": size_mb,
                        "title": title,
                        "reused": True,
                    })
                    continue

                print(f"[BGM测试] 下载 {i}/{len(selected)}: {title}", flush=True)
                result = download_audio_file(mp3_url, save_path)

                if result.get("ok"):
                    size_mb = round(os.path.getsize(save_path) / (1024 * 1024), 2)
                    print(f"[BGM测试] ✓ 下载成功: {filename} ({size_mb} MB)", flush=True)
                    downloaded.append({
                        "name": filename,
                        "size_mb": size_mb,
                        "title": title,
                        "reused": False,
                    })
                else:
                    print(f"[BGM测试] ✗ 下载失败: {result.get('error', '')}", flush=True)

        return {
            "success": len(downloaded) > 0,
            "book_name": book_name,
            "book_id": book_id,
            "downloaded": downloaded,
            "logs": _logs_text(cap),
        }
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


@router.post("/bgm/clear")
def bgm_clear(body: dict = None):
    """清理 BGM 测试缓存目录中的音频文件。

    支持按分类清理，互不影响：
    - category="test": 仅清理源测试音频（非 bgm_output_ 前缀）
    - category="output": 仅清理混音结果（bgm_output_ 前缀）
    - category 不传或 "all": 清理全部
    """
    # 兼容无 body 的旧调用
    category = "all"
    if body and isinstance(body, dict):
        category = body.get("category", "all")

    test_dir = _bgm_test_dir()

    if not os.path.isdir(test_dir):
        return {"success": True, "deleted": 0, "message": "测试目录不存在"}

    supported_exts = (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac")
    deleted = 0
    for name in os.listdir(test_dir):
        path = os.path.join(test_dir, name)
        if not os.path.isfile(path):
            continue
        if os.path.splitext(name)[1].lower() not in supported_exts:
            continue

        is_output = name.startswith("bgm_output_")
        # 根据分类决定是否删除
        if category == "test" and is_output:
            continue
        if category == "output" and not is_output:
            continue

        try:
            os.remove(path)
            deleted += 1
        except Exception:
            pass

    label = {"test": "测试音频", "output": "混音结果", "all": "全部"}.get(category, "全部")
    return {"success": True, "deleted": deleted, "message": f"已清理 {deleted} 个{label}文件"}


@router.get("/bgm/play")
def bgm_play(file: str = "", download: str = "0"):
    """在线试听或下载 BGM 测试音频文件。

    - file: 文件名（仅限 BGM 测试目录内的文件，防目录穿越）
    - download=1: 以附件方式下载；download=0: 以 inline 方式在线播放
    """
    test_dir = _bgm_test_dir()

    # 防目录穿越：只允许文件名，不允许路径分隔符
    safe_name = os.path.basename(file)
    if not safe_name or safe_name != file:
        return {"success": False, "error": "非法文件名"}

    file_path = os.path.join(test_dir, safe_name)
    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        return {"success": False, "error": "文件不存在或为空"}

    supported_exts = (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac")
    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in supported_exts:
        return {"success": False, "error": "不支持的文件类型"}

    # MIME 类型映射
    mime_map = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".flac": "audio/flac",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
        ".aac": "audio/aac",
    }
    media_type = mime_map.get(ext, "application/octet-stream")

    disposition = "attachment" if download == "1" else "inline"
    return FileResponse(
        file_path,
        media_type=media_type,
        filename=safe_name,
        content_disposition_type=disposition,
    )


# ============================================================================
# BGM 混音后台任务（异步执行 + 轮询，避免长耗时 HTTP 超时）
# ============================================================================

_bgm_mix_jobs: dict[str, dict] = {}


@router.post("/bgm/mix")
def bgm_mix(body: BgmMixRequest):
    """启动 BGM 混音测试（后台异步执行）。

    BGM 混音涉及 STFT/ISTFT 等高 CPU 操作，单次测试可能耗时数分钟，
    同步 HTTP 请求会因浏览器/代理超时而断开。
    改为后台线程执行 + 前端轮询日志进度，返回 job_id 供前端查询。
    """
    from ..settings import settings as app_settings

    test_dir = _bgm_test_dir()
    input_path = os.path.join(test_dir, body.input_file)

    if not os.path.exists(input_path) or os.path.getsize(input_path) == 0:
        return {"success": False, "error": f"输入文件不存在或为空: {body.input_file}", "logs": ""}

    music_dir = app_settings.music_dir
    if not os.path.isdir(music_dir) or not any(os.listdir(music_dir)):
        return {
            "success": False,
            "error": f"音乐目录为空或不存在: {music_dir}，请先上传 BGM 音乐文件",
            "logs": "",
        }

    job_id = uuid.uuid4().hex[:8]
    job = {
        "job_id": job_id,
        "status": "starting",
        "result": None,
        "logs": "",
        "started_at": time.time(),
        "input_file": body.input_file,
        "_cap": None,  # _LogCapture 对象引用（供轮询实时读取日志）
    }
    _bgm_mix_jobs[job_id] = job

    # ── 捕获参数（闭包安全） ──
    _input_path = input_path
    _test_dir = test_dir
    _music_dir = music_dir
    _params = {
        "volume_offset_db": body.volume_offset_db,
        "highpass_freq": body.highpass_freq,
        "fade_duration_ms": body.fade_duration_ms,
        "min_volume_db": body.min_volume_db,
        "dyn_vol": body.dyn_vol,
        "spec_shape": body.spec_shape,
        "stereo_offset": body.stereo_offset,
        "ducking_mode": body.ducking_mode,
        "bgm_base_gain_db": body.bgm_base_gain_db,
        "sc_threshold_db": body.sc_threshold_db,
        "sc_threshold_offset_db": body.sc_threshold_offset_db,
        "sc_ratio": body.sc_ratio,
        "sc_attack_ms": body.sc_attack_ms,
        "sc_release_ms": body.sc_release_ms,
        "intro_outro_seconds": body.intro_outro_seconds,
    }
    _input_name = body.input_file

    def _run_mix_job():
        """后台线程：获取锁 → 应用配置 → 捕获日志 → 执行混音 → 存储结果。"""
        job_ref = _bgm_mix_jobs[job_id]
        cap = None
        try:
            ok, err = _acquire_pipeline_lock()
            if not ok:
                job_ref["status"] = "failed"
                job_ref["result"] = {"success": False, "error": err}
                job_ref["logs"] = err
                return

            from pipeline.config import apply_runtime_config
            config = _build_test_config()
            apply_runtime_config(config)

            with _capture_logs() as cap:
                job_ref["_cap"] = cap  # 供轮询端点实时读取
                print(f"[BGM测试] 混音测试开始: {_input_name}", flush=True)
                print(f"[BGM测试] 音乐目录: {_music_dir}", flush=True)
                print(
                    f"[BGM测试] 参数: vol_offset={_params['volume_offset_db']}dB, "
                    f"hp={_params['highpass_freq']}Hz, "
                    f"fade={_params['fade_duration_ms']}ms, "
                    f"dyn_vol={_params['dyn_vol']}, spec_shape={_params['spec_shape']}, "
                    f"ducking={_params['ducking_mode']}, base_gain={_params['bgm_base_gain_db']}dB, "
                    f"intro_outro={_params['intro_outro_seconds']}s",
                    flush=True,
                )
                job_ref["status"] = "running"

                output_name = "bgm_output_" + os.path.splitext(_input_name)[0] + ".mp3"
                output_path = os.path.join(_test_dir, output_name)

                from pipeline.bgm import mix_with_bgm
                t0 = time.time()
                ok_mix = mix_with_bgm(
                    _input_path,
                    output_path,
                    _music_dir,
                    volume_offset_db=_params["volume_offset_db"],
                    highpass_freq=_params["highpass_freq"],
                    fade_duration_ms=_params["fade_duration_ms"],
                    min_volume_db=_params["min_volume_db"],
                    dyn_vol=_params["dyn_vol"],
                    spec_shape=_params["spec_shape"],
                    stereo_offset=_params["stereo_offset"],
                    ducking_mode=_params["ducking_mode"],
                    bgm_base_gain_db=_params["bgm_base_gain_db"],
                    sc_threshold_db=_params["sc_threshold_db"],
                    sc_threshold_offset_db=_params["sc_threshold_offset_db"],
                    sc_ratio=_params["sc_ratio"],
                    sc_attack_ms=_params["sc_attack_ms"],
                    sc_release_ms=_params["sc_release_ms"],
                    intro_outro_seconds=_params["intro_outro_seconds"],
                )
                elapsed = time.time() - t0

                if ok_mix and os.path.exists(output_path):
                    size_mb = round(os.path.getsize(output_path) / (1024 * 1024), 2)
                    print(f"[BGM测试] ✓ 混音完成，耗时 {elapsed:.1f}s，输出: {output_name} ({size_mb} MB)", flush=True)
                    job_ref["status"] = "completed"
                    job_ref["result"] = {
                        "success": True,
                        "output_file": output_name,
                        "output_size_mb": size_mb,
                        "elapsed_seconds": round(elapsed, 1),
                        "message": f"✅ 混音成功！耗时 {elapsed:.1f}s，输出文件 {size_mb} MB",
                    }
                else:
                    print(f"[BGM测试] ✗ 混音失败", flush=True)
                    job_ref["status"] = "failed"
                    job_ref["result"] = {
                        "success": False,
                        "error": "混音失败，请查看日志",
                        "elapsed_seconds": round(elapsed, 1),
                    }
                job_ref["logs"] = _logs_text(cap)
        except ImportError as e:
            job_ref["status"] = "failed"
            job_ref["result"] = {"success": False, "error": f"Pipeline 模块导入失败: {e}"}
            job_ref["logs"] = _logs_text(cap) if cap else str(e)
        except Exception as e:
            tb = traceback.format_exc()
            job_ref["status"] = "failed"
            job_ref["result"] = {"success": False, "error": f"{type(e).__name__}: {e}", "traceback": tb}
            job_ref["logs"] = _logs_text(cap) if cap else str(e)
        finally:
            job_ref["_cap"] = None  # 清除引用，后续读取 job["logs"]
            job_ref["logs"] = _logs_text(cap) if cap else job_ref.get("logs", "")
            _release_pipeline_lock()
            # 清理旧任务（保留最近 10 个）
            if len(_bgm_mix_jobs) > 10:
                oldest = sorted(_bgm_mix_jobs.items(), key=lambda x: x[1].get("started_at", 0))
                for k, _ in oldest[:-10]:
                    if k != job_id:
                        _bgm_mix_jobs.pop(k, None)

    thread = threading.Thread(target=_run_mix_job, daemon=True)
    thread.start()

    return {"job_id": job_id, "status": "starting", "message": "BGM 混音测试已启动，正在后台执行..."}


@router.get("/bgm/mix/status")
def bgm_mix_status(job_id: str = ""):
    """轮询 BGM 混音测试进度。

    返回当前状态（starting/running/completed/failed）、实时日志和最终结果。
    日志在运行期间从 _LogCapture 对象实时读取，完成后从 job["logs"] 读取。
    """
    job = _bgm_mix_jobs.get(job_id)
    if not job:
        return {"status": "not_found", "error": f"Job not found: {job_id}", "logs": ""}

    # 运行期间从 _LogCapture 对象实时读取日志
    cap = job.get("_cap")
    if cap and cap.text:
        logs = cap.text[-12000:] if len(cap.text) > 12000 else cap.text
    else:
        logs = job.get("logs", "")

    return {
        "job_id": job_id,
        "status": job["status"],
        "result": job["result"],
        "logs": logs,
        "elapsed_seconds": round(time.time() - job["started_at"], 1),
    }