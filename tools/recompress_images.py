#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重压超大在用图：同名覆盖 R2 对象 + 给 content/posts 里的 URL 加 ?opt=1。

为什么这么设计（都是实测出来的，不是想当然）：
  · 原地覆盖同名对象 → CDN 会继续给旧字节（边缘缓存 7 天），
    而我又拿不到 zone id 去清缓存 → 只能靠给 URL 加查询参数当新缓存键（实测有效）
  · 加查询参数比"写新 key"好：不产生新的孤儿对象，空间原地省下来，
    content/posts 的改动只是机械追加，且可 git 回退

缩放规则：装进 1344 × 4000 的框（只缩不放）
  · 1344 = 正文列 672px × 2，够高清屏
  · 高上限 4000 是为了治"长截图"：720×8928 那种图若按宽缩到 1344，
    高度会到 16665px；装框后是 322×4000，显示时放大约 2 倍，可接受

用法
  python3 tools/recompress_images.py --list            只看清单和预计效果（不改任何东西）
  python3 tools/recompress_images.py --apply --limit 5  试点 5 张
  python3 tools/recompress_images.py --apply            全量
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

import netfix  # noqa: F401,E402  —— 网络环境修复（IPv6 死路 / 沙箱伪代理），发请求前必须导入

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "content" / "posts"
BACKUP = ROOT / "content" / "_recompress_backup"
INV = Path.home() / ".workbuddy" / "tmp" / "r2_inventory.json"

BOX_W, BOX_H = 1344, 4000
QUALITY = 82
CACHE_SUFFIX = "?opt=1"
CORPUS = [Path(r"E:\WebP_Compressed\weibo"), Path(r"E:\WebP_Compressed\tieba"),
          Path(r"E:\WebP_Compressed\xhs"), Path(r"E:\WebP_Compressed\images_compressed\tieba"),
          Path(r"E:\月季数据\压缩图\webp\weibo")]
PUB = "https://img.feijibei.top/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")
TMP = Path(tempfile.gettempdir())

urs = urllib.request  # 直接全局装"不用代理"的 opener，绕开沙箱伪代理
urs.install_opener(urs.build_opener(urs.ProxyHandler({})))


def token():
    p = Path.home() / ".workbuddy" / "cf-token.txt"
    return p.read_text(encoding="utf-8").strip()


def r2_put(key, data):
    req = urs.Request(f"https://api.cloudflare.com/client/v4/accounts/"
                      f"082a519e512f02161ab5a57b376d3f4c/r2/buckets/plant-images/"
                      f"objects/{urllib.parse.quote(key)}", data=data, method="PUT")
    req.add_header("Authorization", "Bearer " + token())
    req.add_header("Content-Type", "image/webp")
    with urs.urlopen(req, timeout=180) as r:
        return r.status


def fetch(key, timeout=180):
    req = urs.Request(PUB + key)
    req.add_header("User-Agent", UA)
    with urs.urlopen(req, timeout=timeout) as r:
        return r.read()


def webp_dims(b):
    import struct
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


def local_copy(name):
    for d in CORPUS:
        p = d / name
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def load_inventory():
    if not INV.exists():
        print("✗ 没有清单缓存，先跑 r2_inventory.py", file=sys.stderr)
        sys.exit(1)
    return {o["k"].split("/", 1)[1]: o["s"]
            for o in json.loads(INV.read_text(encoding="utf-8"))["objs"]}


