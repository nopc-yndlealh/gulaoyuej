"""果农邦（Discuz!）爬虫：按版块 fid 抓最新帖，游客可见内容即可。

- 无需登录：图片 URL(file 属性)与正文已在 HTML 中（页面虽有"需登录下载"提示，但游客 HTML 已含内容）。
- 防采集闸门：站点对裸请求返回"页面重新载入"JS reload 页；带完整浏览器头 + cookie 会话二次请求即可过闸。
- 图片 host 为 pic.nongrenzhijia.com(http)，前端 proxied() 会升级为 https。
"""
from __future__ import annotations

import re
import time
import random
from bs4 import BeautifulSoup
import httpx

BASE = "https://www.shuiguobang.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
              "image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": BASE + "/",
}
RELOAD_MARKS = ("页面重载开启", "页面正在重新载入", "document.location.reload()")


def _norm_cover(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
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
    return ""  # 相对时间留空，进入随机池


class GuoNongBangCrawler:
    def __init__(self):
        self._client = httpx.Client(
            headers=HEADERS,
            follow_redirects=True,
            timeout=30.0,
            verify=False,  # 站点证书链在部分环境不被信任
        )

    def _get(self, url: str) -> str:
        r = self._client.get(url)
        txt = r.text
        # 站点防采集：首次请求必返回"页面重新载入"挑战页并下 Set-Cookie，
        # 第二次带 cookie 才返回真内容。检测到挑战页则重试一次。
        if any(m in txt for m in RELOAD_MARKS):
            time.sleep(random.uniform(0.5, 1.5))
            r = self._client.get(url)
            txt = r.text
        return txt

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
            # 剥掉非正文浮层：登录提示、APP 推广、隐藏附件(<ignore>)、评论区
            for sel in (".attach_nopermission", ".mag_viewthread", "ignore",
                        ".cm", ".o", "script", "style"):
                for bad in tf.select(sel):
                    bad.decompose()
            img = tf.select_one("img.zoom[file]")
            if img and img.get("file"):
                cover = _norm_cover(img["file"])
            body = _clean(tf.get_text(" ", strip=True))
            # 再清理一次登录提示残留文本（剔除常见前缀噪音）
            body = body.replace(
                "马上加入农人之家，结交更多农友，享用更多功能，让你轻松玩转。", ""
            ).replace("您需要 登录 才可以下载或查看，没有帐号？", "").strip()
        return cover, body

    def crawl_authors(self, authors: list[dict], per: int = 10) -> list[dict]:
        out = []
        for a in authors:
            if a.get("platform") != "guonongbang":
                continue
            fid = a.get("fid") or a.get("id")
            if not fid:
                continue
            try:
                fid_i = int(str(fid).lstrip("0") or "0")
            except ValueError:
                print(f"[guonongbang] 非法 fid: {fid}")
                continue
            board_name = a.get("name", "果农邦")
            try:
                threads = self._parse_list(fid_i, per)
            except Exception as e:
                print(f"[guonongbang] 版块 {fid_i} 列表抓取失败: {e}")
                continue
            for t in threads:
                try:
                    time.sleep(random.uniform(0.8, 1.8))  # 控频防封
                    cover, body = self._parse_detail(t["tid"])
                except Exception as e:
                    cover, body = "", ""
                    print(f"[guonongbang] 帖子 {t['tid']} 详情失败: {e}")
                summary = body[:90] if body else ""
                out.append({
                    "id": f"gnb_{t['tid']}",
                    "platform": "guonongbang",
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
    c = GuoNongBangCrawler()
    try:
        return c.crawl_authors(authors, per)
    finally:
        c.close()
