# AI 摘要生成模块
# Phase 0 — 用 OpenAI 兼容客户端调用 DeepSeek（或 OpenAI），把 Transcript 生成结构化 Summary
"""把 Transcript（视频文字 + 时间轴）交给 AI，生成结构化 Summary。

入口：generate_summary(transcript, ai_config, proxy="") -> Summary
  - transcript.segments 必须有内容（字幕或转录结果）
  - ai_config：AIConfig（provider / api_key / model）
  - 返回与 templates.SUMMARY_MD_TEMPLATE 对应的 Summary

provider 支持：
  - "deepseek" / "siliconflow" / 任意：用 ai.base_url 指定 OpenAI 兼容地址
  - "openai"：走 OpenAI 官方接口
后续要接更多模型（如 Claude / Gemini），在此做分发即可，调用方无感。

分块策略（关键）：长视频原文可能远超模型单次上下文，整段硬塞会被截断、
导致摘要只覆盖开头。这里按字符预算把 segments 切成连续时间块，逐块做「逻辑分章」，
最后合并去重成覆盖全片的逻辑章节，保证：① 覆盖完整时间线 ② 不撑爆上下文
③ 按内容逻辑分章（而非按固定时间间隔平铺）。
"""

from __future__ import annotations

import json
import os
from typing import List, Optional

from . import DetailedRow, GoldenQuote, Segment, Summary, Transcript, format_duration, format_timestamp
from .config import AIConfig
from .lang import _detect_language
from .ratelimit import RateLimiter


# 单块摘要的系统提示：按【内容逻辑】分章，而非按时间平铺
_CHUNK_SYSTEM_PROMPT = """{language_instruction}

你是一个专业的视频内容分析助手。用户给你一段带时间戳的视频文字稿（可能来自字幕或语音转录），这是**整个视频的其中一段**。
请按【内容逻辑】把本段整理成若干「章节」，输出合法 JSON 对象，且只包含以下字段：

{{
  "detailed": [                                        // 按内容逻辑分章
    {{"timestamp": "MM:SS", "point": "该章主题（短句，10字内）", "content": "该章核心内容（1-3句精炼总结，提炼观点与结论，不要照抄原文）"}},
    ...
  ],
  "golden_quotes": [{{"timestamp": "MM:SS", "text": "金句内容"}}, ...]  // 本段最值得摘抄/记住的 1-3 句（原话或凝练观点），timestamp 是该句在文字稿里真实出现过的 MM:SS
}}

要求：
- {language_instruction}（不要输出其他语言）。
- **按内容逻辑分章**：同一话题合并成一章，不同话题分开成不同章；不要按固定时间间隔（如每 1 分钟一行）平铺。**尽量合并同类话题，章节越少越好，但不要遗漏关键内容**。
{mode_instruction}
{query_instruction}
- **均匀覆盖本段时间线**：不要只详写开头而忽略结尾；本段时间范围内每个有意义的时间段都应有所体现，避免整段遗漏。
- 每章的 timestamp 必须是该章开头在文字稿里真实出现过的 MM:SS 时间戳（不要编造）。
- 每章 point 是该章主题短句；content 是对该章的**总结提取**（1-3 句核心结论，不要逐句复述原文）。
- **所有视频相关内容都要加粗**：在 point、content、golden_quotes 中，**所有与视频核心内容相关的关键术语、人名、地名、概念、产品名、专有名词**都必须用 **双星号** 包裹（如 **查理曼**、**达康书记**、**重骑兵**、**Blender**），便于快速扫读和抓住视频核心信息。不加颜色。
- golden_quotes 提炼本段最值得记住的话，金句中包含的关键术语也要加粗；每条必须带 timestamp（MM:SS），是该金句在文字稿里真实出现的时间点（不要编造）。
- 只输出 JSON，不要任何额外解释或 markdown 代码块围栏。"""


