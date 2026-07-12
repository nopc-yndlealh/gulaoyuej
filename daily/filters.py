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


def is_relevant(text: str, cfg: dict, mode: str = "full") -> tuple[bool, str]:
    rel = cfg.get("relevance", {})
    required = rel.get("required_any", [])
    # 负向词（娱乐/音乐/粉丝等）：标题含这些基本可判定为非园艺内容
    negative = rel.get("negative", [])
    if any(w.lower() in text.lower() for w in negative):
        return False, "entertainment_signal"
    # 离题主导词（美妆/穿搭等）：家庭园艺站不应出现
    offtopic = rel.get("offtopic_dominant", [])
    if any(w in text for w in offtopic):
        return False, "offtopic_dominant"
    # 白名单(已审核)博主走 light 模式：只拦明显离题，不强制 required_any 硬匹配
    if mode == "light":
        return True, ""
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


def is_blocked_author(author: str, cfg: dict) -> bool:
    """作者黑名单：命中即丢弃（用于拉黑无关/低质博主）。"""
    black = cfg.get("author_blacklist", [])
    if not black or not author:
        return False
    return author in black
