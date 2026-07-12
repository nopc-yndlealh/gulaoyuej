"""博主清单工具：解析链接 -> 抽平台/id/昵称 -> 写入 authors.json（去重）。

数据女工用法：
  python author_utils.py add <链接> [--name 昵称] [标签...]
  python author_utils.py list

支持输入：
  B站  : https://space.bilibili.com/123456        （空间主页，直接拿 mid）
         纯数字 123456                            （当作 B站 mid）
 小红书: https://www.xiaohongshu.com/user/profile/5fXXXX  （个人主页，拿 user_id）
         长串 5fXXXX                               （当作 小红书 user_id）
注意：小红书抓取按昵称搜索，建议链接旁用「昵称:xxx」标注；
      若省略昵称，resolve_name 会尝试抓主页 <title> 自动补（需联网，可能受反爬限制）。
      链接可带 ?xsec_token= / ?spm_id_from= 等追踪参数，解析时自动忽略，整串粘贴即可。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}

XHS_PROFILE_RE = re.compile(r"xiaohongshu\.com/user/profile/([0-9A-Za-z_]+)")
BILI_SPACE_RE = re.compile(r"space\.bilibili\.com/(\d+)")
BILI_VIDEO_RE = re.compile(r"bilibili\.com/video/(BV[0-9A-Za-z]+)")


def parse_author_url(url: str) -> dict:
    url = (url or "").strip()
    m = XHS_PROFILE_RE.search(url)
    if m:
        return {"platform": "xiaohongshu", "id": m.group(1), "name": "", "tags": []}
    m = BILI_SPACE_RE.search(url)
    if m:
        return {"platform": "bilibili", "id": m.group(1), "name": "", "tags": []}
    m = BILI_VIDEO_RE.search(url)
    if m:
        mid = _bili_resolve_bvid(m.group(1))
        if mid:
            return {"platform": "bilibili", "id": mid, "name": "", "tags": []}
    if url.isdigit():
        return {"platform": "bilibili", "id": url, "name": "", "tags": []}
    if re.fullmatch(r"[0-9A-Za-z_]{10,}", url):
        return {"platform": "xiaohongshu", "id": url, "name": "", "tags": []}
    raise ValueError(f"无法识别的链接: {url}")


def _bili_resolve_bvid(bvid: str) -> str | None:
    try:
        r = httpx.get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
                      headers=HEADERS, timeout=10, follow_redirects=True)
        return str(r.json().get("data", {}).get("owner", {}).get("mid", "")) or None
    except Exception:
        return None


def resolve_name(author: dict) -> str:
    """尽力补全昵称：B站走 acc/info；小红书抓主页标题。"""
    if author.get("name"):
        return author["name"]
    if author["platform"] == "bilibili":
        try:
            r = httpx.get(f"https://api.bilibili.com/x/space/acc/info?mid={author['id']}&jsonp=jsonp",
                          headers=HEADERS, timeout=10, follow_redirects=True)
            name = r.json().get("data", {}).get("name")
            if name:
                author["name"] = name
        except Exception:
            pass
    else:
        try:
            r = httpx.get(f"https://www.xiaohongshu.com/user/profile/{author['id']}",
                          headers=HEADERS, timeout=10, follow_redirects=True)
            m = re.search(r"<title>(.+?)的主页", r.text)
            if m:
                author["name"] = m.group(1).strip()
        except Exception:
            pass
    return author.get("name", "")


def add_author(url: str, tags: list[str] | None = None, name: str | None = None,
               authors_path: str | None = None) -> dict:
    authors_path = Path(authors_path or Path(__file__).resolve().parent / "authors.json")
    a = parse_author_url(url)
    if name:
        a["name"] = name
    if tags:
        a["tags"] = tags
    resolve_name(a)

    data = {"authors": []}
    if authors_path.exists():
        try:
            data = json.loads(authors_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    data.setdefault("authors", [])

    key = (a["platform"], str(a["id"]))
    existing_keys = [(x["platform"], str(x["id"])) for x in data["authors"]]
    if key in existing_keys:
        idx = existing_keys.index(key)
        if a["name"]:
            data["authors"][idx]["name"] = a["name"]
        for t in a["tags"]:
            if t not in data["authors"][idx].get("tags", []):
                data["authors"][idx].setdefault("tags", []).append(t)
        print(f"[author_utils] 已存在，合并标签: {a['platform']} {a['id']}")
    else:
        a.setdefault("trust", "high")
        data["authors"].append(a)
        print(f"[author_utils] 已添加: {a['platform']} {a['id']} name={a['name']!r} tags={a['tags']}")

    authors_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return a


def import_from_txt(path: str, authors_path: str | None = None) -> dict:
    """批量从 txt 导入博主清单。每行格式：
       <链接> [用户名 <昵称>] [标签...] [噪音/备注短语]
       '#' 开头为注释；空行跳过；链接可带 ? 追踪参数（自动忽略）。
       小红书用「用户名 X」标昵称；B站省略昵称（按 mid 抓，昵称后补）。
       含「噪音/注意过滤/影视/剪辑/但是/最适合/美丽程度/状态」视为审核备注 → notes，不进 tags。
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8-sig")
    authors_path = Path(authors_path or Path(__file__).resolve().parent / "authors.json")
    data = {"authors": []}
    if authors_path.exists():
        try:
            data = json.loads(authors_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    data.setdefault("authors", [])
    existing = {(x["platform"], str(x["id"])) for x in data["authors"]}

    NOISE_HINTS = ("噪音", "噪声", "注意过滤", "但是", "最适合", "美丽程度", "状态", "影视", "剪辑")
    results = {"added": [], "skipped": [], "failed": []}

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        toks = line.split()
        url = next((t for t in toks if ("bilibili" in t or "xiaohongshu" in t or t.startswith("http"))), None)
        if not url:
            results["failed"].append((line, "无链接"))
            continue
        try:
            a = parse_author_url(url)
        except ValueError as e:
            results["failed"].append((line, str(e)))
            continue
        rest = [t for t in toks if t != url]
        name = None
        tags = []
        notes = []
        i = 0
        while i < len(rest):
            tok = rest[i]
            if tok == "用户名" and i + 1 < len(rest):
                name = rest[i + 1]
                i += 2
                continue
            if any(h in tok for h in NOISE_HINTS):
                notes.append(tok)
            else:
                tags.append(tok)
            i += 1
        a["name"] = name or ""
        a["tags"] = tags
        if notes:
            a["notes"] = " ".join(notes)
        a.setdefault("trust", "high")
        key = (a["platform"], str(a["id"]))
        if key in existing:
            results["skipped"].append((a["platform"], a["id"]))
            continue
        data["authors"].append(a)
        existing.add(key)
        results["added"].append(a)

    authors_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


def _cli():
    if len(sys.argv) < 2:
        print("用法:")
        print("  python author_utils.py add <链接> [--name 昵称] [标签...]")
        print("  python author_utils.py list")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "import":
        if len(sys.argv) < 3:
            print("用法: python author_utils.py import <txt文件>")
            sys.exit(1)
        res = import_from_txt(sys.argv[2])
        print(f"导入完成 | 新增 {len(res['added'])} | 跳过重复 {len(res['skipped'])} | 失败 {len(res['failed'])}")
        for a in res["added"]:
            print(f"  + [{a['platform']}] {a['id']} name={a.get('name')!r} tags={a.get('tags')}")
        for f in res["failed"]:
            print(f"  ! 失败: {f}")
        sys.exit(0)
    if cmd == "list":
        p = Path(__file__).resolve().parent / "authors.json"
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            for a in d.get("authors", []):
                print(a)
        sys.exit(0)
    if cmd == "add":
        url = None
        name = None
        tags: list[str] = []
        i = 2
        while i < len(sys.argv):
            arg = sys.argv[i]
            if arg == "--name":
                name = sys.argv[i + 1]
                i += 2
                continue
            if (arg.startswith("http") or "bilibili" in arg or "xiaohongshu" in arg
                    or arg.isdigit() or re.fullmatch(r"[0-9A-Za-z_]{10,}", arg)):
                url = arg
            else:
                tags.append(arg)
            i += 1
        if not url:
            print("[author_utils] 缺少链接")
            sys.exit(1)
        add_author(url, tags=tags, name=name)
        sys.exit(0)
    print(f"[author_utils] 未知命令: {cmd}")
    sys.exit(1)


if __name__ == "__main__":
    _cli()
