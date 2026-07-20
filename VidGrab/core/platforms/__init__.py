"""平台提取子包（core/platforms）

B站 / YouTube 各自的提取逻辑放在这里的独立模块里，互不干扰：
  - bilibili.py ：B站提取
  - youtube.py  ：YouTube 提取
  - _ytdlp.py   ：两者共用的 yt-dlp 工具（下划线开头 = 内部模块，不直接对外）
"""

from .bilibili import extract_bilibili, get_bilibili_pages
from .youtube import extract_youtube

__all__ = ["extract_bilibili", "get_bilibili_pages", "extract_youtube"]
