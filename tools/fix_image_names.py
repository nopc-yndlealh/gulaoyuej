#!/usr/bin/env python3
"""
修复正文里「文件名被重复拼接了帖子 id」的微博图片引用。

问题
----
微博图片在 R2 上的规范命名是：
    images/weibo__<原始图片名>.webp
但站内有一批引用长这样（帖子 id 被重复拼了 2~4 次）：
    images/weibo_5053290972712299_weibo_5053290972712299_weibo_5053290972712299_5053290972712299_007C5AOXly1hqwryp5ugoj379e5eie86.webp

这些名字在 R2 上不存在 → **图片全是裂的**。实测全站 980 处、
分布：古老月季 401 / 养花日常 282 / 古代园艺 249 / 微博 48。

好消息：**图本身一张没丢**，R2 上都有正确的文件（
`images/weibo__5053290972712299_007C5AOXly1hqwryp5ugoj379e5eie86.webp`），
所以只需要把引用名规范回去，不用下载也不用上传。

归属怎么确定
------------
坏名字里内嵌了帖子 id，正好能验证归属：
    weibo_<帖子id>_..._<原始图片名>.webp
自检会把「坏名里的帖子 id」与「该图所在的 post 文件 id」对照，
不一致就报警 —— 避免张冠李戴。

用法
----
  python3 tools/fix_image_names.py                    # 预演
  python3 tools/fix_image_names.py --apply            # 实际写入
  python3 tools/fix_image_names.py --r2-keys FILE     # 用 R2 对象清单校验目标是否存在
"""
import argparse
import glob
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "content" / "posts"

# 「坏名」的严格判定：必须以 weibo_<16位帖子id>_ 开头。
# ⚠️ 绝不能写成「只要不以 weibo__ 开头就加前缀」—— 那会把 rose_/tieba_/xhs_
# 这些正常图片一起改坏（预演时踩过这个坑）。
BAD_RE = re.compile(r"^weibo_\d{16}_")
PREFIX_RE = re.compile(r"^(weibo_\d{16}_)+")


def is_bad(name):
    s = name[:-5] if name.lower().endswith(".webp") else name
    return bool(BAD_RE.match(s))


def canonical(name):
    """坏文件名 → 规范文件名。不是坏名则原样返回。"""
    if not is_bad(name):
        return name
    s = name[:-5] if name.lower().endswith(".webp") else name
    return "weibo__" + PREFIX_RE.sub("", s) + ".webp"


def embedded_pids(name):
    """从坏名里取出被重复拼接的帖子 id，用于归属自检。"""
    return re.findall(r"weibo_(\d{16})_", name)


def fix_url(url):
    if not isinstance(url, str) or "/images/" not in url:
        return url, None
    head, name = url.rsplit("/", 1)
    new = canonical(name)
    return (url, None) if new == name else (head + "/" + new, (name, new))


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def dump(p, obj):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写入（默认只预演）")
    ap.add_argument("--r2-keys", type=Path, default=None,
                    help="R2 对象清单（每行一个 key），用于校验目标确实存在")
    args = ap.parse_args()

    r2names = set()
    if args.r2_keys and args.r2_keys.exists():
        with open(args.r2_keys, encoding="utf-8") as f:
            r2names = {l.strip().split("/", 1)[1] for l in f if l.strip()}
        print(f"已载入 R2 对象名 {len(r2names)} 个（用于校验目标存在性）\n")

    changed = []
    all_bad = Counter()
    mismatch_pid, target_missing = [], []

    for f in sorted(POSTS.glob("*.json")):
        post = load(f)
        seg_hits = []
        for seg in post.get("segments", []):
            new, info = fix_url(seg.get("i"))
            if info:
                seg_hits.append((info, seg))
                all_bad[info[0]] += 1

        thumb_new, thumb_info = fix_url(post.get("thumb"))

        if not seg_hits and not thumb_info:
            continue

        # 归属自检：坏名里内嵌的帖子 id 必须包含本 post 的 id
        for (old, new), _ in seg_hits:
            pids = embedded_pids(old)
            if pids and post["id"].split("_", 1)[1] not in pids:
                mismatch_pid.append((post["id"], old))
        # 目标存在性自检
        if r2names:
            for (old, new), _ in seg_hits:
                if new not in r2names:
                    target_missing.append((post["id"], old, new))

        changed.append((f, post, seg_hits, thumb_info))

    print(f"受影响 post：{len(changed)} 个")
    print(f"待改引用（含重复计数）：{sum(all_bad.values())} 处，去重 {len(all_bad)} 个文件名")
    print(f"待改 thumb：{sum(1 for _, _, _, t in changed if t)} 个")
    print()
    print("按分类分布（待改引用数）：")
    cats = Counter()
    for f, post, hits, _ in changed:
        cats[post.get("cat", "?")] += len(hits)
    for c, n in cats.most_common(10):
        print(f"  {c:<24} {n:>5} 处")
    print()

    if mismatch_pid:
        print(f"⚠️ 归属存疑 {len(mismatch_pid)} 处（坏名里的帖子 id 与本 post 不符）：")
        for pid, old in mismatch_pid[:8]:
            print(f"    post={pid}  {old[:80]}")
        print()
    if target_missing:
        print(f"⚠️ R2 上找不到目标 {len(target_missing)} 处，改完仍会裂图：")
        for pid, old, new in target_missing[:8]:
            print(f"    {pid}: {new[:70]}")
        print()
    if not mismatch_pid and not target_missing and r2names:
        print("✓ 自检通过：归属一致，且所有目标文件在 R2 上都存在\n")

    print("样例（前 5 条改法）:")
    for f, post, hits, th in changed[:5]:
        print(f"  [{post.get('cat')}] {post['id']}")
        for (old, new), _ in hits[:2]:
            print(f"    ✗ {old[:86]}")
            print(f"    ✓ {new[:86]}")
    print()

    if not args.apply:
        print("── 预演结束，未写盘。加 --apply 实际执行。──")
        return 0

    written = 0
    for f, post, hits, th in changed:
        for seg in post.get("segments", []):
            new, info = fix_url(seg.get("i"))
            if info:
                seg["i"] = new
        if th:
            post["thumb"] = fix_url(post["thumb"])[0]
        dump(f, post)
        written += 1
    print(f"✓ 已重写 {written} 个 post 文件")
    print("下一步：python3 tools/build.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
