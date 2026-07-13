"""B站爬虫：关键词搜索 + 白名单 UP主最新列表。

使用官方 web 接口 + wbi 签名（无需登录即可搜索；带 cookie 可提升限流额度）。
纯 httpx 实现，无浏览器依赖。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse
from pathlib import Path

import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "https://search.bilibili.com/",
    "Accept": "application/json, text/plain, */*",
}

# wbi 混排表（B站固定）
MIXIN_KEY_TABLE = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61,
    26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
SEARCH_URL = "https://api.bilibili.com/x/web-interface/wbi/search/type"
SPACE_URL = "https://api.bilibili.com/x/space/wbi/arc/search"

COOKIE_PATH = Path(__file__).resolve().parent.parent / "cookies" / "bilibili.json"


def _load_cookie() -> dict | None:
    if COOKIE_PATH.exists():
        try:
            return json.loads(COOKIE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def _norm_cover(pic: str) -> str:
    if not pic:
        return ""
    if pic.startswith("//"):
        return "https:" + pic
    return pic


class BilibiliCrawler:
    def __init__(self, timeout: float = 15.0):
        self._client = httpx.Client(headers=HEADERS, timeout=timeout, follow_redirects=True)
        cookie = _load_cookie()
        if cookie:
            self._client.cookies.update(cookie)
        self._mixin_key: str | None = None

    def _get_mixin_key(self) -> str:
        if self._mixin_key:
            return self._mixin_key
        resp = self._client.get(NAV_URL)
        resp.raise_for_status()
        wbi = resp.json()["data"]["wbi_img"]
        # 文件名即 wbi key，注意不能用宽松正则（会误匹配域名里的 0）
        img = re.search(r"/([0-9a-f]+)\.(?:png|jpg|webp)", wbi["img_url"]).group(1)
        sub = re.search(r"/([0-9a-f]+)\.(?:png|jpg|webp)", wbi["sub_url"]).group(1)
        orig = img + sub
        self._mixin_key = "".join(orig[i] for i in MIXIN_KEY_TABLE)[:32]
        return self._mixin_key

    def _sign(self, params: dict) -> dict:
        mix = self._get_mixin_key()
        params = dict(params)
        params["wts"] = int(time.time())
        items = sorted(params.items())
        qs = urllib.parse.urlencode(items)
        params["w_rid"] = hashlib.md5((qs + mix).encode()).hexdigest()
        return params

    def search(self, keyword: str, ps: int = 20) -> list[dict]:
        params = self._sign({
            "search_type": "video",
            "keyword": keyword,
            "page": 1,
            "page_size": ps,
            "order": "totalrank",
        })
        resp = self._client.get(SEARCH_URL, params=params)
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        results = data.get("result") or []
        out = []
        for it in results:
            bvid = it.get("bvid")
            if not bvid:
                continue
            title = _strip_tags(it.get("title", ""))
            author = it.get("author", "")
            cover = _norm_cover(it.get("pic", ""))
            # 用 BV 号构造链接（搜索结果里的 arcurl 是老式 av 巨长号，可能失效）
            url = f"https://www.bilibili.com/video/{bvid}"
            # 搜索结果未直接给 like，用播放量作热度代理
            likes = int(it.get("like") or it.get("play") or 0)
            pub = it.get("pubdate")
            published_at = time.strftime("%Y-%m-%d", time.localtime(pub)) if pub else ""
            out.append({
                "id": f"bili_{bvid}",
                "platform": "bilibili",
                "title": title,
                "author": author,
                "author_url": f"https://space.bilibili.com/{it.get('mid', '')}" if it.get("mid") else "",
                "cover": cover,
                "url": url,
                "likes": likes,
                "published_at": published_at,
                "summary": _strip_tags(it.get("description", ""))[:80],
            })
        return out

    def space_latest(self, mid: int | str, ps: int = 10) -> list[dict]:
        params = self._sign({
            "mid": int(mid),
            "ps": ps,
            "pn": 1,
            "order": "pubdate",
            "keyword": "",
        })
        resp = self._client.get(SPACE_URL, params=params)
        resp.raise_for_status()
        vlist = resp.json().get("data", {}).get("list", {}).get("vlist") or []
        out = []
        for it in vlist:
            bvid = it.get("bvid")
            if not bvid:
                continue
            out.append({
                "id": f"bili_{bvid}",
                "platform": "bilibili",
                "title": _strip_tags(it.get("title", "")),
                "author": it.get("author", ""),
                "author_url": f"https://space.bilibili.com/{mid}",
                "cover": _norm_cover(it.get("pic", "")),
                "url": f"https://www.bilibili.com/video/{bvid}",
                "likes": int(it.get("play") or 0),
                "published_at": time.strftime("%Y-%m-%d", time.localtime(it["created"])) if it.get("created") else "",
                "summary": _strip_tags(it.get("description", ""))[:100],
            })
        return out

    def close(self):
        self._client.close()


def crawl(keywords: list[str], whitelist_mids: list[int], search_per_keyword: int = 20, space_ps: int = 10) -> list[dict]:
    """返回标准化条目列表。"""
    crawler = BilibiliCrawler()
    items: list[dict] = []
    try:
        for kw in keywords:
            try:
                items.extend(crawler.search(kw, ps=search_per_keyword))
            except Exception as e:
                print(f"[bilibili] 搜索 '{kw}' 失败: {e}")
        for mid in whitelist_mids:
            try:
                items.extend(crawler.space_latest(mid, ps=space_ps))
            except Exception as e:
                print(f"[bilibili] UP主 {mid} 最新列表失败: {e}")
    finally:
        crawler.close()
    return items


def crawl_authors(ids: list[int | str], ps: int = 10) -> list[dict]:
    """按白名单 UP主(mid) 拉各自最新视频，标记 source=whitelist。"""
    crawler = BilibiliCrawler()
    items: list[dict] = []
    try:
        for mid in ids:
            try:
                its = crawler.space_latest(mid, ps=ps)
                for it in its:
                    it["source"] = "whitelist"
                items.extend(its)
                print(f"[bilibili] UP主 {mid} -> {len(its)} 条")
            except Exception as e:
                print(f"[bilibili] UP主 {mid} 最新列表失败: {e}")
    finally:
        crawler.close()
    return items


if __name__ == "__main__":
    sample = crawl(["月季", "多肉"], [], search_per_keyword=5)
    for s in sample[:5]:
        print(s["title"], "|", s["likes"], "|", s["url"])
