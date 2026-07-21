"""应用配置 — 通过环境变量注入。"""

from __future__ import annotations

import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局应用配置。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # ─── 数据库连接 ───
    database_url: str = "postgresql://audiobook_app:changeme@localhost:5432/audiobook"

    # ─── 自建数据库密码 ───
    postgres_password: str = "changeme_strong_password"

    # ─── Web 服务 ───
    secret_key: str = "dev_secret_key_change_in_production"
    base_url: str = "http://localhost:59386"
    app_password: str = "inriynisse"

    # ─── 文件路径 ───
    output_root: str = "/data/output"
    music_dir: str = "/data/music"

    # ─── YouTube OAuth ───
    youtube_scopes: str = "https://www.googleapis.com/auth/youtube"
    oauth_state_ttl_seconds: int = 600


settings = Settings()


def get_dsn() -> str:
    """返回 PostgreSQL DSN 连接串。

    优先级：
    1. 环境变量 DATABASE_URL（由 docker-compose 注入）
    2. settings.database_url
    """
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        dsn = settings.database_url

    # 兼容 postgresql+psycopg:// 前缀
    if dsn.startswith("postgresql+psycopg://"):
        dsn = dsn.replace("postgresql+psycopg://", "postgresql://", 1)
    return dsn
