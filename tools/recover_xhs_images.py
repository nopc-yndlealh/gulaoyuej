#!/usr/bin/env python3
"""
把「从小红书抓下来的图片」转码、上传 R2、写回内容源。

配合外部的 Node 抓取脚本使用（需要 Playwright，跑在本机 WorkBuddy 的 node 工作区）：
  抓取脚本把图片按 `xhs_<note_id>_<序号>.img` 落到一个临时目录，
  本脚本再负责后半段：转 WebP → 上传 R2 → 写 content/posts/xhs_*.json。

为什么抓取要单独用浏览器：
  小红书的图片 CDN（sns-webpic-qc.xhscdn.com）URL 路径里带**时间签名**，
  历史 URL 会 403；笔记页又必须要新鲜的 `xsec_token`。
  只有真实浏览器加载页面后，才能拿到当场生成的签名 URL。
  （登录态用 Chrome profile 里的 cookie，见 notes/记忆里的说明）

命名：沿用 R2 既有规范 images/xhs_<note_id>_<序号>.webp

用法：
  python3 tools/recover_xhs_images.py --dir <下载目录>            # 预演
  python3 tools/recover_xhs_images.py --dir <下载目录> --apply
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import netfix  # noqa: F401,E402  —— 网络环境修复（IPv6 死路 / 沙箱伪代理），发请求前必须导入

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "content" / "posts"

ACC = "082a519e512f02161ab5a57b376d3f4c"
BUCKET = "plant-images"
R2_API = f"https://api.cloudflare.com/client/v4/accounts/{ACC}/r2/buckets/{BUCKET}/objects"
R2_PREFIX = "https://img.feijibei.top/"
MAX_EDGE = 1600
QUALITY = 82

NAME_RE = re.compile(r"^xhs_([0-9a-f]{24})_(\d+)\.(img|webp|jpg|png)$")


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


def is_webp(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            h = f.read(12)
        return h[:4] == b"RIFF" and h[8:12] == b"WEBP"
    except OSError:
        return False


def to_webp(src: Path, dest: Path):
    """转成 WebP。

    源本身已是 WebP 时直接复制，不做二次编码 ——
    小红书 CDN 返回的就是 WebP，重新编码只会白掉一次画质。
    """
    if is_webp(src):
        dest.write_bytes(src.read_bytes())
        return dest.stat().st_size
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
           "-vf", f"scale='min({MAX_EDGE},iw)':-2",
           "-c:v", "libwebp", "-quality", str(QUALITY), str(dest)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not dest.exists():
        raise RuntimeError((r.stderr or "ffmpeg 失败")[:160])
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
            pass
    return False


def r2_put(name, data, tok, tries=3):
    """上传。必须捕获所有异常 —— 网络抖动抛的 TimeoutError 会打死整个任务。"""
    url = f"{R2_API}/{urllib.parse.quote('images/' + name)}"
    for i in range(tries):
        req = urllib.request.Request(url, data=data, method="PUT")
        req.add_header("Authorization", "Bearer " + tok)
        req.add_header("Content-Type", "image/webp")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status == 200
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                print(f"      ✗ R2 拒绝 HTTP {e.code}", file=sys.stderr)
                return False
        except Exception as e:
            if i == tries - 1:
                print(f"      ✗ R2 上传失败 {type(e).__name__}", file=sys.stderr)
                return False
        import time
        time.sleep(1.5 * (i + 1))
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="图片下载目录")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    dl = Path(args.dir)
    if not dl.is_dir():
        print(f"✗ 目录不存在: {dl}", file=sys.stderr)
        return 1

    files = [f for f in dl.iterdir() if NAME_RE.match(f.name)]
    by_note = {}
    for f in files:
        m = NAME_RE.match(f.name)
        by_note.setdefault(m.group(1), []).append((int(m.group(2)), f))
    for k in by_note:
        by_note[k].sort()

    print(f"下载目录: {dl}")
    print(f"涉及笔记: {len(by_note)} 条，图片 {len(files)} 张\n")

    tok = r2_token() if args.apply else None
    wp = dl / "_webp"
    if args.apply:
        wp.mkdir(exist_ok=True)

    stat = {"已补条目": 0, "图片": 0, "跳过已存在": 0, "失败": 0}
    for nid, items in sorted(by_note.items()):
        post_file = POSTS / f"xhs_{nid}.json"
        if not post_file.exists():
            print(f"  ? xhs_{nid}  找不到对应的 post 文件，跳过")
            continue
        post = json.loads(post_file.read_text(encoding="utf-8"))
        if any(s.get("i") for s in post.get("segments", [])):
            print(f"  · xhs_{nid}  已有图，跳过")
            continue

        added = []
        for idx, src in items:
            name = f"xhs_{nid}_{idx}.webp"
            if args.apply and r2_exists(name, tok):
                stat["跳过已存在"] += 1
                added.append(R2_PREFIX + "images/" + name)
                continue
            if not args.apply:
                added.append(R2_PREFIX + "images/" + name)
                continue
            try:
                size = to_webp(src, wp / name)
            except Exception as e:
                stat["失败"] += 1
                print(f"      ✗ {name} 转码失败: {e}")
                continue
            if not r2_put(name, (wp / name).read_bytes(), tok):
                stat["失败"] += 1
                continue
            # ⚠️ 这里刻意不删临时文件：每张删一次，跑到 50 个左右会撞上沙箱的
            # 批量删除保护（SAFE_DELETE_BULK_CONFIRM_REQUIRED），整个进程被打断。
            # 这个坑在微博脚本里踩过一次，这里又踩了一次 —— 临时目录留在
            # 下载目录的 _webp/ 下，跑完手动清即可，不影响正确性。
            added.append(R2_PREFIX + "images/" + name)
            stat["图片"] += 1

        if args.apply and added:
            post["segments"] = [{"i": u} for u in added] + list(post.get("segments", []))
            post["thumb"] = added[0]
            post_file.write_text(json.dumps(post, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
            stat["已补条目"] += 1
            print(f"  ✓ xhs_{nid}  补 {len(added)} 张")
        elif not args.apply:
            print(f"  [预演] xhs_{nid}  将补 {len(added)} 张")

    print("\n=== 统计 ===")
    for k, v in stat.items():
        print(f"  {k}: {v}")
    if not args.apply:
        print("\n── 预演结束，未上传也未改数据。加 --apply 实际执行。──")
    else:
        print("\n下一步：python3 tools/build.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