# 合并阶段的系统提示：把各段「分章草稿」合并成全片稳定的逻辑章节
_MERGE_SYSTEM_PROMPT = """{language_instruction}

你是一个视频总结助手。用户给你一整段视频按时间顺序的「分章草稿」（来自各段摘要合并，可能在段边界处把同一话题切开了）。
请输出整片最终的结构化摘要，必须是合法 JSON 对象，且只包含以下字段：

{{
  "content_overview": "一句话概述整个视频在讲什么（20-40字），关键术语用 **加粗**",
  "detailed": [                                        // 全片最终逻辑章节
    {{"timestamp": "MM:SS", "point": "该章主题（短句，10字内，关键词加粗）", "content": "该章核心内容（1-3句精炼总结，关键词加粗）"}},
    ...
  ],
  "golden_quotes": [{{"timestamp": "MM:SS", "text": "金句内容（关键词加粗）"}}, ...],  // 从给定金句里挑最值得的 2-5 条，保留各自 timestamp
}}

要求：
- {language_instruction}（不要输出其他语言）。
- **合并要激进**：把相邻或相同话题的草稿尽可能合并成更少的章节。不要每个小话题单独成章——只有当话题确实有显著转折时才分开。目标是用最少的章节覆盖全片核心内容，但不遗漏关键信息。
{mode_instruction}
{query_instruction}
- **时间轴均匀分布**：确保最终章节在时间轴上尽量均匀分布，覆盖视频完整时长；避免前段密集、后段稀疏。若某 10+ 分钟区间完全没有章节，应补一章概括该时段核心内容（可较简略，但不要整段遗漏）。
- 不规定具体章节数量——根据内容自然决定，但应当远少于输入的草稿数量。
- 不要按固定时间间隔切；按主题合并。
- 每章 timestamp 用该章开头真实出现过的 MM:SS。
- 每章 content 是 1-3 句精炼总结。
- **所有视频相关内容都要加粗**：在 content_overview、point、content、golden_quotes 中，**所有与视频核心内容相关的关键术语、人名、地名、概念、产品名、专有名词**都必须用 **双星号** 包裹（Markdown 加粗）。这是最重要的输出要求之一——让读者快速抓住视频核心信息。不加颜色。
- golden_quotes 每条必须带 timestamp（MM:SS），是该金句在视频里真实出现的时间点。
- 只输出 JSON，不要额外解释或 markdown 代码块围栏。"""


_MODE_INSTRUCTIONS = {
    "concise": "",
    "detailed": "- **详细模式**：除了核心大重点，还要把每个大重点下的**次重点**也作为独立 detailed 行输出（时间戳与大重点相同或相邻），用 point 字段标识为次重点。整段内容要比精简模式更丰满，但不堆砌无关细节。",
    "query": "- **自定义模式**：用户只关心特定主题，请只提取与下面主题相关的重点，忽略无关内容。",
}


_CHUNK_MAX_CHARS = 12000  # 单块字符预算：调小以「按时间窗强制覆盖」全片。
# 字幕文字密度约 1.5-2 万字符/40分钟，40000 会让整片塞进单个 chunk，
# 模型易「重开头、轻结尾」导致后半段被漏掉（内容脉络前密后疏）。
# 12000 让 40 分钟视频自然切成 2 块（各覆盖约一半时间线），强制后半段也被单独概括；
# 合并阶段再去重。块数增加有限（长视频多几次调用），但覆盖率与均衡度明显改善。


def _chunk_segments(segments: List[Segment], max_chars: int = _CHUNK_MAX_CHARS) -> List[List[Segment]]:
    """把 segments 按字符预算切成连续的块（保持时间顺序）。"""

    chunks: List[List[Segment]] = []
    cur: List[Segment] = []
    cur_chars = 0
    for s in segments:
        line_len = len(s.text) + 10  # 估算 [MM:SS] 前缀
        if cur and cur_chars + line_len > max_chars:
            chunks.append(cur)
            cur = []
            cur_chars = 0
        cur.append(s)
        cur_chars += line_len
    if cur:
        chunks.append(cur)
    return chunks


def _build_chunk_text(chunk: List[Segment]) -> str:
    return "\n".join(f"[{format_timestamp(s.start)}] {s.text}" for s in chunk)


