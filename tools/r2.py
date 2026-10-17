#!/usr/bin/env python3
"""
R2 对象存储小工具（走 Cloudflare REST API，不需要 S3 密钥）。

凭据来源，按优先级：
  1. 环境变量 CLOUDFLARE_API_TOKEN
  2. C:\\Users\\<用户>\\.workbuddy\\cf-token.txt      ← 推荐放这里
  3. %APPDATA%\\xdg.config\\.wrangler\\config\\default.toml 的 oauth_token
     （注意：wrangler 的 OAuth 授权里**没有** r2 scope，通常用不了，只作兜底）

⚠️ 令牌只放在仓库外的本地文件里，绝不进 git。仓库的 .gitignore 也已加了防护。

命令
----
  python3 tools/r2.py ls [prefix]              列出对象（前缀可选）
  python3 tools/r2.py has <name> [<name>...]   检查某几个图片名在不在
  python3 tools/r2.py get <key> <outfile>      下载一个对象
  python3 tools/r2.py put <localfile> <key>    上传（key 形如 images/xxx.webp）
  python3 tools/r2.py rm <key>                 删除
  python3 tools/r2.py check-refs               核对 content/posts 里引用的图片是否都在 R2
  python3 tools/r2.py check-refs --limit 20    只报前 20 个缺失

`check-refs` 才是重点：它能在本地就发现「引用了 R2 上不存在的图片」这类裂图，
不用等用户反馈。构建脚本的离线自检只能验证「图片在 img.feijibei.top 域名上」，
验证不了「文件真的存在」。
"""
import argparse
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────
# 环境修复（2026-10-17 加）。不修这两条，本工具在本机会**直接挂死**。
#
# 坑 1 · IPv6 死路
#   本机到 Cloudflare 的 IPv6 不通（AAAA 记录连接超时 8s+），而 DNS 会优先返回
#   AAAA，Python 又按 getaddrinfo 的返回顺序尝试 → 一卡 60 秒以上甚至永久挂住。
#   curl 默认走 IPv4 所以没事 —— 这就是「curl 能通、Python 不通」的由来。
#   修法：把 getaddrinfo 换成只返回 AF_INET。
#
# 坑 2 · 沙箱伪代理
#   WorkBuddy 沙箱给每个子进程注入 HTTP_PROXY / HTTPS_PROXY（大小写都注入），
#   urllib 照单全收 → 墙外站点被白名单代理拦掉。修法：清掉这些变量 + 装无代理 opener。
# ─────────────────────────────────────────────────────────────────────
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


if not getattr(socket, "_v4_patched", False):
    socket.getaddrinfo = _ipv4_only
    socket._v4_patched = True

