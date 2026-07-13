"""聚合：调爬虫 → 去重 → 过滤 → 排序截断。

模式（daily_config.json 的 mode 字段）：
- "whitelist_primary"：白名单作者模式为主，关键词搜索兜底（默认，最精准）
- "pure"            ：只跑白名单作者，完全不碰关键词
- "keyword"         ：只跑关键词（旧行为）
"""
from __future__ import annotations

import json
import random
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "daily_config.json"
AUTHORS_PATH = BASE / "authors.json"

# 延迟导入，避免缺少 playwright 时连 B站都跑不了
from filters import passes_threshold, is_relevant, is_safe, is_chinese, is_blocked_author  # noqa: E402


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _load_authors() -> list[dict]:
    if not AUTHORS_PATH.exists():
        return []
    try:
        data = json.loads(AUTHORS_PATH.read_text(encoding="utf-8"))
        return data.get("authors", []) or []
    except Exception:
        return []


def _priority_set() -> set:
    """返回 authors.json 中 priority=true 的作者集合，元素为 (platform, name)。"""
    out: set = set()
    try:
        for a in _load_authors():
            if a.get("priority"):
                out.add((a.get("platform"), a.get("name")))
    except Exception:
        pass
    return out


def collect_raw(cfg: dict) -> list[dict]:
    raw: list[dict] = []
    per = cfg.get("search_per_keyword", 20)
    per_author = cfg.get("per_author", 10)
    mode = cfg.get("mode", "whitelist_primary")
    authors = _load_authors()

    # ---- 白名单作者模式（whitelist_primary / pure）----
    if mode in ("whitelist_primary", "pure"):
        bili_ids = [int(a["id"]) for a in authors
                    if a.get("platform") == "bilibili" and str(a.get("id", "")).isdigit()]
        xhs_authors = [a for a in authors if a.get("platform") == "xiaohongshu"]
        if bili_ids:
            try:
                from crawlers.bilibili import crawl_authors as crawl_bili_authors
                raw += crawl_bili_authors(bili_ids, ps=per_author)
            except Exception as e:
                print(f"[aggregator] B站作者抓取异常: {e}")
        if xhs_authors:
            try:
                from crawlers.xiaohongshu_mcp import crawl_authors as crawl_xhs_authors
                raw += crawl_xhs_authors(xhs_authors, per=per_author)
            except ImportError:
                print("[aggregator] 小红书 MCP 适配器缺失，跳过小红书作者模式。")
            except Exception as e:
                print(f"[aggregator] 小红书作者抓取异常: {e}")
        gnb_authors = [a for a in authors if a.get("platform") == "guonongbang"]
        if gnb_authors:
            try:
                from crawlers.guonongbang import crawl_authors as crawl_gnb_authors
                raw += crawl_gnb_authors(gnb_authors, per=per_author)
            except Exception as e:
                print(f"[aggregator] 果农邦抓取异常: {e}")
        thx_authors = [a for a in authors if a.get("platform") == "tahuaxing"]
        if thx_authors:
            try:
                from crawlers.tahuaxing import crawl_authors as crawl_thx_authors
                raw += crawl_thx_authors(thx_authors, per=per_author)
            except Exception as e:
                print(f"[aggregator] 踏花行抓取异常: {e}")

    # ---- 关键词兜底（whitelist_primary / keyword）----
    if mode in ("whitelist_primary", "keyword"):
        kws = cfg.get("keywords", [])
        # B站（httpx，无需浏览器）
        try:
            from crawlers.bilibili import crawl as crawl_bili
            kw = crawl_bili(kws, [], per)
            for it in kw:
                it.setdefault("source", "keyword")
            raw += kw
        except ImportError as e:
            print(f"[aggregator] B站爬虫依赖缺失（{e}），跳过。")
        except Exception as e:
            print(f"[aggregator] B站抓取异常: {e}")

        # 小红书：优先 MCP 适配器，回退 Playwright
        try:
            from crawlers.xiaohongshu_mcp import crawl as crawl_xhs
            kw = crawl_xhs(kws, [], per)
            for it in kw:
                it.setdefault("source", "keyword")
            raw += kw
        except ImportError:
            try:
                from crawlers.xiaohongshu import crawl as crawl_xhs_pw
                kw = crawl_xhs_pw(kws, [], per)
                for it in kw:
                    it.setdefault("source", "keyword")
                raw += kw
            except ImportError as e:
                print(f"[aggregator] 小红书爬虫依赖缺失（{e}），跳过。")
            except Exception as e:
                print(f"[aggregator] 小红书抓取异常: {e}")
        except Exception as e:
            print(f"[aggregator] 小红书抓取异常: {e}")

    return raw


