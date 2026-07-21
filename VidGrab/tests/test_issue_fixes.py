"""针对用户本轮 4 项问题的回归单测（无需重依赖即可跑）。

覆盖：
- 语言检测：中文→zh / 英文→en / 空→""（修复英文视频被错误加中文标点）
- 分P 文件名：多P 时带 -P{n} 后缀，且正文标题的「P{n} · 」前缀在文件名中被剥离（不重复）
- 全选：_select_formats("all") 返回全部 5 种格式
"""

import os
import sys

# 把项目根（tests/ 的上一级）加入 sys.path，使 `from core...` / `import skill.main` 可解析
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import types


def _seg(text):
    class S:
        def __init__(self, t):
            self.text = t
    return S(text)


def test_detect_language():
    from core.platforms.bilibili import _detect_language

    zh = [_seg("这是一段中文语音识别的文字内容，讲得非常好。")]
    en = [_seg("This is an English transcript of the audio speech.")]
    assert _detect_language(zh) == "zh"
    assert _detect_language(en) == "en"
    assert _detect_language([]) == ""


def test_safe_title_multipp_strips_prefix():
    import re
    from core import exporter

    class FakePlatform:
        value = "bilibili"
    class T:
        title = "P1 · 道德经演讲录 - 第一集"
        author = "白岩松"
        platform = FakePlatform()
        video_id = "BV1CW411P7rb"
        is_collection = True
        page_index = 0  # -> P1

    name = exporter._safe_title(T(), "全文文案")
    # 新规则：视频名-P1-摘要-全文文案（Pxx 紧跟视频名、在「摘要」之前）
    assert "-P1-" in name, name
    assert name.endswith("-全文文案"), name
    assert "P1 · " not in name, name
    assert "全文文案" in name


def test_safe_title_singlep_no_suffix():
    from core import exporter

    class FakePlatform:
        value = "bilibili"
    class T:
        title = "普通单P视频标题"
        author = ""
        platform = FakePlatform()
        video_id = "BVxxxx"
        is_collection = False
        page_index = 0

    name = exporter._safe_title(T(), "精简")
    assert "-P" not in name, name
    # 单P 新格式：视频名-摘要-模式（用精确字符串锁定，防止回归）
    assert name == "普通单P视频标题-摘要-精简", name


def test_select_formats_all():
    # 直接测 CLI/forced 分支的「全选」
    import skill.main as m

    assert m._select_formats(forced="all") == ["markdown", "html", "docx", "pdf", "image"]
    assert m._select_formats(forced="全选") == ["markdown", "html", "docx", "pdf", "image"]


def test_language_instruction_follows_video():
    # 摘要语言必须跟随视频真实语种（用户明确要求：不能一律中文）
    from core import summarizer

    zh = summarizer._language_instruction("zh")
    en = summarizer._language_instruction("en")
    auto = summarizer._language_instruction("")
    auto2 = summarizer._language_instruction("auto")

    # 中文视频：简体中文 + 中文标点（满足用户硬性要求）
    assert "简体中文" in zh
    assert "中文标点" in zh or "逗号" in zh
    # 英文视频：英文输出，绝不强制中文
    assert "English" in en
    assert "简体中文" not in en
    # 未知/auto/空：跟随视频真实语种，不强行统一成中文
    assert "跟随" in auto and "跟随" in auto2


if __name__ == "__main__":
    test_detect_language()
    test_safe_title_multipp_strips_prefix()
    test_safe_title_singlep_no_suffix()
    test_select_formats_all()
    test_language_instruction_follows_video()
    print("ALL ISSUE-FIX TESTS PASSED")
