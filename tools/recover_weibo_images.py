#!/usr/bin/env python3
"""
从微博把「抓取时漏掉的图片」捞回来。

背景
----
站内有 68 条已上架微博是"无图"的。查下来只有 28 条原帖真的没图，
另外 38 条**有图** —— 而且多半能解释得通：那些多是**转发帖**，
图片在被转发的原帖里，而当年的爬虫只看了主帖自己的 `pics`，于是全漏了。

    例：「悬钩子属好多看起来一毛一样的…」缺 18 张
        「单瓣月季花中山植物园藏了这么多好东西」缺 13 张
        「很有意义的蔷薇拟人…」缺 1 张（其实原帖是视频，有封面图）

流程
----
  1. 用 m.weibo.cn 接口取帖子详情（需要 cookie，见下）
  2. 从「主帖」与「转发原帖」两处收集图片：pics[].large.url 与
     page_info.page_pic.url（视频封面）
  3. 下载（**Referer 必须是 weibo.com / m.weibo.cn**，否则 403）
  4. 转 WebP（长边上限 1600，quality 82），命名沿用 R2 既有规范
     images/weibo__<微博图床文件名>.webp
  5. 上传 R2，并把图片段插到正文最前面（与历史微博条目的图在前一致）

凭据
----
  - 微博 cookie：默认读
    `E:\\MediaCrawler_Output\\weibo\\cookies.txt`（JSON 数组格式）
    这份 cookie 在 2027 年才过期，实测仍有效；失效时用浏览器重新导出即可
  - R2：读 ~/.workbuddy/cf-token.txt（见 tools/r2.py）

用法
----
  python3 tools/recover_weibo_images.py                 # 预演：只报告能补什么
  python3 tools/recover_weibo_images.py --apply          # 下载 + 转码 + 上传 + 改数据
  python3 tools/recover_weibo_images.py --apply --limit 5
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "content" / "posts"
CFG_WEIBO_COOKIES = Path(r"E:\MediaCrawler_Output\weibo\cookies.txt")
WORK = ROOT / ".recover-tmp"          # 临时下载目录，用完删

ACC = "082a519e512f02161ab5a57b376d3f4c"
BUCKET = "plant-images"
R2_API = f"https://api.cloudflare.com/client/v4/accounts/{ACC}/r2/buckets/{BUCKET}/objects"
R2_PREFIX = "https://img.feijibei.top/"

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")
MAX_EDGE = 1600
QUALITY = 82


# ── 凭据 ──
def weibo_cookie():
    if not CFG_WEIBO_COOKIES.exists():
        print(f"✗ 找不到微博 cookie：{CFG_WEIBO_COOKIES}", file=sys.stderr)
        sys.exit(1)
    arr = json.loads(CFG_WEIBO_COOKIES.read_text(encoding="utf-8", errors="replace"))
    return "; ".join(f"{c['name']}={c['value']}" for c in arr
                     if c.get("name") and c.get("value"))


def r2_token():
    t = os.environ.get("CLOUDFLARE_API_TOKEN")
    if t:
        return t.strip()
    p = Path.home() / ".workbuddy" / "cf-token.txt"
    if p.exists():
        v = p.read_text(encoding="utf-8").strip()
        if v:
            return v
    print("✗ 找不到 Cloudflare 令牌", file=sys.stderr)
    sys.exit(1)


# ── 微博 ──
def fetch_post(mid, ck):
    req = urllib.request.Request(f"https://m.weibo.cn/statuses/show?id={mid}")
    req.add_header("Cookie", ck)
    req.add_header("User-Agent", UA)
    req.add_header("Referer", "https://m.weibo.cn/")
    req.add_header("X-Requested-With", "XMLHttpRequest")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        return {"ok": 0, "err": str(e)[:80]}


def harvest(d):
    """收集可用的图片 URL（含视频封面），返回 [(来源说明, url)]"""
    out = []
    data = d.get("data") or {}

    def take(node, tag):
        for p in (node.get("pics") or []):
            u = (p.get("large") or {}).get("url") or p.get("url")
            if u:
                out.append((tag, u))
        pi = node.get("page_info") or {}
        if pi.get("type") == "video":
            pp = (pi.get("page_pic") or {}).get("url")
            if pp:
                out.append((tag + "(视频封面)", pp))

    take(data, "主帖")
    rl = data.get("retweeted_status")
    if rl:
        take(rl, "转发原帖")
    # 去重，保持顺序
    seen, uniq = set(), []
    for tag, u in out:
        key = image_stem(u)
        if key not in seen:
            seen.add(key)
            uniq.append((tag, u))
    return uniq


def image_stem(url):
    """从微博图床 URL 里取文件名（不含扩展名），作为 R2 对象名的一部分"""
    return os.path.splitext(os.path.basename(url.split("?")[0]))[0]


# ── 下载 / 转码 / 上传 ──
def download(url, dest):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    # ⚠️ 必须带微博 Referer：实测无 Referer 或带自己站点 Referer 都是 403
    req.add_header("Referer", "https://m.weibo.cn/")
    with urllib.request.urlopen(req, timeout=90) as r:
        b = r.read()
    if len(b) < 1024 or b[:3] != b"\xff\xd8\xff" and b[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError(f"下载内容不是图片（{len(b)} 字节）")
    dest.write_bytes(b)
    return len(b)


def webp_size(p):
    """读 WebP 的真实像素尺寸（不依赖 PIL）。"""
    import struct
    b = Path(p).read_bytes()[:8192]
    if b[:4] != b"RIFF" or b[8:12] != b"WEBP":
        return None
    pos = 12
    while pos + 8 <= len(b):
        tag, size = b[pos:pos + 4], struct.unpack("<I", b[pos + 4:pos + 8])[0]
        body = b[pos + 8:pos + 8 + size]
        if tag == b"VP8 " and body[3:6] == b"\x9d\x01\x2a":
            w, h = struct.unpack("<HH", body[6:10])
            return w & 0x3FFF, h & 0x3FFF
        if tag == b"VP8L":
            v = struct.unpack("<I", body[1:5])[0]
            return (v & 0x3FFF) + 1, ((v >> 14) & 0x3FFF) + 1
        if tag == b"VP8X":
            return ((body[4] | body[5] << 8 | body[6] << 16) + 1,
                    (body[7] | body[8] << 8 | body[9] << 16) + 1)
        pos += 8 + size + (size & 1)
    return None


def to_webp(src, dest):
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
           "-vf", f"scale='min({MAX_EDGE},iw)':-2",
           "-c:v", "libwebp", "-quality", str(QUALITY), str(dest)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not dest.exists():
        raise RuntimeError("ffmpeg 转码失败：" + (r.stderr or "")[:200])
    # ★ 后置校验：不能只看"ffmpeg 退出码是 0"就当压好了。
    #   实测 R2 上 475 张在用图超过 1MB（最大 20.36 MB、5976×10697），
    #   就是当年漏了缩放也没人发现 —— 少了这一道检查，同一个坑会再犯一次。
    #   宁可这里直接炸掉，也不要静默把巨图传上云端。
    #
    #   判据要和上面的 -vf 语义一致：那条命令是**限宽度**到 MAX_EDGE
    #   （竖图会得到 1600×2864 这种，高度可以超）。所以这里只看宽度 ——
    #   一开始我用 max(w,h) 判，把正常输出也拦了，是自己想当然。
    dim = webp_size(dest)
    if dim and dim[0] > MAX_EDGE:
        raise RuntimeError(f"缩放没生效：输出 {dim[0]}×{dim[1]}，宽度超过 {MAX_EDGE}")
    return dest.stat().st_size


def r2_exists(name, tok):
    q = urllib.parse.urlencode({"prefix": "images/" + name, "per_page": "1"})
    req = urllib.request.Request(R2_API + "?" + q)
    req.add_header("Authorization", "Bearer " + tok)
    for _ in range(2):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read().decode()).get("result") or []
                return any(o["key"] == "images/" + name for o in res)
        except Exception:
            time.sleep(1)
    return False


def r2_put(name, data, tok, tries=3):
    """上传到 R2。

    ⚠️ 必须捕获**所有**异常，不能只捕 HTTPError —— 实测跑长任务时会碰到
    `TimeoutError: The read operation timed out`（网络抖动），
    只捕 HTTPError 的话一个超时就会把整个恢复进程打死，
    而前面的成果只在内存里、还没写回 post 文件。
    """
    url = f"{R2_API}/{urllib.parse.quote('images/' + name)}"
    for i in range(tries):
        req = urllib.request.Request(url, data=data, method="PUT")
        req.add_header("Authorization", "Bearer " + tok)
        req.add_header("Content-Type", "image/webp")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status == 200
        except urllib.error.HTTPError as e:
            # 4xx/5xx：4xx 是权限/参数问题，重试无意义
            if 400 <= e.code < 500:
                print(f"      ✗ R2 拒绝（HTTP {e.code}）：{e.read()[:120]}", file=sys.stderr)
                return False
        except Exception as e:
            if i == tries - 1:
                print(f"      ✗ R2 上传失败（{type(e).__name__}，重试 {tries} 次）：{str(e)[:80]}",
                      file=sys.stderr)
                return False
        time.sleep(1.5 * (i + 1))   # 退避重试
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际下载/上传/改数据")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    ck = weibo_cookie()
    tok = r2_token() if args.apply else None
    if args.apply:
        WORK.mkdir(exist_ok=True)

    posts = []
    for f in sorted(POSTS.glob("*.json")):
        with open(f, encoding="utf-8") as fh:
            posts.append((f, json.load(fh)))

    targets = [(f, p) for f, p in posts
               if p.get("cat") == "微博" and p.get("status") == "published"
               and not any(s.get("i") for s in p.get("segments", []))]
    if args.limit:
        targets = targets[:args.limit]
    print(f"待处理：{len(targets)} 条无图微博" + ("（--apply 会真下载）" if args.apply else "（预演）"))
    print()

    stat = {"已补条目": 0, "原帖无图": 0, "接口失败": 0, "图片": 0,
            "跳过已存在": 0, "下载失败": 0}
    for f, p in targets:
        mid = p["id"].split("_", 1)[1]
        d = fetch_post(mid, ck)
        if d.get("ok") != 1:
            stat["接口失败"] += 1
            print(f"  ? {p['id']}  接口失败：{d.get('err') or d.get('msg')}")
            time.sleep(0.6)
            continue
        imgs = harvest(d)
        if not imgs:
            stat["原帖无图"] += 1
            time.sleep(0.5)
            continue

        print(f"  ▸ {p['id']}  {len(imgs)} 张  {p.get('title','')[:34]}")
        added = []
        for tag, url in imgs:
            stem = image_stem(url)
            name = f"weibo__{stem}.webp"
            if args.apply and r2_exists(name, tok):
                stat["跳过已存在"] += 1
                added.append(R2_PREFIX + "images/" + name)
                continue
            if not args.apply:
                added.append(R2_PREFIX + "images/" + name)
                print(f"      [预演] {tag} → {name}")
                continue
            jpg = WORK / "_t.jpg"
            webp = WORK / "_t.webp"
            try:
                nb = download(url, jpg)
                wb = to_webp(jpg, webp)
            except Exception as e:
                stat["下载失败"] += 1
                print(f"      ✗ {stem[:40]}：{e}")
                continue
            if not r2_put(name, webp.read_bytes(), tok):
                stat["下载失败"] += 1
                continue
            # ⚠️ 这里刻意**不删临时文件**，而是复用同一个文件名反复覆盖。
            # 原因：每张图 unlink 一次，跑到 50 个左右会撞上沙箱的批量删除保护
            # （SAFE_DELETE_BULK_CONFIRM_REQUIRED），整个进程会被打断 ——
            # 表现就是「图传上 R2 了，但 post 文件没写回」，很难查。
            added.append(R2_PREFIX + "images/" + name)
            stat["图片"] += 1
            print(f"      ✓ {tag}  {nb//1024}KB → {wb//1024}KB  {name[:56]}")
            time.sleep(0.25)

        if args.apply and added:
            # 图片段放最前，与历史微博条目的「图在前」一致
            segs = [{"i": u} for u in added] + [s for s in p.get("segments", [])]
            p["segments"] = segs
            p["thumb"] = added[0]
            with open(f, "w", encoding="utf-8") as fh:
                json.dump(p, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            stat["已补条目"] += 1
        time.sleep(0.6)

    print()
    print("=== 统计 ===")
    for k, v in stat.items():
        print(f"  {k}: {v}")
    if not args.apply:
        print("\n── 预演结束，未下载也未改数据。加 --apply 实际执行。──")
    else:
        print("\n下一步：python3 tools/build.py")
        try:
            WORK.rmdir()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
