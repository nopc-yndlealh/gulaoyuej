"""严格过滤：阈值、相关性、内容安全、中文判定。"""
from __future__ import annotations

import re

CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def has_cjk(text: str) -> bool:
    return bool(CJK_RE.search(text or ""))


def passes_threshold(item: dict, cfg: dict) -> bool:
    t = cfg.get("thresholds", {}).get(item.get("platform", ""), {})
    min_likes = t.get("min_likes", 0)
    return (item.get("likes", 0) or 0) >= min_likes


def is_relevant(text: str, cfg: dict) -> tuple[bool, str]:
    rel = cfg.get("relevance", {})
    required = rel.get("required_any", [])
    # 负向词（娱乐/音乐等）：标题含这些基本可判定为非园艺内容
    negative = rel.get("negative", [])
    if any(w.lower() in text.lower() for w in negative):
        return False, "entertainment_signal"
    if not any(k in text for k in required):
        return False, "no_required_keyword"
    return True, ""


def is_safe(text: str, cfg: dict) -> tuple[bool, str]:
    for w in cfg.get("safety_blocklist", []):
        if w and w in text:
            return False, f"blocklist:{w}"
    return True, ""


def is_chinese(text: str) -> bool:
    """要求标题含中文，过滤纯外语文案。"""
    return has_cjk(text)
