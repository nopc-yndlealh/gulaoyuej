"""小红书爬虫：关键词搜索 + 白名单用户最新笔记。

依赖 Playwright（需 `playwright install chromium`）+ 登录 cookie。
读取搜索/用户页渲染后的 DOM（.note-item）提取笔记，比内部 __INITIAL_STATE__ 更抗页面结构变动。
cookie 缺失时优雅跳过，不影响整体流水线。
"""
from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path

from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

COOKIE_PATH = Path(__file__).resolve().parent.parent / "cookies" / "xiaohongshu.json"
SEARCH_URL = "https://www.xiaohongshu.com/search_result?keyword={kw}&source=web_search_result_note"
USER_URL = "https://www.xiaohongshu.com/user/profile/{uid}"


def _load_cookies():
    if not COOKIE_PATH.exists():
        return None
    data = json.loads(COOKIE_PATH.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = [{"name": k, "value": v} for k, v in data.items()]
    return data


def _parse_count(text: str) -> int:
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


def _extract_cards(page, limit: int) -> list[dict]:
    out: list[dict] = []
    try:
        page.wait_for_selector(".note-item", timeout=20000)
    except Exception:
        return out
    # 滚动几次触发加载更多
    for _ in range(3):
        page.mouse.wheel(0, 2000)
        page.wait_for_timeout(800)
    cards = page.query_selector_all(".note-item")
    for c in cards[:limit]:
        try:
            link = c.query_selector("a[href*='/explore/']")
            href = link.get_attribute("href") if link else None
            m = re.search(r"/explore/([0-9a-zA-Z]+)", href or "")
            if not m:
                continue
            note_id = m.group(1)
            img = c.query_selector("img")
            cover = img.get_attribute("src") if img else ""
            if cover and cover.startswith("//"):
                cover = "https:" + cover
            title_el = c.query_selector(".title")
            title = title_el.inner_text().strip() if title_el else ""
            author_el = c.query_selector(".author .name") or c.query_selector(".author")
            author = author_el.inner_text().strip() if author_el else ""
            like_el = c.query_selector(".like-wrapper .count")
            likes = _parse_count(like_el.inner_text() if like_el else "")
            out.append({
                "id": f"xhs_{note_id}",
                "platform": "xiaohongshu",
                "title": title,
                "author": author,
                "author_url": "",
                "cover": cover,
                "url": f"https://www.xiaohongshu.com/explore/{note_id}",
                "likes": likes,
                "published_at": "",
                "summary": "",
            })
        except Exception:
            continue
    return out


def crawl(keywords: list[str], whitelist_userids: list[str], search_per_keyword: int = 20) -> list[dict]:
    cookies = _load_cookies()
    if not cookies:
        print("[xiaohongshu] 未找到 cookies/xiaohongshu.json，跳过。请先用 Cookie-Editor 导出登录 cookie。")
        return []
    items: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
        try:
            ctx.add_cookies(cookies)
        except Exception as e:
            print(f"[xiaohongshu] cookie 注入失败: {e}，跳过。")
            browser.close()
            return []
        page = ctx.new_page()
        page.set_default_timeout(30000)
        for kw in keywords:
            try:
                url = SEARCH_URL.format(kw=urllib.parse.quote(kw))
                page.goto(url, wait_until="domcontentloaded")
                items.extend(_extract_cards(page, search_per_keyword))
            except Exception as e:
                print(f"[xiaohongshu] 搜索 '{kw}' 失败: {e}")
        for uid in whitelist_userids:
            try:
                page.goto(USER_URL.format(uid=uid), wait_until="domcontentloaded")
                items.extend(_extract_cards(page, search_per_keyword))
            except Exception as e:
                print(f"[xiaohongshu] 用户 {uid} 笔记失败: {e}")
        browser.close()
    return items


if __name__ == "__main__":
    sample = crawl(["月季"], [], search_per_keyword=5)
    for s in sample[:5]:
        print(s["title"], "|", s["likes"], "|", s["url"])