def _dedupe_quotes(quotes: List[GoldenQuote], cap: int = 5) -> List[GoldenQuote]:
    """去重金句（按 text 精确匹配，保留顺序与时间戳），最多 cap 条。"""

    seen = set()
    out: List[GoldenQuote] = []
    for q in quotes:
        text = (q.text or "").strip()
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        out.append(q)
        if len(out) >= cap:
            break
    return out


def _parse_quotes(raw) -> List[GoldenQuote]:
    """把 AI 返回的 golden_quotes（可能是 [str] 或 [{timestamp,text}]）规整成 GoldenQuote 列表。"""

    out: List[GoldenQuote] = []
    for q in (raw or []):
        if isinstance(q, dict):
            ts = str(q.get("timestamp", "")).strip()
            txt = str(q.get("text", "")).strip()
        else:
            ts, txt = "", str(q).strip()
        if txt:
            out.append(GoldenQuote(timestamp=ts, text=txt))
    return out


def _client_for(ai: AIConfig, proxy: str):
    """按 provider 构造 OpenAI 兼容客户端。优先级：ai.base_url > provider 默认地址。"""

    from openai import OpenAI

    if proxy:
        os.environ.setdefault("HTTP_PROXY", proxy)
        os.environ.setdefault("HTTPS_PROXY", proxy)

    if ai.base_url:
        return OpenAI(api_key=ai.api_key, base_url=ai.base_url.rstrip("/"))
    if ai.provider == "deepseek":
        return OpenAI(api_key=ai.api_key, base_url="https://api.deepseek.com")
    if ai.provider == "openai":
        return OpenAI(api_key=ai.api_key)
    return OpenAI(api_key=ai.api_key)


