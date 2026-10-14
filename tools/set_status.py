#!/usr/bin/env python3
"""
批量改条目状态：下架（draft）/ 上架（published）。

为什么要显式区分 status：
  旧的线上数据里，「读者看不到」有两种成因混在一起 ——
  一是有意隐藏，二是数据掉了没人发现。两者外观一样、都没报错。
  现在 status 把「有意下架」变成显式状态：条目从列表与搜索里消失，
  但数据仍完整保留在 content/posts/ 里，随时可以改回来。

用法：
  python3 tools/set_status.py --draft xhs_6651687d000000000f00d342 weibo_5206631524860471
  python3 tools/set_status.py --draft --from-report      # 下架报告里「没有正文」的全部条目
  python3 tools/set_status.py --publish rose_0001
  （加 --apply 才写盘，默认只预演）
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "content" / "posts"
REPORT = ROOT / "content" / "_build_report.md"


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def dump(p, obj):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def ids_from_report():
    """从体检报告里取出「没有正文」那一节的全部 id。"""
    if not REPORT.exists():
        print(f"✗ 找不到 {REPORT}，先跑 python3 tools/build.py", file=sys.stderr)
        return []
    text = REPORT.read_text(encoding="utf-8")
    section = text.split("## 图片不在 R2", 1)[0]
    return sorted(set(re.findall(r"\| `([a-z]+_[0-9a-zA-Z]+)` \|", section)))


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--draft", action="store_true", help="下架（status=draft）")
    g.add_argument("--publish", action="store_true", help="上架（status=published）")
    ap.add_argument("ids", nargs="*", help="要处理的 id")
    ap.add_argument("--from-report", action="store_true",
                    help="从 content/_build_report.md 里取「没有正文」的条目")
    ap.add_argument("--apply", action="store_true", help="实际写入（默认只预演）")
    args = ap.parse_args()

    ids = list(args.ids)
    if args.from_report:
        ids = sorted(set(ids) | set(ids_from_report()))
    if not ids:
        print("✗ 没有指定任何 id", file=sys.stderr)
        return 1

    target = "draft" if args.draft else "published"
    changed, missing, already = [], [], []

    for pid in ids:
        f = POSTS / f"{pid}.json"
        if not f.exists():
            missing.append(pid)
            continue
        post = load(f)
        cur = post.get("status", "published")
        if cur == target:
            already.append(pid)
            continue
        changed.append((f, post, cur))

    print(f"目标状态：{target}   待修改 {len(changed)}   已是该状态 {len(already)}   找不到 {len(missing)}\n")
    for f, post, cur in changed[:60]:
        print(f"  {post['id']:<30} {cur} → {target}   [{post.get('cat','?')}] {post.get('title','')[:30]}")
    if len(changed) > 60:
        print(f"  … 另有 {len(changed) - 60} 条")
    if already:
        print(f"\n已是 {target}（跳过）：{already[:10]}{' …' if len(already) > 10 else ''}")
    if missing:
        print(f"\n✗ 找不到这些 id：{missing}")

    if not changed:
        print("\n没有需要改的。")
        return 0

    if not args.apply:
        print("\n── 预演结束，未写盘。加 --apply 实际执行。──")
        print("改完记得跑：python3 tools/build.py")
        return 0

    for f, post, _ in changed:
        post["status"] = target
        dump(f, post)
    print(f"\n✓ 已修改 {len(changed)} 个条目")
    print("下一步：python3 tools/build.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
