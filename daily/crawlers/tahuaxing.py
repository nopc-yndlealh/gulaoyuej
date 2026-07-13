"""踏花行（bbs.tahua.net，Discuz!）爬虫：按版块 fid 抓最新帖。

图片机制（已实测确认）：
  - 游客页正文图是「登录后才可见」的附件，游客 HTML 里连图片地址都没有。
  - 带上登录 cookie（cookies/tahua.json，与 B站同格式：{name: value} 字典）
    后，详情页正文图以标准 Discuz! 附件形式出现：<img class="zoom" file="...">。
  - 因此：cookie 缺失时仍可按游客抓到标题/作者/简介（纯文字卡片，cover 留空）；
    cookie 存在时额外抓到真实封面图。

列表页（标题/作者/时间）游客即可读，无需登录。
"""
from __future__ import annotations

import json
import re
import time
import random
from pathlib import Path

from bs4 import BeautifulSoup
import httpx

BASE = "https://bbs.tahua.net"
BASE_DIR = Path(__file__).resolve().parent.parent
COOKIE_FILE = BASE_DIR / "cookies" / "tahua.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
              "image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": BASE + "/",
}


def _load_cookies() -> dict:
    """读取 cookies/tahua.json（{name: value} 字典）。缺失返回空 dict（游客模式）。"""
    if COOKIE_FILE.exists():
        try:
            data = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception as e:
            print(f"[tahuaxing] cookie 解析失败，按游客模式运行: {e}")
    return {}


def _norm_cover(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return BASE + url
    return url  # 前端 proxied() 会升级 http→https


def _clean(text: str) -> str:
    text = re.sub(r"<br\s*/?>", " ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"[\s]+", " ", text).strip()
    return text


def _norm_date(s: str) -> str:
    """Discuz 列表时间形如 '2026-7-13 17:29' 或相对时间(今天/昨天)。归一为 ISO。"""
    s = (s or "").strip()
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{1,2}))?", s)
    if m:
        y, mo, d, hh, mm = m.groups()
        out = f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
        if hh:
            out += f" {int(hh):02d}:{int(mm):02d}"
        return out
    return ""


class TahuaXingCrawler:
    def __init__(self):
        cookies = _load_cookies()
        self._has_cookie = bool(cookies)
        self._client = httpx.Client(
            headers=HEADERS,
            cookies=cookies,           # 有则登录态，无则游客
            follow_redirects=True,
            timeout=30.0,
            verify=False,
        )

    def _get(self, url: str) -> str:
        r = self._client.get(url)
        return r.text

    def _parse_list(self, fid: int, per: int) -> list[dict]:
        url = f"{BASE}/forum-{fid}-1.html"
        html = self._get(url)
        soup = BeautifulSoup(html, "lxml")
        out = []
        for tb in soup.select("tbody[id^='normalthread_']"):
            a = tb.select_one("a.s.xst")
            if not a:
                continue
            href = a.get("href", "")
            m = re.search(r"thread-(\d+)-", href or "")
            if not m:
                continue
            tid = m.group(1)
            title = a.get_text(strip=True)
            by = tb.select("td.by")
            author = ""
            date = ""
            if by:
                cite = by[0].select_one("cite a")
                if cite:
                    author = cite.get_text(strip=True)
                em = by[0].select_one("em")
                if em:
                    date = _norm_date(em.get_text(strip=True))
            out.append({"tid": tid, "title": title, "author": author, "date": date})
            if len(out) >= per:
                break
        return out

    def _parse_detail(self, tid: str) -> tuple[str, str]:
        url = f"{BASE}/thread-{tid}-1-1.html"
        html = self._get(url)
        soup = BeautifulSoup(html, "lxml")
        cover = ""
        body = ""
        tf = soup.select_one("td.t_f, div.t_f")
        if tf:
            # 剥掉非正文浮层：登录提示、附件权限占位、附件图说明、编辑标记、隐藏内容、评论区、脚本样式
            for sel in (".attach_nopermission", ".mag_viewthread", "ignore",
                        ".cm", ".o", ".aimg_tip", ".pstatus", ".attnm", ".tattl",
                        "script", "style"):
                for bad in tf.select(sel):
                    bad.decompose()
            # 封面：优先 Discuz! 附件图（登录后才有），依次尝试 file / zoomfile / src
            img = tf.select_one("img.zoom[file]") or tf.select_one("img[zoomfile]")
            if img:
                cover = _norm_cover(img.get("file") or img.get("zoomfile") or "")
            elif img := tf.select_one("img"):
                src = img.get("src", "")
                if src and not src.startswith("static/") and "common/" not in src:
                    cover = _norm_cover(src)
            body = _clean(tf.get_text(" ", strip=True))
            # 清理可能的登录提示残留
            body = re.sub(r"您需要\s*登录\s*才可以下载或查看.*?$", "", body).strip()
        return cover, body

    def crawl_authors(self, authors: list[dict], per: int = 10) -> list[dict]:
        out = []
        if not self._has_cookie:
            print("[tahuaxing] 未检测到 cookies/tahua.json，将以游客模式抓取"
                  "（无封面图，仅文字卡片）。")
        for a in authors:
            if a.get("platform") != "tahuaxing":
                continue
            fid = a.get("fid") or a.get("id")
            if not fid:
                continue
            try:
                fid_i = int(str(fid).lstrip("0") or "0")
            except ValueError:
                print(f"[tahuaxing] 非法 fid: {fid}")
                continue
            board_name = a.get("name", "踏花行")
            try:
                threads = self._parse_list(fid_i, per)
            except Exception as e:
                print(f"[tahuaxing] 版块 {fid_i} 列表抓取失败: {e}")
                continue
            for t in threads:
                try:
                    time.sleep(random.uniform(0.8, 1.8))  # 控频防封
                    cover, body = self._parse_detail(t["tid"])
                except Exception as e:
                    cover, body = "", ""
                    print(f"[tahuaxing] 帖子 {t['tid']} 详情失败: {e}")
                summary = body[:90] if body else ""
                out.append({
                    "id": f"thx_{t['tid']}",
                    "platform": "tahuaxing",
                    "title": t["title"],
                    "author": t["author"] or board_name,
                    "author_url": f"{BASE}/forum-{fid_i}-1.html",
                    "cover": cover,
                    "url": f"{BASE}/thread-{t['tid']}-1-1.html",
                    "likes": 0,
                    "published_at": t["date"],
                    "summary": summary,
                    "source": "whitelist",
                })
        return out

    def close(self):
        self._client.close()


def crawl_authors(authors: list[dict], per: int = 10) -> list[dict]:
    c = TahuaXingCrawler()
    try:
        return c.crawl_authors(authors, per)
    finally:
        c.close()
