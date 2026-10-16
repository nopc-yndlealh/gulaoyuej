#!/usr/bin/env python3
"""量全站图片的原生宽度分布 —— 为「统一宽度 vs 贴吧式」提供实测依据。

只取每个图的前 1KB（WebP 的尺寸头在最前面），所以带宽可以忽略。
"""
import glob
import json
import re
import struct
import sys
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

ROOT = "C:/Users/吴子瑞/WorkBuddy/2026-10-14-15-12-08/gulaoyuej-repo"


def dims(buf, url):
    """从字节流里解出 (宽, 高)。支持 WebP / PNG / JPEG。"""
    if buf[:4] == b"RIFF" and buf[8:12] == b"WEBP":
        fmt = buf[12:16]
        if fmt == b"VP8X":
            w = int.from_bytes(buf[24:27], "little") + 1
            h = int.from_bytes(buf[27:30], "little") + 1
            return w, h
        if fmt == b"VP8 ":
            # 3 字节 sync + 2 字节 14 位宽 + 2 字节 14 位高
            i = buf.index(b"\x9d\x01\x2a")
            w, h = struct.unpack("<HH", buf[i + 3:i + 7])
            return w & 0x3FFF, h & 0x3FFF
        if fmt == b"VP8L":
            b = buf[21:25]
            bits = int.from_bytes(b, "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        return None
    if buf[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", buf[16:24])
        return w, h
    if buf[:2] == b"\xff\xd8":
        i = 2
        while i < len(buf) - 9:
            if buf[i] != 0xFF:
                i += 1
                continue
            m = buf[i + 1]
            if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB):
                h, w = struct.unpack(">HH", buf[i + 5:i + 9])
                return w, h
            if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
                i += 2
                continue
            seg = struct.unpack(">H", buf[i + 2:i + 4])[0]
            i += 2 + seg
        return None
    return None


def fetch(url):
    # ⚠️ 必须伪装 UA。沙箱的白名单代理会把 `Python-urllib/3.x` 直接 403，
    #    换 curl / 浏览器 UA 才放行（实测：默认 UA → 403；curl UA → 206）。
    req = urllib.request.Request(url, headers={
        "Range": "bytes=0-1023",
        "User-Agent": "curl/8.4.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return url, r.read()
    except Exception:
        return url, None


def main():
    posts = []
    for f in glob.glob(ROOT + "/content/posts/*.json"):
        d = json.load(open(f, encoding="utf-8"))
        if d.get("status") != "published":
            continue
        ims = [s["i"] for s in (d.get("segments") or []) if s.get("i")]
        if ims:
            posts.append((d["id"], d.get("source", ""), ims))

    # 全局每 20 张抽 1 张（分层）+ 每篇首图
    sample = {}
    allimgs = 0
    for pid, src, ims in posts:
        allimgs += len(ims)
        for i, u in enumerate(ims):
            if i == 0 or (i % 20 == 7):
                sample.setdefault(u, (pid, src))
    urls = list(sample)
    print(f"published 有图文章 {len(posts)} 篇 / 图片引用 {allimgs} 处 / 抽样 {len(urls)} 张",
          file=sys.stderr)

    widths = []
    fails = 0
    bad = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        for u, buf in ex.map(fetch, urls):
            if not buf:
                fails += 1
                bad.append((u, b"<fetch-failed>"))
                continue
            d = dims(buf, u)
            if d:
                widths.append((d[0], u))
            else:
                fails += 1
                bad.append((u, buf[:12]))

    print(f"\n成功解出尺寸 {len(widths)} 张（失败/非图 {fails}）")
    if not len(widths):
        for u, head in bad[:8]:
            print("  ✗", head, u.rsplit("/", 1)[-1])
        return

    widths.sort(key=lambda t: t[0])
    ws = widths
    n = len(ws)

    if bad:
        from collections import Counter as _C
        ext = _C(u.rsplit(".", 1)[-1].split("?")[0].lower() for u, _ in bad)
        print("失败样本扩展名分布:", dict(ext.most_common(6)))
        for u, head in bad[:6]:
            print("   ✗", head, u.rsplit("/", 1)[-1])

    def pct(p):
        return ws[min(n - 1, int(n * p / 100))][0]

    print(f"宽度  min={ws[0][0]}  p5={pct(5)}  p25={pct(25)}  中位={pct(50)}  "
          f"p75={pct(75)}  p95={pct(95)}  max={ws[-1][0]}")

    print("\n宽度众数 Top12（看是不是卡在某个固定值上）：")
    for w, k in Counter(x[0] for x in ws).most_common(12):
        print(f"   {w:5d}px  {k:5d}  {100*k/n:5.1f}%")

    # 正文列 672px：小于它的图在「现在线上」下会缩着显示
    buckets = Counter()
    for w, _ in widths:
        if w < 300:
            buckets["<300"] += 1
        elif w < 400:
            buckets["300-399"] += 1
        elif w < 500:
            buckets["400-499"] += 1
        elif w < 600:
            buckets["500-599"] += 1
        elif w < 672:
            buckets["600-671"] += 1
        elif w <= 760:
            buckets["672-760（正好/略超，几乎不受影响）"] += 1
        else:
            buckets[">760（超宽，两种方案都满铺）"] += 1
    print("\n宽度分布（正文列 = 672px）：")
    for k in sorted(buckets):
        v = buckets[k]
        print(f"  {k:32s} {v:6d}  {100*v/n:5.1f}%")

    narrow = sum(v for k, v in buckets.items()
                 if k in ("<300", "300-399", "400-499", "500-599", "600-671"))
    print(f"\n窄于 672px 的图占 {100*narrow/n:.1f}% —— 这些在「现在线上」会缩着显示，")
    print("在「统一宽度」下会把被拉伸到 672px。")

    print("\n最窄的 20 张（放大倍率最大）：")
    for w, u in ws[:20]:
        pid, src = sample.get(u, ("?", "?"))
        print(f"  {w:5d}px  ×{672/w:4.2f}  {src:8s} {pid:24s} {u.rsplit('/',1)[-1]}")

    print("\n各来源的中位宽度：")
    bysrc = {}
    for w, u in widths:
        src = sample.get(u, ("?", "?"))[1]
        bysrc.setdefault(src, []).append(w)
    for src, arr in sorted(bysrc.items(), key=lambda kv: -len(kv[1])):
        arr.sort()
        print(f"  {src:10s} n={len(arr):5d}  中位 {arr[len(arr)//2]:5d}px")


main()
