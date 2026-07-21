from ._test_shared import (
    router, logger, _acquire_pipeline_lock, _build_test_config,
    _release_pipeline_lock, _capture_logs, _logs_text,
    AiTestRequest, UploadTestRequest, TgDownloadTestRequest,
    BgmDownloadRequest, BgmMixRequest,
)

@router.post("/ai")
def test_ai(body: AiTestRequest):
    """测试 AI 生成（SEO 文案 / 封面图片）。

    调用 pipeline 的实际生成函数（单轮尝试，非无限重试），
    返回生成结果、错误信息和运行日志。
    """
    ok, err = _acquire_pipeline_lock()
    if not ok:
        return {"success": False, "error": err, "logs": ""}

    cap = None
    try:
        from pipeline.config import apply_runtime_config
        config = _build_test_config()
        apply_runtime_config(config)

        with _capture_logs() as cap:
            print(f"[测试] AI 生成测试开始（类型: {body.test_type}，书名: {body.book_name}）", flush=True)
            results = {}
            errors = []

            # ── SEO 文案测试 ──
            if body.test_type in ("seo", "both"):
                print("[测试] → 开始 SEO 文案测试...", flush=True)
                try:
                    from pipeline.cover import (
                        _get_modelscope_usage_token_pool,
                        _run_text_task_with_model_fallback,
                        _get_modelscope_text_model_sequence,
                        _create_modelscope_openai_client,
                        _extract_modelscope_chat_content,
                        _strip_markdown_code_fences,
                    )

                    token_pool = _get_modelscope_usage_token_pool(
                        config.get("MODELSCOPE_TOKEN", ""), "text"
                    )
                    if not token_pool:
                        print("[测试] ✗ MODELSCOPE_TOKEN 未配置，跳过 SEO 测试", flush=True)
                        results["seo"] = {
                            "success": False,
                            "error": "MODELSCOPE_TOKEN 未配置，无法生成 SEO 文案",
                        }
                        errors.append("SEO: MODELSCOPE_TOKEN 未配置")
                    else:
                        print(f"[测试] ✓ Token 池就绪（{len(token_pool)} 个），开始调用 AI...", flush=True)

                        def _seo_runner(current_token, text_model):
                            client = _create_modelscope_openai_client(current_token)
                            system_prompt = (
                                "你是YouTube运营专家。根据书名和简介返回JSON，"
                                '包含 title(标题)、Description(描述)、label(标签)。'
                                "只返回JSON，不要其他文字。"
                            )
                            user_prompt = f"书名：[{body.book_name}]\n简介：[{body.book_desc}]"
                            response = client.chat.completions.create(
                                model=text_model,
                                messages=[
                                    {"role": "system", "content": system_prompt},
                                    {"role": "user", "content": user_prompt},
                                ],
                            )
                            reply = _strip_markdown_code_fences(
                                _extract_modelscope_chat_content(response)
                            )
                            return json.loads(reply)

                        seo_dict, gen_errors = _run_text_task_with_model_fallback(
                            task_label="SEO测试",
                            token_pool=token_pool,
                            attempt=1,
                            runner=_seo_runner,
                            model_sequence=_get_modelscope_text_model_sequence(),
                        )
                        if seo_dict:
                            print("[测试] ✓ SEO 文案生成成功", flush=True)
                            results["seo"] = {
                                "success": True,
                                "content": seo_dict,
                            }
                        else:
                            err_summary = " | ".join(gen_errors[-5:]) if gen_errors else "未知错误"
                            print(f"[测试] ✗ SEO 文案生成失败: {err_summary}", flush=True)
                            results["seo"] = {"success": False, "error": err_summary}
                            errors.append(f"SEO: {err_summary}")
                except Exception as e:
                    tb = traceback.format_exc()
                    results["seo"] = {
                        "success": False,
                        "error": f"{type(e).__name__}: {e}",
                        "traceback": tb,
                    }
                    errors.append(f"SEO 异常: {type(e).__name__}: {e}")

            # ── 封面图片测试 ──
            if body.test_type in ("cover", "both"):
                print("[测试] → 开始封面图片测试...", flush=True)
                try:
                    from pipeline.cover import (
                        _dispatch_cover_text,
                        _dispatch_cover_image,
                        _get_modelscope_usage_token_pool,
                    )

                    token = config.get("MODELSCOPE_TOKEN", "")
                    text_pool = _get_modelscope_usage_token_pool(token, "text")
                    image_pool = _get_modelscope_usage_token_pool(token, "image")

                    if not text_pool and not image_pool:
                        print("[测试] ✗ MODELSCOPE_TOKEN 未配置，跳过封面测试", flush=True)
                        results["cover"] = {
                            "success": False,
                            "error": "MODELSCOPE_TOKEN 未配置，无法生成封面",
                        }
                        errors.append("封面: MODELSCOPE_TOKEN 未配置")
                    else:
                        # 1. 生成绘图提示词
                        print("[测试] → 生成绘图提示词...", flush=True)
                        draw_prompt, prompt_errors = _dispatch_cover_text(
                            book_name=body.book_name,
                            book_desc=body.book_desc,
                            text_token_pool=text_pool,
                            prompt_generation_attempt=1,
                        )
                        if not draw_prompt:
                            print("[测试] ✗ 绘图提示词生成失败", flush=True)
                            err_summary = (
                                " | ".join(prompt_errors[-5:])
                                if prompt_errors
                                else "提示词生成失败"
                            )
                            results["cover"] = {"success": False, "error": err_summary}
                            errors.append(f"封面提示词: {err_summary}")
                        else:
                            print(f"[测试] ✓ 提示词生成成功，开始生成图片（{body.resolution}）...", flush=True)
                            results["cover"] = {"draw_prompt": draw_prompt}

                            # 2. 生成封面图片
                            cover_path = os.path.join(
                                tempfile.gettempdir(),
                                f"test_cover_{int(time.time())}.jpg",
                            )
                            image_ok, image_errors = _dispatch_cover_image(
                                output_path=cover_path,
                                draw_prompt=draw_prompt,
                                resolution=body.resolution,
                                image_token_pool=image_pool,
                            )
                            if image_ok and os.path.exists(cover_path):
                                size = os.path.getsize(cover_path)
                                print(f"[测试] ✓ 封面图片生成成功（{size // 1024} KB）", flush=True)
                                preview = ""
                                try:
                                    from PIL import Image

                                    with Image.open(cover_path) as img:
                                        img.thumbnail((400, 400))
                                        buf = io.BytesIO()
                                        img.convert("RGB").save(
                                            buf, format="JPEG", quality=70
                                        )
                                        preview = (
                                            "data:image/jpeg;base64,"
                                            + base64.b64encode(buf.getvalue()).decode()
                                        )
                                except Exception:
                                    pass
                                results["cover"].update({
                                    "success": True,
                                    "file": cover_path,
                                    "size": size,
                                    "preview": preview,
                                })
                            else:
                                err_summary = (
                                    " | ".join(image_errors[-5:])
                                    if image_errors
                                    else "图片生成失败"
                                )
                                print(f"[测试] ✗ 封面图片生成失败: {err_summary}", flush=True)
                                results["cover"].update({
                                    "success": False,
                                    "error": err_summary,
                                })
                                errors.append(f"封面图片: {err_summary}")
                except Exception as e:
                    tb = traceback.format_exc()
                    results["cover"] = {
                        "success": False,
                        "error": f"{type(e).__name__}: {e}",
                        "traceback": tb,
                    }
                    errors.append(f"封面异常: {type(e).__name__}: {e}")

            print(f"[测试] AI 测试完成，共 {len(errors)} 个错误", flush=True)

        return {
            "success": len(errors) == 0,
            "results": results,
            "logs": _logs_text(cap),
            "errors": errors,
            "config_used": {
                "MODELSCOPE_TOKEN": "***已配置***" if config.get("MODELSCOPE_TOKEN") else "未配置",
                "API_PRIORITY_ORDER": config.get("API_PRIORITY_ORDER", ""),
                "ENABLE_COVER_GENERATION": config.get("ENABLE_COVER_GENERATION"),
                "ENABLE_SEO_GENERATION": config.get("ENABLE_SEO_GENERATION"),
            },
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
