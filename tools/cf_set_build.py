#!/usr/bin/env python3
"""把 Cloudflare Pages 项目切成「构建时生成」模式。

改动内容：
  build_config.build_command  = python3 tools/build.py --dist dist --stamp
  build_config.destination_dir = dist
  deployment_configs.*.env_vars.SKIP_DEPENDENCY_INSTALL = 1
    （本项目没有 JS 构建步骤，跳过 CF 自动跑的 npm install，省时间也少一个失败点）

其他字段全部原样带回，避免 PATCH 误伤（compatibility_date / build_image_major_version 等）。

用法：
  python tools/cf_set_build.py --dry-run    # 只打印将要提交的 body
  python tools/cf_set_build.py --apply
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ACCOUNT = "082a519e512f02161ab5a57b376d3f4c"
PROJECT = "gulaoyuej"
API = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/pages/projects/{PROJECT}"

BUILD_COMMAND = "python3 tools/build.py --dist dist --stamp"
DESTINATION = "dist"

# wrangler 的 OAuth token 存放位置（Windows）
CFG_CANDIDATES = [
    Path(os.environ.get("APPDATA", "")) / "xdg.config" / ".wrangler" / "config" / "default.toml",
    Path.home() / ".config" / ".wrangler" / "config" / "default.toml",
    Path.home() / ".wrangler" / "config" / "default.toml",
]


def token():
    for p in CFG_CANDIDATES:
        if p.exists():
            m = re.search(r'oauth_token\s*=\s*"([^"]+)"', p.read_text(encoding="utf-8"))
            if m:
                return m.group(1)
    print("✗ 找不到 wrangler OAuth token，先跑 wrangler login", file=sys.stderr)
    sys.exit(1)


def call(method, url, body=None, tok=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {tok}")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tok = token()
    cur = call("GET", API, tok=tok)
    if not cur.get("success"):
        print("✗ 读取项目失败:", json.dumps(cur.get("errors"), ensure_ascii=False))
        return 1
    p = cur["result"]

    bc = dict(p.get("build_config") or {})
    print("=== 改动前 ===")
    print(f"  build_command   : {bc.get('build_command')!r}")
    print(f"  destination_dir : {bc.get('destination_dir')!r}")

    bc["build_command"] = BUILD_COMMAND
    bc["destination_dir"] = DESTINATION

    dc = json.loads(json.dumps(p.get("deployment_configs") or {}))
    for env in ("production", "preview"):
        e = dc.setdefault(env, {})
        # 注意 env_vars 可能是 None（不是 {}），setdefault 不会帮你兜住
        ev = e.get("env_vars") or {}
        ev["SKIP_DEPENDENCY_INSTALL"] = {"value": "1"}
        e["env_vars"] = ev

    body = {"build_config": bc, "deployment_configs": dc}

    print("\n=== 改动后 ===")
    print(f"  build_command   : {bc['build_command']!r}")
    print(f"  destination_dir : {bc['destination_dir']!r}")
    print("  env_vars        : SKIP_DEPENDENCY_INSTALL=1 (production + preview)")
    print("  其他字段        : 原样保留（compatibility_date / build_image_major_version 等）")

    if args.dry_run or not args.apply:
        print("\n--- 将要提交的 body ---")
        print(json.dumps(body, ensure_ascii=False, indent=2)[:2000])
        print("\n（加 --apply 实际提交）")
        return 0

    res = call("PATCH", API, body=body, tok=tok)
    if not res.get("success"):
        print("\n✗ 提交失败:", json.dumps(res.get("errors"), ensure_ascii=False))
        return 1
    r = res["result"]
    rbc = r.get("build_config") or {}
    print("\n✓ 修改成功")
    print(f"  build_command   : {rbc.get('build_command')!r}")
    print(f"  destination_dir : {rbc.get('destination_dir')!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