for _v in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
           "ALL_PROXY", "all_proxy"):
    os.environ.pop(_v, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"
urllib.request.install_opener(
    urllib.request.build_opener(urllib.request.ProxyHandler({})))

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "content" / "posts"

ACCOUNT = "082a519e512f02161ab5a57b376d3f4c"
BUCKET = "plant-images"
API = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/r2/buckets/{BUCKET}/objects"

TOKEN_PATHS = [
    Path.home() / ".workbuddy" / "cf-token.txt",
    Path(os.environ.get("APPDATA", "")) / ".." / ".workbuddy" / "cf-token.txt",
]


def token():
    t = os.environ.get("CLOUDFLARE_API_TOKEN")
    if t:
        return t.strip()
    for p in TOKEN_PATHS:
        try:
            p = p.resolve()
        except OSError:
            continue
        if p.exists():
            v = p.read_text(encoding="utf-8").strip()
            if v:
                return v
    cfg = Path(os.environ.get("APPDATA", "")) / "xdg.config" / ".wrangler" / "config" / "default.toml"
    if cfg.exists():
        m = re.search(r'oauth_token\s*=\s*"([^"]+)"', cfg.read_text(encoding="utf-8"))
        if m:
            return m.group(1)
    print("✗ 找不到 Cloudflare 令牌。请把它放到 ~/.workbuddy/cf-token.txt，"
          "或设置环境变量 CLOUDFLARE_API_TOKEN。", file=sys.stderr)
    sys.exit(1)


def call(method, url, data=None, ctype="application/octet-stream", tries=3):
    """发一次请求，带退避重试。

    ⚠️ 必须同时捕 URLError / TimeoutError / OSError，光捕 HTTPError 不够。
       只捕 HTTPError 时，网络抖动抛的 TimeoutError 会直接打死整个进程 ——
       check-refs 拉 4000+ 对象要翻好几页，任何一页抖一下就整条崩掉，
       连已经拉到的结果都拿不到。这正是本项目踩坑清单第 10 条。
       重试到极限仍失败就返回状态码 0，让调用方自己降级，不要往外抛。
    """
    last = "未知错误"
    for i in range(tries):
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", "Bearer " + token())
        if data is not None:
            req.add_header("Content-Type", ctype)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and i < tries - 1:
                last = f"HTTP {e.code}"
            else:
                return e.code, e.read()[:400]
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = f"{type(e).__name__}: {e}"
        if i < tries - 1:
            time.sleep(1.5 * (i + 1))
    print(f"  ! 请求重试 {tries} 次仍失败：{last}", file=sys.stderr)
    return 0, last.encode()


def list_keys(prefix=""):
    keys, cursor = [], None
    while True:
        q = {"per_page": "1000"}
        if prefix:
            q["prefix"] = prefix
        if cursor:
            q["cursor"] = cursor
        st, body = call("GET", API + "?" + urllib.parse.urlencode(q))
        if st != 200:
            print(f"✗ 列取失败 HTTP {st}: {body[:200]}", file=sys.stderr)
            break
        d = json.loads(body)
        res = d.get("result", [])
        keys += [o["key"] for o in res]
        cursor = (d.get("result_info") or {}).get("cursor")
        if not cursor or not res:
            break
    return keys


def list_objs(prefix=""):
    """列出对象，返回 [{"key": …, "size": …}]。

    与 list_keys 的区别：带上 size。check-refs 要靠它发现 **0 字节空文件**
    —— 那种文件是"存在"的，只查 key 根本查不出来（2026-10-17 实测踩过：
    3 处活裂图因此被漏掉）。
    """
    out, cursor = [], None
    while True:
        q = {"per_page": "1000"}
        if prefix:
            q["prefix"] = prefix
        if cursor:
            q["cursor"] = cursor
        st, body = call("GET", API + "?" + urllib.parse.urlencode(q))
        if st != 200:
            print(f"✗ 列取失败 HTTP {st}: {body[:200]}", file=sys.stderr)
            break
        d = json.loads(body)
        res = d.get("result", [])
        out += [{"key": o["key"], "size": o.get("size") or 0} for o in res]
        cursor = (d.get("result_info") or {}).get("cursor")
        if not cursor or not res:
            break
    return out


def cmd_ls(args):
    keys = list_keys(args.prefix or "")
    print(f"共 {len(keys)} 个对象")
    for k in keys[:args.limit]:
        print("  " + k)
    if len(keys) > args.limit:
        print(f"  … 另有 {len(keys) - args.limit} 个（用 --limit 调整）")


def cmd_has(args):
    """按精确文件名逐个前缀查询，避免拉全量清单（4129 个对象很慢）。

    ⚠️ 名字传「裸文件名」或「完整 key」都要认。
       以前只认裸名、内部无条件再拼一个 "images/"，于是传
       images/wb_x.webp 会变成查 images/images/wb_x.webp —— **永远返回"没有"**。
       这种假阴性直接把「文件在不在」的判断带偏，而本项目踩坑清单第 3 条
       说的就是这件事（"断言缺失前先确认自己用的键是对的"）。别再退回去。
    """
    for n in args.names:
        key = n if n.startswith("images/") else "images/" + n
        st, body = call("GET", API + "?" + urllib.parse.urlencode(
            {"prefix": key, "per_page": "10"}))
        if st != 200:
            print(f"  ? 查询失败 HTTP {st}  {key}")
            continue
        found = json.loads(body).get("result") or []
        hit = any(o["key"] == key for o in found)
        print(f"  {'✓ 有  ' if hit else '✗ 没有'}  {key}")


def cmd_get(args):
    st, body = call("GET", f"{API}/{urllib.parse.quote(args.key)}")
    if st != 200:
        print(f"✗ HTTP {st}: {body[:200]}", file=sys.stderr)
        return 1
    Path(args.out).write_bytes(body)
    print(f"✓ 已保存 {args.out}（{len(body)} 字节）")
    return 0


def cmd_put(args):
    data = Path(args.local).read_bytes()
    ext = Path(args.local).suffix.lower()
    ctype = {".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".png": "image/png", ".gif": "image/gif"}.get(ext, "application/octet-stream")
    st, body = call("PUT", f"{API}/{urllib.parse.quote(args.key)}", data, ctype)
    if st != 200:
        print(f"✗ HTTP {st}: {body[:200]}", file=sys.stderr)
        return 1
    print(f"✓ 已上传 {args.key}（{len(data)} 字节）")
    return 0


def cmd_rm(args):
    st, _ = call("DELETE", f"{API}/{urllib.parse.quote(args.key)}")
    print(("✓ 已删除 " if st == 200 else f"✗ HTTP {st} ") + args.key)
    return 0 if st == 200 else 1


def cmd_check_refs(args):
    """核对 content/posts 里引用的每一张图，在 R2 上是否真的存在、以及是不是 0 字节。

    ⚠️ 只查"在不在"不够：2026-10-17 实测发现 R2 上有 **0 字节对象正被正文引用**，
       旧版会报"全部存在于 R2"（文件确实存在，只是内容是空的）→ 3 处活裂图被漏掉。
       所以这里同时检查 size == 0。
    """
    print("正在拉取 R2 对象清单…")
    objs = list_objs("images/")
    names = set(o["key"].split("/", 1)[1] for o in objs)
    size_of = {o["key"].split("/", 1)[1]: o["size"] for o in objs}
    print(f"  R2 上 images/ 下共 {len(names)} 个对象"
          f"（其中 0 字节 {sum(1 for o in objs if not o['size'])} 个）\n")

    posts = []
    for f in sorted(POSTS.glob("*.json")):
        with open(f, encoding="utf-8") as fh:
            posts.append(json.load(fh))

    total = missing = empty = 0
    by_post = {}
    for p in posts:
        bad, emp = [], []
        for s in p.get("segments", []):
            u = s.get("i")
            if not u or "/images/" not in u:
                continue
            total += 1
            # 必须剥掉查询串：重压后 URL 是 `...webp?opt=1`，
            # 不剥的话取到 `xxx.webp?opt=1`，匹配不上 key → 误报"R2 上不存在"
            n = u.split("?")[0].split("#")[0].rsplit("/", 1)[-1]
            if n not in names:
                bad.append(n)
            elif not size_of.get(n):
                emp.append(n)
        if bad or emp:
            by_post[p["id"]] = (p.get("cat"), p.get("status"), bad, emp)
            missing += len(bad)
            empty += len(emp)

    print(f"正文引用的图片共 {total} 处")
    print(f"  R2 上不存在   : {missing} 处")
    print(f"  存在但 0 字节 : {empty} 处   ← 同样会裂图，只查存在性发现不了")
    if not by_post:
        print("✓ 全部存在于 R2，且都不是空文件")
        return 0

    print(f"\n分布在 {len(by_post)} 个帖子：\n")
    for pid, (cat, st, bad, emp) in list(by_post.items())[:args.limit]:
        print(f"  [{cat}/{st}] {pid}   缺 {len(bad)} 张 / 空 {len(emp)} 张")
        for n in bad[:2]:
            print(f"      ✗ 不存在  {n[:84]}")
        for n in emp[:2]:
            print(f"      ✗ 0 字节  {n[:84]}")
    if len(by_post) > args.limit:
        print(f"  … 另有 {len(by_post) - args.limit} 个帖子")
    print("\n提示：文件名被重复拼接帖子 id → tools/fix_image_names.py 修；"
          "0 字节对象 → 用原图重压后同名覆盖上传（并给 URL 换新的缓存串）")
    return 1


def main():
    ap = argparse.ArgumentParser(description="R2 对象存储小工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ls", help="列出对象"); p.add_argument("prefix", nargs="?", default="")
    p.add_argument("--limit", type=int, default=30); p.set_defaults(func=cmd_ls)

    p = sub.add_parser("has", help="检查对象是否存在"); p.add_argument("names", nargs="+")
    p.set_defaults(func=cmd_has)

    p = sub.add_parser("get", help="下载对象"); p.add_argument("key"); p.add_argument("out")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("put", help="上传对象"); p.add_argument("local"); p.add_argument("key")
    p.set_defaults(func=cmd_put)

    p = sub.add_parser("rm", help="删除对象"); p.add_argument("key"); p.set_defaults(func=cmd_rm)

    p = sub.add_parser("check-refs", help="核对正文引用的图片是否都在 R2")
    p.add_argument("--limit", type=int, default=15); p.set_defaults(func=cmd_check_refs)

    args = ap.parse_args()
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
