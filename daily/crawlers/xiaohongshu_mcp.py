"""小红书 MCP 适配器：通过本地 xiaohongshu-mcp HTTP 服务（xpzouying/xiaohongshu-mcp）抓取。

优先于 Playwright 版 crawlers.xiaohongshu：无需浏览器/cookie 文件，直接复用已在桌面端
登录的 MCP 服务（扫码登录一次即可）。MCP 服务未启动、登录失效或抓取异常时返回空列表，
不影响整体流水线（B站照常出刊）。

输出与 crawlers.xiaohongshu 保持一致的标准字典，便于聚合器统一过滤。
"""
from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path

import httpx

MCP_URL = "http://localhost:18060/mcp"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
PROTOCOL = "2024-11-05"


def _mcp_available() -> bool:
    try:
        with httpx.Client(timeout=5) as c:
            r = c.get(MCP_URL, headers=HEADERS)
            return r.status_code in (200, 405)
    except Exception:
        return False


def _parse_count(text: str) -> int:
    """小红书点赞数形如 '19945' / '1' / '1.2万' / '1234'。"""
    text = (text or "").strip().replace(" ", "")
    if not text:
        return 0
    m = re.match(r"([\d.]+)\s*万", text)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.match(r"([\d.]+)\s*亿", text)
    if m:
        return int(float(m.group(1)) * 1e8)
    return int(re.sub(r"\D", "", text) or 0)


def _clean_summary(text: str, limit: int = 100) -> str:
    """清洗笔记/视频简介：去 HTML 标签、压缩空白、截断到 limit 字（卡片预览用）。"""
    s = re.sub(r"<[^>]+>", " ", text or "")
    s = re.sub(r"[\u200b\u3000]+", " ", s)        # 去零宽空格 / 全角空格
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