def aggregate(cfg: dict, raw: list[dict] | None = None) -> tuple[list[dict], dict]:
    if raw is None:
        raw = collect_raw(cfg)

    seen: set[str] = set()
    items: list[dict] = []
    dropped = {"dup": 0, "threshold": 0, "irrelevant": 0, "unsafe": 0,
               "non_chinese": 0, "blocked_author": 0, "source": {}}

    for it in raw:
        src = it.get("source", "keyword")
        dropped["source"][src] = dropped["source"].get(src, 0) + 1
        if it.get("id") in seen:
            dropped["dup"] += 1
            continue
        seen.add(it["id"])

        text = f"{it.get('title', '')} {it.get('summary', '')}"
        if not is_chinese(it.get("title", "")):
            dropped["non_chinese"] += 1
            continue
        if is_blocked_author(it.get("author", ""), cfg):
            dropped["blocked_author"] += 1
            continue
        # 白名单(已审核)作者全信任：跳过点赞阈值，低赞高质量内容也保留
        if src != "whitelist" and not passes_threshold(it, cfg):
            dropped["threshold"] += 1
            continue
        # 白名单(已审核)博主走 light：只拦明显离题；关键词来源走 full 硬匹配
        ok, _ = is_relevant(text, cfg, mode="light" if src == "whitelist" else "full")
        if not ok:
            dropped["irrelevant"] += 1
            continue
        ok, _ = is_safe(text, cfg)
        if not ok:
            dropped["unsafe"] += 1
            continue
        items.append(it)

    # 混合排序：5 条最新（有发布时间的优先）+ 余下随机（让老内容/小红书也有露出）
    # 小红书不返回时间，全部进入随机池；随机池按作者去重（每作者至多 1 条）提升多样性
    dated = [i for i in items if i.get("published_at")]
    undated = [i for i in items if not i.get("published_at")]
    dated.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    top = dated[:5]
    # 随机池按作者去重，保留该作者点赞最高的一条
    _best: dict = {}
    for it in undated:
        k = (it.get("platform"), it.get("author"))
        if k not in _best or it.get("likes", 0) > _best[k].get("likes", 0):
            _best[k] = it
    # 优先级作者（priority:true）强制占一个槽位，保证每期稳定露出
    prio_keys = {(a.get("platform"), a.get("name"))
                 for a in _load_authors() if a.get("priority")}
    forced = [v for k, v in _best.items() if k in prio_keys]
    rest = [v for k, v in _best.items() if k not in prio_keys]
    pool = dated[5:] + rest
    random.shuffle(pool)
    n_random = max(0, cfg.get("max_items_per_issue", 11) - len(top) - len(forced))
    chosen = top + forced + pool[:n_random]

    # 保证 priority 白名单作者（authors.json 中 priority=true）一定出现在本期周报
    prio = _priority_set()
    if prio:
        chosen_ids = {c.get("id") for c in chosen}
        for (pplat, pname) in prio:
            if any(c.get("platform") == pplat and c.get("author") == pname for c in chosen):
                continue
            cand = next((i for i in items
                        if i.get("platform") == pplat and i.get("author") == pname
                        and i.get("id") not in chosen_ids), None)
            if cand:
                if len(chosen) >= cfg.get("max_items_per_issue", 11):
                    chosen[-1] = cand
                else:
                    chosen.append(cand)
                chosen_ids.add(cand.get("id"))

    items = chosen

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
        print(s["title"], "|", s["platform"], "|", s.get("source"), "|", s["likes"])
