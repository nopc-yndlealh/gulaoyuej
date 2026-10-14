#!/usr/bin/env python3
"""
清理正文里图片已失效的 segment。

背景（实测结论）：
  历史数据里有 15 张图仍指向百度 CDN（tiebapic.baidu.com / imgsrc.baidu.com）。
  - 带 Referer 请求 → 403（百度防盗链）
  - 不带 Referer 请求 → 200，但内容是**占位图**：
    两个完全不同的 URL 返回同一个 4262 字节文件（md5 相同），
    说明原图已经不存在了，无法找回。
  所以这些 segment 只能删掉，不能靠改 referrer 策略救回来。
  （HTTP 200 不等于图片有效 —— 这个坑值得记一笔。）

用法：
  python3 tools/fix_broken_images.py            # 预演，只报告
  python3 tools/fix_broken_images.py --apply    # 实际写入
  python3 tools/fix_broken_images.py --apply --allow-empty   # 允许删到没图
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "content" / "posts"
R2_PREFIX = "https://img.feijibei.top/"


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
    ap.add_argument("--allow-empty", action="store_true",
                    help="允许把只剩坏图的条目删成无图（默认跳过并提示）")
    args = ap.parse_args()

    affected, skipped, removed_total, empty_after = [], [], 0, []

    for f in sorted(POSTS.glob("*.json")):
        post = load(f)
        segs = post.get("segments", [])
        bad = [s for s in segs if s.get("i") and not s["i"].startswith(R2_PREFIX)]
        if not bad:
            continue

        keep = [s for s in segs if s not in bad]
        has_img = any(s.get("i") for s in keep)
        affected.append((f, post, bad, keep, has_img))

        if not has_img and not args.allow_empty:
            skipped.append(post["id"])

    print(f"扫描 {POSTS}")
    print(f"受影响条目：{len(affected)}   坏图：{sum(len(b) for _, _, b, _, _ in affected)}\n")

    for f, post, bad, keep, has_img in affected:
        pid = post["id"]
        n_img = sum(1 for s in post.get("segments", []) if s.get("i"))
        flag = "" if has_img or args.allow_empty else "   ⚠️ 删后会没图，默认跳过"
        print(f"  {pid:<26} [{post.get('cat','?'):<10}] "
              f"坏图 {len(bad)}/{n_img}  {post.get('title','')[:26]}{flag}")
        for s in bad:
            print(f"       ✗ {s['i'][:78]}")

    print()
    if skipped:
        print(f"跳过 {len(skipped)} 条（删掉坏图后将没有任何图片）：{skipped}")
        print("如确实要删，加 --allow-empty\n")

    if not args.apply:
        print("── 预演结束，未写盘。加 --apply 实际执行。──")
        return 0

    written = 0
    for f, post, bad, keep, has_img in affected:
        if not has_img and not args.allow_empty:
            continue
        post["segments"] = keep
        dump(f, post)
        removed_total += len(bad)
        written += 1
        if not keep:
            empty_after.append(post["id"])

    print(f"✓ 已重写 {written} 个 post 文件，删除 {removed_total} 个坏图 segment")
    if empty_after:
        print(f"⚠️ 以下条目现在没有任何 segment（会在构建报告里被列为「没有正文」）：{empty_after}")
    print("\n下一步：python3 tools/build.py 重新生成 data/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
