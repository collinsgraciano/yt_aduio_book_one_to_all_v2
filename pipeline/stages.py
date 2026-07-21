"""Pipeline 共享处理阶段 — process_standard_book 和 process_split_part 共用的逻辑。

当前包含：
- preset_tg_cached_denoised: TG 缓存章节预置到降噪目录（两边 verbatim 重复）
"""

from __future__ import annotations

import os
import shutil

from .runtime import log


def preset_tg_cached_denoised(chapter_items, chapter_paths, tg_cached_indices, denoised_targets):
    """将 TG 缓存章节（已降噪）提前复制到 denoised 目录，DeepFilter 会自动跳过已存在文件。

    在 process_standard_book 和 process_split_part 中完全一致的逻辑，提取为共享函数。
    """
    if not tg_cached_indices:
        return
    for i, item in enumerate(chapter_items):
        if item["source_index"] in tg_cached_indices and i < len(chapter_paths):
            src = chapter_paths[i]
            dst = denoised_targets[i]
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if not os.path.exists(dst) or os.path.getsize(dst) == 0:
                shutil.copy2(src, dst)
                log.info("[TG缓存] 已预置降噪文件: %s", os.path.basename(dst))