def build_refmap():
    """文件名 → [post 文件路径]"""
    m = {}
    for f in POSTS.glob("*.json"):
        try:
            txt = f.read_text(encoding="utf-8")
        except Exception:
            continue
        for name in []:
            pass
        m[f] = txt
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--min-mb", type=float, default=1.0)
    args = ap.parse_args()
    if not (args.list or args.apply):
        ap.print_help()
        return 1

    size_of = load_inventory()
    posts = {}
    for f in POSTS.glob("*.json"):
        posts[f] = json.loads(f.read_text(encoding="utf-8"))
    refs = {}                       # 文件名 → [path]
    for f, d in posts.items():
        for s in d.get("segments") or []:
            u = s.get("i")
            if isinstance(u, str) and "/images/" in u:
                refs.setdefault(u.rsplit("/", 1)[-1], []).append(f)

    targets = sorted([(n, s) for n, s in size_of.items()
                      if s >= args.min_mb * 1048576 and n in refs],
                     key=lambda x: -x[1])
    if args.limit:
        targets = targets[:args.limit]

    total_old = sum(s for _, s in targets)
    print(f"目标：{len(targets)} 张「在用且 ≥{args.min_mb}MB」的图，共 {total_old/1048576:,.1f} MB\n")
    print(f"{'原大小':>9} {'尺寸':>12}  文件名")
    for n, s in targets[:40]:
        src = local_copy(n)
        dim = ""
        if src:
            d = webp_dims(src.read_bytes()[:8192])
            dim = f"{d[0]}×{d[1]}" if d else ""
        print(f"{s/1048576:8.2f}M {dim:>12}  {n[:66]}")
    if len(targets) > 40:
        print(f"  … 另有 {len(targets)-40} 张")

    if args.list:
        print(f"\n（--list 模式，没有改动任何东西）")
        return 0

    # ── 执行 ──
    BACKUP.mkdir(parents=True, exist_ok=True)
    done, skipped, failed = [], [], []
    new_bytes_total = 0
    for i, (n, old_size) in enumerate(targets, 1):
        key = "images/" + n
        try:
            src = local_copy(n)
            raw = src.read_bytes() if src else fetch(key)
            dim = webp_dims(raw[:8192])
            if not dim:
                skipped.append((n, "读不出尺寸")); continue
            w, h = dim
            if max(w, h) <= BOX_W:
                skipped.append((n, f"本来就够小 {w}×{h}")); continue
            scale = min(BOX_W / w, BOX_H / h, 1.0)
            tw, th = max(1, round(w * scale)), max(1, round(h * scale))
            # ⚠️ 临时文件必须带扩展名：ffmpeg 靠扩展名判断输出格式，
            #    叫 _rc_out（无后缀）会直接报 "Unable to choose an output format"
            tin, tout = TMP / "_rc_in.webp", TMP / "_rc_out.webp"
            tin.write_bytes(raw)
            p = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tin),
                                "-vf", f"scale={tw}:{th}", "-c:v", "libwebp",
                                "-quality", str(QUALITY), str(tout)],
                               capture_output=True)
            if p.returncode != 0 or not tout.exists():
                failed.append((n, "ffmpeg: " + p.stderr[:80].decode("utf-8", "replace")))
                continue
            newb = tout.read_bytes()
            if len(newb) >= old_size * 0.95:
                skipped.append((n, f"压不下来（{old_size/1048576:.1f}→{len(newb)/1048576:.1f}M）"))
                continue
            st = r2_put(key, newb)
            if st != 200:
                failed.append((n, f"PUT HTTP {st}")); continue
            new_bytes_total += len(newb)
            done.append((n, old_size, len(newb), f"{w}×{h}", f"{tw}×{th}"))
            print(f"  [{i}/{len(targets)}] ✓ {n[:50]}  "
                  f"{old_size/1048576:5.2f}M → {len(newb)/1048576:5.2f}M  "
                  f"{w}×{h} → {tw}×{th}")
        except Exception as e:
            failed.append((n, f"{type(e).__name__}: {e}"))
            print(f"  [{i}/{len(targets)}] ✗ {n[:50]}  {e}")

    # ── 改引用：给对应 URL 追加 ?opt=1 ──
    changed_posts = 0
    if done:
        new_urls = {PUB + "images/" + n: PUB + "images/" + n + CACHE_SUFFIX
                    for n, *_ in done}

        def walk(o):
            if isinstance(o, dict):
                return {k: walk(v) for k, v in o.items()}
            if isinstance(o, list):
                return [walk(v) for v in o]
            if isinstance(o, str) and o in new_urls:
                return new_urls[o]
            return o

        for f, d in posts.items():
            nd = walk(d)
            if nd != d:
                BACKUP.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, BACKUP / f.name)
                f.write_text(json.dumps(nd, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
                changed_posts += 1

    print(f"\n═══ 结果 ═══")
    print(f"  成功重压 : {len(done)} 张")
    print(f"  跳过     : {len(skipped)}")
    print(f"  失败     : {len(failed)}")
    if done:
        print(f"  空间     : {sum(o for _, o, *_ in done)/1048576:,.1f} MB "
              f"→ {new_bytes_total/1048576:,.1f} MB "
              f"（省 {(sum(o for _, o, *_ in done)-new_bytes_total)/1048576:,.1f} MB）")
        print(f"  改了引用 : {changed_posts} 个 post 文件（原文件备份在 content/_recompress_backup/）")
    for n, why in (skipped + failed)[:12]:
        print(f"    - {n[:54]}  {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
