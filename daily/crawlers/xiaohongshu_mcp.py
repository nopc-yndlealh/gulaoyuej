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


class _McpClient:
    def __init__(self):
        self._c = httpx.Client(timeout=30)
        self._init()

    def _init(self):
        r = self._c.post(
            MCP_URL,
            headers=HEADERS,
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
            HEADERS["mcp-session-id"] = sid
        self._c.post(
            MCP_URL,
            headers=HEADERS,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

    def call(self, name: str, arguments: dict) -> dict:
        r = self._c.post(
            MCP_URL,
            headers=HEADERS,
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
                "summary": "",
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


def crawl_authors(authors: list[dict], per: int = 10) -> list[dict]:
    """按白名单博主(user_id + 昵称)拉笔记：搜索昵称 -> 过滤出该博主 -> 标 source=whitelist。
    走 search_feeds 过滤，不需要 xsec_token；要求 authors 里带 name(昵称)。
    小红书 MCP 偶发超时，对单个作者 search 失败自动重试（指数退避），无匹配则跳过重试。"""
    if not _mcp_available():
        print("[xiaohongshu_mcp] MCP 服务未启动（localhost:18060），跳过小红书作者模式。")
        return []
    try:
        client = _McpClient()
    except Exception as e:
        print(f"[xiaohongshu_mcp] 连接 MCP 失败: {e}，跳过。")
        return []
    import time
    items: list[dict] = []
    try:
        for a in authors:
            uid = a.get("id") or ""
            name = (a.get("name") or "").strip()
            if not name:
                print(f"[xiaohongshu_mcp] 作者 {uid} 缺昵称(name)，跳过（请提供昵称）。")
                continue
            matched: list[dict] = []
            for attempt in range(3):
                try:
                    resp = client.call("search_feeds", {"keyword": name})
                    feeds = _extract_feeds(resp, per * 3)
                    # 优先按 user_id 精确匹配；没有 id 时退化为昵称匹配
                    if uid:
                        matched = [f for f in feeds if f.get("author_id") == uid]
                    else:
                        matched = [f for f in feeds if f.get("author") == name]
                    break  # 能正常返回即视为成功（无论是否匹配到），跳出重试
                except Exception as e:
                    if attempt < 2:
                        time.sleep(2 ** attempt * 2)  # 2s, 4s 退避后重试
                        continue
                    print(f"[xiaohongshu_mcp] 作者 {name} 失败(重试耗尽): {e}")
            for f in matched[:per]:
                f["source"] = "whitelist"
                if uid and not f.get("author_url"):
                    f["author_url"] = f"https://www.xiaohongshu.com/user/profile/{uid}"
                items.append(f)
            print(f"[xiaohongshu_mcp] 作者 {name}({uid}) -> {len(matched[:per])} 条")
    finally:
        client.close()
    return items


if __name__ == "__main__":
    sample = crawl(["月季", "多肉"], [], 10)
    for s in sample[:5]:
        print(s["title"], "|", s["likes"], "|", s["url"])