def _parse_json(obj_text: str) -> dict:
    """容忍式解析：先直接 json.loads，失败再剥离 ```json 围栏 / 截取首尾花括号。"""

    obj_text = obj_text.strip()
    try:
        return json.loads(obj_text)
    except json.JSONDecodeError:
        pass
    if obj_text.startswith("```"):
        obj_text = obj_text.strip("`")
        if obj_text.lower().startswith("json"):
            obj_text = obj_text[4:]
        obj_text = obj_text.strip()
        try:
            return json.loads(obj_text)
        except json.JSONDecodeError:
            pass
    s, e = obj_text.find("{"), obj_text.rfind("}")
    if s != -1 and e != -1 and e > s:
        try:
            return json.loads(obj_text[s : e + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"AI 返回的摘要不是合法 JSON：{obj_text[:200]}")


def _raw_content(raw) -> str:
    """从 with_raw_response 的返回里稳定取出 message content 文本。

    兼容不同 openai SDK 版本：raw.parse() 可能直接返回 ChatCompletion，
    也可能返回带 .data 的 ParsedResponse；极端情况下退化为直接解析原始 JSON。
    """

    try:
        parsed = raw.parse()
        completion = getattr(parsed, "data", parsed)
        return (completion.choices[0].message.content or "")
    except Exception:  # noqa: BLE001
        pass
    # 兜底：直接解析原始响应体
    try:
        import json
        obj = json.loads(raw.content.decode("utf-8"))
        return (obj["choices"][0]["message"]["content"] or "")
    except Exception:  # noqa: BLE001
        return ""


def _language_instruction(language: str) -> str:
    """摘要输出语言指令：跟随视频真实语种动态变化（用户明确要求）。

    - 中文视频（zh）：简体中文 + 中文标点（用户硬性要求：中文必须简体 + 逗号句号断句）。
    - 英文视频（en）：英文输出。
    - 未知/auto/空：先在 generate_summary 入口处用 _detect_language 根据实际文本
      重新判定为 zh/en，不应以空值进入 LLM；若仍为空，则强制要求「先判断视频语种再输出」。

    全文文案（transcript）跟随音频真实语种，由 _restore_punctuation 的语种守卫控制，
    不在此处处理。
    """
    lang = (language or "").lower()
    if lang == "en":
        return (
            "Output entirely in English, with natural English punctuation and sentence breaks. "
            "All narrative text, chapter points, content summaries, and golden quotes must be in English. "
            "Do not output Chinese except for proper nouns that are originally in Chinese."
        )
    if lang == "zh":
        return (
            "全部用简体中文输出，并使用中文标点（逗号、句号、问号、感叹号、顿号等）正常断句；"
            "涉及外文专有名词可保留原文（如英文术语、人名），但叙述语言必须是简体中文。"
        )
    # 防御：空/auto/未知不应直接落到 LLM，generate_summary 会先做一次 _detect_language。
    # 若仍为空，则给出最保守指令：先判断再输出，避免模型默认中文。
    return (
        "请先判断视频文字稿的主要语种，然后使用与文字稿相同的语言输出；"
        "若文字稿主要是中文，则用简体中文并加中文标点断句；"
        "若主要是英文，则全部用英文输出（包括章节要点、内容总结、金句）。"
        "不要混合语言，不要擅自把英文内容翻译成中文。"
    )


# 中文（CJK）常用标点；用于判断转录文本是否已具备基本断句标点
_CJK_PUNCT = set("，。、；：！？“”‘’（）《》—…·")


def _needs_punctuation(text: str) -> bool:
    """文本中基本没有 CJK 标点时才需要补标点。"""

    return not any(ch in _CJK_PUNCT for ch in (text or ""))


def _restore_punctuation(text: str, client, ai: AIConfig, proxy: str = "") -> str:
    """轻量标点恢复：仅补中文标点，不改字、不增删内容、不动时间戳。

    仅在 transcript.language=='zh' 且文本基本无标点时调用；失败（无网络/异常）
    一律回退为原文本，绝不影响主流程。
    """

    if not text or not _needs_punctuation(text):
        return text
    sys_p = (
        "你是一个严谨的中文标点校对助手。下面是一段带 [MM:SS] 时间戳的中文语音识别原文。"
        "请【仅】补上缺失的中文标点（逗号、句号、问号、感叹号、顿号等），让句子可以正常断句；"
        "【绝对不要】改动、增删任何汉字或时间戳，【不要】输出任何解释或多余文字。"
        "直接返回补好标点的原文。"
    )
    user = text
    try:
        from .ratelimit import RateLimiter

        rl = RateLimiter(tier=ai.tier, provider=ai.provider, model=ai.model)
        rl.wait_before_call()
        raw = client.chat.completions.with_raw_response.create(
            model=ai.model,
            messages=[
                {"role": "system", "content": sys_p},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            max_tokens=max(len(text) * 2, 2048),
        )
        return _raw_content(raw).strip() or text
    except Exception:  # noqa: BLE001
        return text


def _call_llm(client, ai: AIConfig, system_prompt: str, user_msg: str,
             max_tokens: int, proxy: str = "", rate_limiter: RateLimiter = None,
             retries: int = 3) -> dict:
    """统一发起一次聊天补全并解析 JSON。遇到 503/429 等临时错误自动重试。

    rate_limiter：传入则会在成功后读取响应头细化限流间隔；限流时优先读 Retry-After。
    """

    import time

    for attempt in range(retries):
        try:
            # 用 with_raw_response 拿到原生响应头，让限流头（x-ratelimit-*）真正生效
            raw = client.chat.completions.with_raw_response.create(
                model=ai.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                response_format={"type": "json_object"},
                temperature=0.3,
                max_tokens=max_tokens,
            )
            if rate_limiter is not None:
                try:
                    rate_limiter.update_from_response(raw.headers)
                except Exception:  # noqa: BLE001
                    pass
            content = _raw_content(raw)
            return _parse_json(content)
        except Exception as e:
            err_str = str(e)
            # 限流（429）：优先按服务端 Retry-After 头等待
            ra = None
            try:
                import openai
                if isinstance(e, openai.RateLimitError) and rate_limiter is not None:
                    ra = rate_limiter.extract_retry_after(e)
            except Exception:
                pass
            if ra is not None:
                print(f"  ⚠️ 触发限流（429），按服务端要求等待 {ra:.0f} 秒后重试（{attempt+1}/{retries}）...")
                time.sleep(ra)
                continue
            # 503 繁忙 / 429 无 Retry-After / too busy → 退避重试
            if "429" in err_str or "503" in err_str or "too busy" in err_str.lower():
                wait = (attempt + 1) * 5
                print(f"  ⚠️ AI 服务繁忙/限流（{err_str[:80]}），{wait}秒后重试（{attempt+1}/{retries}）...")
                time.sleep(wait)
                continue
            raise  # 其他错误不重试
    # 重试耗尽，给出用户友好的引导
    raise RuntimeError(
        f"\n❌ AI 服务连续 {retries} 次返回限流/繁忙，放弃了。\n"
        "   原因：免费额度有每分钟请求数（RPM）限制，短时间内调用太多次。\n"
        "   解决（任选其一）：\n"
        "     ① 等 5-30 分钟再试（最稳）\n"
        "     ② 如果你其实是【付费】额度：把 config.yaml 的 ai.tier 改成 paid（工具就不会主动限流）\n"
        "     ③ 升级到付费版（更高 RPM）\n"
        "   说明：DeepSeek/OpenAI/硅基流动 的【免费档】都有 RPM 限制（一般 3-5 次/分钟）。\n"
        "   付费档 RPM 高得多（数十~数百次/分钟），单个视频的基本不会触顶。\n"
    )


def _build_mode_instructions(mode: str, query: str) -> tuple:
    """根据输出模式生成要插入到 prompt 中的指令片段。

    返回 (chunk_instruction, merge_instruction, query_instruction)。
    """

    mode = (mode or "concise").lower().strip()
    if mode not in _MODE_INSTRUCTIONS:
        mode = "concise"
    mode_inst = _MODE_INSTRUCTIONS[mode]

    # query 只在自定义模式下追加；若模式不是 query 但用户传了 query，也友好地纳入
    query_inst = ""
    if query:
        query_inst = f"- **用户关注主题**：{query}\n  请优先围绕上述主题提炼重点，无关内容可忽略或简略。"
    elif mode == "query":
        # 自定义模式但没给 query，退化为 detailed
        mode_inst = _MODE_INSTRUCTIONS["detailed"]

    return mode_inst, query_inst


def _build_full_text(segments: List[Segment], interval_sec: int = 180, max_chars: int = 30000) -> str:
    """把转录片段拼成连续文案，每隔约 interval_sec 秒插入一个 [MM:SS] 时间戳。

    用于「全文文案」模式：保留完整原文，仅用时间戳标出视频进度，方便回看定位。
    max_chars 限制总长度（默认约 3 万字），超长则截断并提示。
    """

    buf: List[str] = []
    last_ts = -1e9
    chars = 0
    for s in segments:
        text = (s.text or "").strip()
        if not text:
            continue
        if s.start - last_ts >= interval_sec:
            buf.append(f"\n\n[{format_timestamp(s.start)}] ")
            last_ts = s.start
        buf.append(text)
        chars += len(text) + 1
        if chars >= max_chars:
            buf.append("\n\n……（全文过长，已截断；完整内容见本地转录原文）")
            break
    return "".join(buf).strip()


def _generate_overview(transcript, client, ai: AIConfig, proxy: str, rate_limiter) -> str:
    """全文文案模式下，用一次轻量调用生成「内容概述」一句话（关键术语加粗）。

    失败则回退用视频开头首句，保证基本信息不为空。
    """

    head = _build_chunk_text(transcript.segments)[:1500]
    overview_lang = transcript.language or _detect_language(transcript.segments) or "en"
    sys_p = (
        "你是视频内容分析助手。"
        + _language_instruction(overview_lang)
        + "请用一句话（20-40字）概述整个视频在讲什么，"
        "关键术语用 **加粗** 包裹。只输出这句话本身，不要 JSON、不要任何解释或引号。"
    )
    user = (
        f"视频标题：{transcript.title}\n"
        f"作者：{transcript.author or '未知'}\n"
        f"总时长：{format_duration(transcript.duration)}\n\n"
        f"以下是视频开头部分的文字稿（前 1500 字）：\n{head}\n\n概述："
    )
    try:
        rate_limiter.wait_before_call()
        raw = client.chat.completions.with_raw_response.create(
            model=ai.model,
            messages=[
                {"role": "system", "content": sys_p},
                {"role": "user", "content": user},
            ],
            temperature=0.3,
            max_tokens=120,
        )
        ov = (_raw_content(raw)).strip().strip('"').strip()
        return ov
    except Exception:  # noqa: BLE001
        # 回退：用开头首句
        for s in transcript.segments:
            t = (s.text or "").strip()
            if t:
                return t[:60]
        return ""


def generate_summary(
    transcript: Transcript,
    ai: Optional[AIConfig] = None,
    proxy: str = "",
    mode: str = "concise",
    query: str = "",
) -> Summary:
    """把 Transcript 转成结构化 Summary（AI 生成，按逻辑分章 + 合并去重）。

    transcript 必须已有 segments；否则抛 ValueError 引导先提取/转录。

    mode: concise（默认，只提炼核心重点）| detailed（大重点+次重点）| query（按用户 query 过滤）
    query: mode=query 时用户输入的关键词/问题；非 query 模式下也可作为倾向性提示。
    """

    if not transcript.segments:
        raise ValueError(
            "摘要需要文字稿（transcript.segments 为空）。\n"
            "请确认已成功提取字幕，或无字幕视频已先下载音频并转录。"
        )

    ai = ai or AIConfig()
    if not ai.api_key:
        raise ValueError(
            f"未配置 AI Key（ai.provider={ai.provider!r} 的 api_key 为空）。\n"
            "请在 config/config.yaml 填入对应 Key。"
        )

    client = _client_for(ai, proxy)

    # tier 自动探测（由 product-reviewer 提供的 core.tier_probe.detect_tier 完成）；
    # 模块尚不存在时安全跳过，沿用用户自报的 ai.tier。
    try:
        from core.tier_probe import detect_tier

        detected = detect_tier(ai, proxy)
        if detected:
            ai.tier = detected
    except Exception:  # noqa: BLE001
        pass

    rate_limiter = RateLimiter(tier=ai.tier, provider=ai.provider, model=ai.model)
    if ai.tier == "free":
        rate_limiter.notify_if_free()

    # 输出语言跟随视频语种；若 transcript.language 为空/未知，按实际文本重新判定
    detected_lang = transcript.language or _detect_language(transcript.segments)
    if not detected_lang and transcript.segments:
        detected_lang = "en"  # 非空但无 CJK 时，默认英文更合理（避免模型默认中文）
    lang_inst = _language_instruction(detected_lang)

    # 全文文案模式：不调 AI 分章，直接输出带时间戳的连续转录文案（内容概述仍用一次轻量调用生成）
    if mode == "fulltext":
        full_text = _build_full_text(transcript.segments)
        # 仅当转录文本确为「真实中文」时才做轻量标点恢复；英文/未知语种不强行加中文标点
        if _detect_language(transcript.segments) == "zh":
            full_text = _restore_punctuation(full_text, client, ai, proxy)
        try:
            overview = _generate_overview(transcript, client, ai, proxy, rate_limiter)
        except Exception:  # noqa: BLE001
            overview = ""
        return Summary(
            title=transcript.title,
            source=transcript.platform.label,
            author=transcript.author,
            publish_time=transcript.publish_time,
            duration_text=format_duration(transcript.duration),
            content_overview=overview,
            detailed=[],
            golden_quotes=[],
            full_text=full_text,
            mode_label="全文文案",
        )

    mode_inst, query_inst = _build_mode_instructions(mode, query)
    chunk_system = _CHUNK_SYSTEM_PROMPT.format(
        language_instruction=lang_inst, mode_instruction=mode_inst, query_instruction=query_inst
    )
    merge_system = _MERGE_SYSTEM_PROMPT.format(
        language_instruction=lang_inst, mode_instruction=mode_inst, query_instruction=query_inst
    )

    chunks = _chunk_segments(transcript.segments, _CHUNK_MAX_CHARS)
    total = len(chunks)

    all_detailed: List[DetailedRow] = []
    all_quotes: List[GoldenQuote] = []

    for i, chunk in enumerate(chunks):
        text = _build_chunk_text(chunk)
        start_ts = format_timestamp(chunk[0].start)
        end_ts = format_timestamp(chunk[-1].end)

        user_msg = (
            f"视频标题：{transcript.title}\n"
            f"作者：{transcript.author or '未知'}\n"
            f"总时长：{format_duration(transcript.duration)}\n\n"
            f"这是视频的第 {i + 1}/{total} 段，时间范围约 {start_ts}–{end_ts}。"
            f"请按内容逻辑把本段整理成若干章节（同一话题合并、不同话题分开），并提取本段金句。\n\n"
            f"以下是该段带时间戳的文字稿：\n{text}"
        )

        rate_limiter.wait_before_call()
        data = _call_llm(client, ai, chunk_system, user_msg, max_tokens=4096, proxy=proxy, rate_limiter=rate_limiter)

        for r in (data.get("detailed") or []):
            if r.get("content") or r.get("point"):
                all_detailed.append(
                    DetailedRow(
                        timestamp=str(r.get("timestamp", "")),
                        point=str(r.get("point", "")),
                        content=str(r.get("content", "")),
                        remark="",  # 用户备注列，AI 不填，留给用户事后手写
                    )
                )

        all_quotes.extend(_parse_quotes(data.get("golden_quotes")))

    # 合并阶段：把各段「分章草稿」合并成全片稳定的逻辑章节 + 概述
    chapters_text = "\n".join(f"[{r.timestamp}] {r.point}：{r.content}" for r in all_detailed)
    quotes_text = "\n".join(f"- [{q.timestamp}] {q.text}" for q in all_quotes) or "（无）"
    merge_msg = (
        f"视频标题：{transcript.title}\n"
        f"总时长：{format_duration(transcript.duration)}\n\n"
        f"以下是各段摘要合并出的「分章草稿」（按时间顺序）：\n{chapters_text}\n\n"
        f"候选金句：\n{quotes_text}\n\n"
        f"请合并相邻/相同话题的草稿成稳定的逻辑章节，覆盖全片，并输出 content_overview / 最终 detailed / golden_quotes。"
    )
    rate_limiter.wait_before_call()
    merged = _call_llm(client, ai, merge_system, merge_msg, max_tokens=4096, proxy=proxy, rate_limiter=rate_limiter)

    detailed = [
        DetailedRow(
            timestamp=str(r.get("timestamp", "")),
            point=str(r.get("point", "")),
            content=str(r.get("content", "")),
            remark="",
        )
        for r in (merged.get("detailed") or [])
        if (r.get("content") or r.get("point"))
    ]
    # 若合并阶段没给 detailed，退化用各段草稿直接拼接（保底）
    if not detailed:
        detailed = all_detailed

    golden_quotes = _dedupe_quotes(
        _parse_quotes(merged.get("golden_quotes")) or all_quotes, cap=5
    )

    # 模式标签：渲染在标题后，如 "标题 - 精简" / "标题 - 详细" / "标题 - 自定义：聚类"
    mode_label = {
        "concise": "精简",
        "detailed": "详细",
        "query": f"自定义：{query[:30]}{'…' if len(query) > 30 else ''}",
    }.get(mode, "精简")

    return Summary(
        title=transcript.title,
        source=transcript.platform.label,
        author=transcript.author,
        publish_time=transcript.publish_time,
        duration_text=format_duration(transcript.duration),
        content_overview=str(merged.get("content_overview", "")),
        detailed=detailed,
        golden_quotes=golden_quotes,
        mode_label=mode_label,
    )
