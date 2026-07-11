"""聚合：调爬虫 → 去重 → 严格过滤 → 排序截断。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "daily_config.json"

# 延迟导入，避免缺少 playwright 时连 B站都跑不了
from filters import passes_threshold, is_relevant, is_safe, is_chinese  # noqa: E402


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def collect_raw(cfg: dict) -> list[dict]:
    raw: list[dict] = []
    per = cfg.get("search_per_keyword", 20)

    # B站（httpx，无需浏览器）
    try:
        from crawlers.bilibili import crawl as crawl_bili
        raw += crawl_bili(cfg.get("keywords", []), cfg.get("whitelist", {}).get("bilibili", []), per)
    except ImportError as e:
        print(f"[aggregator] B站爬虫依赖缺失（{e}），跳过。")
    except Exception as e:
        print(f"[aggregator] B站抓取异常: {e}")

    # 小红书（Playwright，可能未安装/无 cookie）
    try:
        from crawlers.xiaohongshu import crawl as crawl_xhs
        raw += crawl_xhs(cfg.get("keywords", []), cfg.get("whitelist", {}).get("xiaohongshu", []), per)
    except ImportError as e:
        print(f"[aggregator] 小红书爬虫依赖缺失（{e}），跳过。")
    except Exception as e:
        print(f"[aggregator] 小红书抓取异常: {e}")

    return raw


def aggregate(cfg: dict, raw: list[dict] | None = None) -> tuple[list[dict], dict]:
    if raw is None:
        raw = collect_raw(cfg)

    seen: set[str] = set()
    items: list[dict] = []
    dropped = {"dup": 0, "threshold": 0, "irrelevant": 0, "unsafe": 0, "non_chinese": 0}

    for it in raw:
        if it.get("id") in seen:
            dropped["dup"] += 1
            continue
        seen.add(it["id"])

        text = f"{it.get('title', '')} {it.get('summary', '')}"
        if not is_chinese(it.get("title", "")):
            dropped["non_chinese"] += 1
            continue
        if not passes_threshold(it, cfg):
            dropped["threshold"] += 1
            continue
        ok, _ = is_relevant(text, cfg)
        if not ok:
            dropped["irrelevant"] += 1
            continue
        ok, _ = is_safe(text, cfg)
        if not ok:
            dropped["unsafe"] += 1
            continue
        items.append(it)

    items.sort(key=lambda x: x.get("likes", 0) or 0, reverse=True)
    items = items[: cfg.get("max_items_per_issue", 30)]

    stats = {
        "total_raw": len(raw),
        "kept": len(items),
        "dropped": dropped,
        "by_platform_raw": _count_platform(raw),
    }
    return items, stats


def _count_platform(raw: list[dict]) -> dict:
    c: dict[str, int] = {}
    for it in raw:
        c[it.get("platform", "?")] = c.get(it.get("platform", "?"), 0) + 1
    return c


if __name__ == "__main__":
    cfg = load_config()
    items, stats = aggregate(cfg)
    print("STATS:", json.dumps(stats, ensure_ascii=False, indent=2))
    for s in items[:5]:
        print(s["title"], "|", s["platform"], "|", s["likes"])
