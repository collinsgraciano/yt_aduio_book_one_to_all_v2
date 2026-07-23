"""测试请求模型 — tests.py 和 tests_hf.py 共享。"""

from __future__ import annotations

from pydantic import BaseModel


class AiTestRequest(BaseModel):
    book_name: str = "测试书籍：星光彼岸"
    book_desc: str = "这是一本关于勇气与冒险的奇幻小说，讲述主角穿越星海寻找自我救赎的故事。"
    test_type: str = "seo"  # seo | cover | both
    resolution: str = "1080p"


class UploadTestRequest(BaseModel):
    channel_name: str = ""  # 留空则用全局 YOUTUBE_CHANNEL_NAME


class TgDownloadTestRequest(BaseModel):
    file_id: str = ""
    bot_user_id: int | None = None  # 从样本获取，用于匹配正确的 Bot Token
    bot_id: int | None = None  # 从样本获取（备用匹配）
    do_download: bool = False  # 是否实际下载文件（getFile 验证之外）


class BgmDownloadRequest(BaseModel):
    count: int = 1       # 下载几个章节
    book_id: str = ""    # 指定书籍 ID（留空随机）


class BgmMixRequest(BaseModel):
    input_file: str = ""
    volume_offset_db: int = -25
    highpass_freq: int = 150
    fade_duration_ms: int = 3000
    min_volume_db: int = -40
    dyn_vol: bool = True
    spec_shape: bool = True
    stereo_offset: float = 0.0
    ducking_mode: str = "sidechain"
    bgm_base_gain_db: int = -15
    sc_threshold_db: int = -30
    sc_threshold_offset_db: int = -5
    sc_ratio: int = 8
    sc_attack_ms: int = 5
    sc_release_ms: int = 400
    intro_outro_seconds: int = 3
