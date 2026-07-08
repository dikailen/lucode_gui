from __future__ import annotations

import re
from typing import Any

from runtime.common.text_utils import sanitize_text


MAX_SESSION_TITLE_CHARS = 24

POLITE_PREFIX_RE = re.compile(
    r"^(?:请你|请|麻烦你|麻烦|能不能|能否|可以|帮我|请帮我|请你帮我|我想|我希望|现在|继续|好的|ok|OK)[，,。\s]*",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
PHASE_RE = re.compile(r"(?:进入|开始|补|做)?\s*(p\d+|phase\s*\d+|第\s*\d+\s*批)", re.IGNORECASE)


def smart_session_title(value: Any, *, max_chars: int = MAX_SESSION_TITLE_CHARS) -> str:
    text = _normalize(value)
    if not text:
        return "New chat"
    phase_title = _phase_title(text)
    if phase_title:
        return _clip_title(phase_title, max_chars=max_chars)
    cleaned = _clean_candidate(text)
    candidates = [_clean_candidate(part) for part in re.split(r"[。！？!?；;\n，,]", cleaned)]
    candidates = [item for item in candidates if item]
    if not candidates:
        candidates = [cleaned]
    chosen = max(candidates, key=_candidate_score).strip()
    chosen = _post_process(chosen)
    return _clip_title(chosen or cleaned or text, max_chars=max_chars)


def _normalize(value: Any) -> str:
    text = sanitize_text(str(value or ""))
    text = URL_RE.sub(" ", text)
    text = re.sub(r"`{1,3}[^`]*`{1,3}", " ", text)
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _phase_title(text: str) -> str:
    match = PHASE_RE.search(text)
    if not match:
        return ""
    phase = re.sub(r"\s+", "", match.group(1)).upper()
    lowered = text.casefold()
    if "风险" in text:
        return f"{phase} 风险处理"
    if "测试" in text or "体检" in text:
        return f"{phase} 验证体检"
    if "sqlite" in lowered:
        return f"{phase} SQLite 存储"
    return f"{phase} 推进"


def _clean_candidate(text: str) -> str:
    candidate = text.strip()
    previous = ""
    while candidate and previous != candidate:
        previous = candidate
        candidate = POLITE_PREFIX_RE.sub("", candidate).strip()
    replacements = {
        "当前项目的": "",
        "这个项目的": "",
        "这个项目": "",
        "当前": "",
        "真实": "",
        "是否已经能够": "",
        "是否已经": "",
        "是否能够": "",
        "是否可以": "",
        "是否": "",
        "已经": "",
        "能够": "",
        "可以": "",
        "一下": "",
        "这方面": "",
        "这块": "",
        "吧": "",
        "然后": " ",
        "并且": " ",
        "以及": " ",
        "和": " ",
        "保存历史并恢复": "历史保存恢复",
        "保存历史恢复": "历史保存恢复",
    }
    for old, new in replacements.items():
        candidate = candidate.replace(old, new)
    candidate = re.sub(r"能(?=历史|保存)", "", candidate)
    candidate = re.sub(r"保存\s*历史\s*(?:并)?\s*恢复", "历史保存恢复", candidate)
    candidate = re.sub(r"\s+", " ", candidate)
    candidate = re.sub(r"^[：:，,。\s]+|[：:，,。\s]+$", "", candidate)
    return candidate.strip()


def _post_process(text: str) -> str:
    candidate = _clean_candidate(text)
    candidate = re.sub(r"^(?:打开|进入|开始|做|处理)\s*$", "", candidate)
    candidate = re.sub(r"^打开\s+(?=总结|查看|检查|分析)", "", candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip()
    return candidate


def _candidate_score(text: str) -> tuple[int, int]:
    score = 0
    if re.search(r"[A-Z]{2,}|[A-Za-z]+-[A-Za-z]+|\d+", text):
        score += 4
    if any(keyword in text for keyword in ("检查", "修复", "总结", "设计", "验证", "风险", "历史", "SQLite", "MVP")):
        score += 3
    if len(text) <= MAX_SESSION_TITLE_CHARS:
        score += 2
    if len(text) < 4:
        score -= 3
    return score, min(len(text), MAX_SESSION_TITLE_CHARS)


def _clip_title(text: str, *, max_chars: int) -> str:
    title = re.sub(r"\s+", " ", sanitize_text(str(text or "")).strip())
    limit = max(8, int(max_chars or MAX_SESSION_TITLE_CHARS))
    if len(title) <= limit:
        return title
    clipped = title[:limit].rstrip(" ，,。:：")
    return clipped or title[:limit]
