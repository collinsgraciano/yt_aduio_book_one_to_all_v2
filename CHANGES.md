# v2/ 完整重构变更摘要

原代码完全不动，所有重构在本目录执行。

---

## Phase 1: 清理死代码

| 文件 | 变更 |
|------|------|
| `pipeline/audio.py` | 删除 `download_file_with_wget`、`download_file_with_requests`；移除未使用的 `tempfile`、`urlparse`、`urlunparse` 导入 |
| `pipeline/seo.py` | 移除未使用的 `from .audio import download_file` |
| `pipeline/config.py` | **修复 latent bug**：实现缺失的 `normalize_runtime_source` 函数 |
| `pipeline/config.py` | 修复 `collect_runtime_config_snapshot` 默认值不匹配 + 缩进修复 |
| `backend/api/tests.py` | 移除未使用的 `from pydantic import BaseModel as _BM` |
| `backend/models/` | 移除未使用的 `ChannelConfigUpdate` 类、`Any`/`Field` 导入 |
| `hf_workers/vps_relay/app.py` | **修复 bug**：`_reset_stuck_jobs` 添加 `return count` |
| `hf_workers/vps_relay/app.py` | **修复 bug**：`_YT_DAILY_PUBLISH_LIMIT` 硬编码改用 panel 配置值 |

## Phase 2: 消除重复代码

| 重复项 | 消除方式 | 份数 |
|--------|----------|------|
| `_extract_youtube_video_id` | 提取到 `runtime.py` 的 `extract_youtube_video_id` | 2→1 |
| 日志捕获类 | 新建 `pipeline/log_capture.py` | 3→1 |
| 测试请求模型 (5个) | 新建 `backend/api/test_models.py` | 2→1 |
| `_get_vps_relay_url` | 新建 `backend/api/shared.py` | 2→1 |
| `download_file_from_url` | 改用 `audio.download_file` | 2→1 |
| OAuth UPSERT SQL | 提取为 `_upsert_youtube_credentials` | 3→1 |
| TG 缓存 `shutil.copy2` 循环 | 新建 `pipeline/stages.py` 的 `preset_tg_cached_denoised` | 2→1 |
| POSTGRES_DSN 注入 | 提取到 `config_service.resolve_postgres_dsn()` | 3→1 |
| `_update_worker_stats` SQL | 提取到 `hf_workers/shared.py` 的 `upsert_worker_stats` | 2→1 |
| PROJECT_FLAG status 解析 | 统一使用 `normalize_text_items`（pipeline/runtime.py + hf_workers/shared.py） | 3→1 |

## Phase 3: 中等架构改进

| 改进项 | 文件 | 变更 |
|--------|------|------|
| 无限重试加保护 | `pipeline/cover.py` | `while True` → `while attempt < 10` |
| 无限重试加保护 | `pipeline/seo.py` | `while True` → `while attempt < 10` |
| Pydantic v1→v2 | `backend/models/*.py` | `class Config` → `model_config = ConfigDict(...)` |
| Pydantic v1→v2 | `backend/models/book.py` | `tags: list[str] = []` → `Field(default_factory=list)` |
| Pydantic v1→v2 | `backend/settings.py` | `class Config` → `model_config = SettingsConfigDict(...)` |
| async→sync | `backend/api/*.py` (6个文件, 41个路由) | `async def` → `def` |
| **拆分 tests.py** | `backend/api/` | 1137行 → 5个文件: `tests.py`(聚合) + `_test_shared.py`(106行) + `tests_ai.py`(231行) + `tests_upload.py`(146行) + `tests_tg_download.py`(197行) + `tests_bgm.py`(478行) |

## 新增文件

| 文件 | 用途 |
|------|------|
| `pipeline/log_capture.py` | 共享日志捕获（LogCapture, CapturingHandler, capture_logs, logs_text） |
| `pipeline/stages.py` | Pipeline 共享处理阶段（preset_tg_cached_denoised） |
| `backend/api/test_models.py` | 测试请求模型（5个 Pydantic 类） |
| `backend/api/shared.py` | API 共享工具（get_vps_relay_url） |
| `backend/api/_test_shared.py` | 测试共享逻辑（锁、配置构建、日志捕获导入） |
| `backend/api/tests_ai.py` | AI 生成测试端点 |
| `backend/api/tests_upload.py` | YouTube 上传测试端点 |
| `backend/api/tests_tg_download.py` | TG 音频下载测试端点 |
| `backend/api/tests_bgm.py` | BGM 混音测试端点 |
| `hf_workers/shared.py` | HF Workers 共享工具（normalize_text_items, upsert_worker_stats） |

## 未改动

- monkey-patching 架构保留（podcast.py 注入机制不变）
- 核心 pipeline 流程不重写
- `backend/main.py` 的页面路由 async def 保留（仅渲染模板，无 DB 调用）
- books.py 的 service 层提取未执行（books.py 已恢复 + async→sync 修复，但未拆分 SQL 到 service 层）