class _McpClient:
    def __init__(self):
        # 每个实例持有独立 headers 副本，避免多线程共享模块级 HEADERS 造成 session-id 竞态
        self._headers = dict(HEADERS)
        self._c = httpx.Client(timeout=30)
        self._init()

    def _init(self):
        r = self._c.post(
            MCP_URL,
            headers=self._headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL,
                    "capabilities": {},
                    "clientInfo": {"name": "daily", "version": "1.0"},
                },
            },
        )
        sid = r.headers.get("mcp-session-id") or r.headers.get("Mcp-Session-Id")
        if sid:
            self._headers["mcp-session-id"] = sid
        self._c.post(
            MCP_URL,
            headers=self._headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

    def call(self, name: str, arguments: dict) -> dict:
        r = self._c.post(
            MCP_URL,
            headers=self._headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
        ct = r.headers.get("content-type", "")
        text = r.text
        if "text/event-stream" in ct:
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    try:
                        return json.loads(line[5:].strip())
                    except Exception:
                        continue
            return {}
        try:
            return json.loads(text)
        except Exception:
            return {}

    def close(self):
        try:
            self._c.close()
        except Exception:
            pass


def _extract_feeds(resp: dict, limit: int) -> list[dict]:
    out: list[dict] = []
    content = resp.get("result", {}).get("content", [])
    if not content:
        return out
    try:
        data = json.loads(content[0]["text"])
    except Exception:
        return out
    for f in data.get("feeds", [])[:limit]:
        card = f.get("noteCard", {})
        title = card.get("displayTitle", "")
        if not title:
            continue
        user = card.get("user", {})
        author = user.get("nickname") or user.get("nickName") or ""
        author_id = user.get("id") or user.get("userId") or ""
        interact = card.get("interactInfo", {})
        likes = _parse_count(interact.get("likedCount", "0"))
        cover = card.get("cover", {})
        cover_url = (
            cover.get("urlDefault") or cover.get("urlPre") or cover.get("url") or ""
        )
        if cover_url.startswith("//"):
            cover_url = "https:" + cover_url
        note_id = f.get("id", "")
        xsec = f.get("xsecToken", "")
        if not note_id:
            continue
        desc = _clean_summary(card.get("desc", ""))
        url = f"https://www.xiaohongshu.com/explore/{note_id}"
        if xsec:
            url += f"?xsec_token={urllib.parse.quote(xsec)}&xsec_source=pc_search"
        author_url = f"https://www.xiaohongshu.com/user/profile/{author_id}" if author_id else ""
        out.append(
            {
                "id": f"xhs_{note_id}",
                "platform": "xiaohongshu",
                "title": title,
                "author": author,
                "author_id": author_id,
                "author_url": author_url,
                "cover": cover_url,
                "url": url,
                "likes": likes,
                "published_at": "",
                "summary": desc,
            }
        )
    return out


def crawl(
    keywords: list[str],
    whitelist_userids: list[str] | None = None,
    search_per_keyword: int = 20,
) -> list[dict]:
    if not _mcp_available():
        print("[xiaohongshu_mcp] MCP 服务未启动（localhost:18060），跳过小红书。")
        return []
    try:
        client = _McpClient()
    except Exception as e:
        print(f"[xiaohongshu_mcp] 连接 MCP 失败: {e}，跳过。")
        return []
    items: list[dict] = []
    try:
        for kw in keywords:
            try:
                resp = client.call("search_feeds", {"keyword": kw})
                feeds = _extract_feeds(resp, search_per_keyword)
                items.extend(feeds)
                print(f"[xiaohongshu_mcp] 关键词 '{kw}' -> {len(feeds)} 条")
            except Exception as e:
                print(f"[xiaohongshu_mcp] 搜索 '{kw}' 失败: {e}")
    finally:
        client.close()
    return items


def _crawl_one_author(a: dict, per: int) -> list[dict]:
    """单作者抓取（在独立线程中执行，使用独立 MCP 客户端）。返回该作者的笔记列表。"""
    uid = a.get("id") or ""
    name = (a.get("name") or "").strip()
    token = a.get("xsec_token") or ""
    if not uid:
        print(f"[xiaohongshu_mcp] 作者 {name} 缺 user_id(id)，跳过。")
        return []
    matched: list[dict] = []
    mode_used = ""
    try:
        client = _McpClient()
    except Exception as e:
        print(f"[xiaohongshu_mcp] 作者 {name} 连接 MCP 失败: {e}")
        return []
    try:
        # 优先 user_profile：按 id 直接拉该博主笔记（需 xsec_token）
        if token:
            try:
                resp = client.call("user_profile", {"user_id": uid, "xsec_token": token})
                matched = _extract_feeds(resp, per)
                mode_used = "user_profile"
            except Exception as e:
                print(f"[xiaohongshu_mcp] user_profile {name} 失败: {e}")
        # 退化：昵称搜索 + id 精确过滤（无 token 或 user_profile 失败时使用）
        if not matched:
            try:
                kw = name or uid
                resp = client.call("search_feeds", {"keyword": kw})
                feeds = _extract_feeds(resp, per * 3)
                if uid:
                    matched = [f for f in feeds if f.get("author_id") == uid]
                else:
                    matched = [f for f in feeds if f.get("author") == name]
                mode_used = "search"
            except Exception as e:
                print(f"[xiaohongshu_mcp] 作者 {name} 搜索失败: {e}")
    finally:
        client.close()
    out = []
    for f in matched[:per]:
        f["source"] = "whitelist"
        f["author_id"] = uid
        f["author"] = name
        if not f.get("author_url"):
            f["author_url"] = f"https://www.xiaohongshu.com/user/profile/{uid}"
        out.append(f)
    print(f"[xiaohongshu_mcp] 作者 {name}({uid}) -> {len(out)} 条 (mode={mode_used})")
    return out


def crawl_authors(authors: list[dict], per: int = 10) -> list[dict]:
    """按白名单博主直拉笔记，稳定收录指定博主。

    优先用 MCP 的 user_profile（按 user_id + xsec_token 直接拉该博主主页笔记），
    不依赖昵称搜索——因为很多博主自己的笔记不含其昵称字面词，昵称搜索匹配不到本人
    （例如「小耗子」的笔记是关于月季的，搜「小耗子」只会返回其他提到鼠类的笔记）。
    authors 需带 id(user_id) 与 name(昵称)；有 xsec_token 走 user_profile，
    无 token（或 user_profile 失败）退化为昵称 search_feeds + id 精确过滤兜底。

    每个作者在独立线程中使用独立 MCP 客户端抓取，单作者硬超时 PER_AUTHOR_TIMEOUT 秒，
    超时即跳过该作者——避免个别作者 user_profile 调用挂起时拖垮整轮抓取（MCP 偶发变慢）。
    """
    if not _mcp_available():
        print("[xiaohongshu_mcp] MCP 服务未启动（localhost:18060），跳过小红书作者模式。")
        return []
    import concurrent.futures as _cf
    PER_AUTHOR_TIMEOUT = 50
    items: list[dict] = []
    for a in authors:
        if not a.get("id"):
            continue
        with _cf.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_crawl_one_author, a, per)
            try:
                items.extend(fut.result(timeout=PER_AUTHOR_TIMEOUT))
            except _cf.TimeoutError:
                print(f"[xiaohongshu_mcp] 作者 {a.get('name')} 抓取超时(>{PER_AUTHOR_TIMEOUT}s)，跳过本轮。")
            except Exception as e:
                print(f"[xiaohongshu_mcp] 作者 {a.get('name')} 异常: {e}")
    return items


if __name__ == "__main__":
    sample = crawl(["月季", "多肉"], [], 10)
    for s in sample[:5]:
        print(s["title"], "|", s["likes"], "|", s["url"])
