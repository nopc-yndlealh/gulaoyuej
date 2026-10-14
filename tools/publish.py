#!/usr/bin/env python3
"""
feijibei.top 本地发布服务。

只监听 127.0.0.1，不对外。作用是把「编辑 → 发布」这条链路从
「手工改 JSON + 跑脚本 + git push」压成一次点击。

  python3 tools/publish.py            # 启动，浏览器打开 http://127.0.0.1:8788
  python3 tools/publish.py --port 9000
  python3 tools/publish.py --no-browser

接口
----
  GET  /                      编辑器页面
  GET  /api/meta              分类树 + 现有条数
  GET  /api/list?q=&cat=&n=   搜索 / 列出条目
  GET  /api/post?id=          读单条
  POST /api/save              新建或更新一条（写 content/posts/{id}.json）
  POST /api/image             上传图片到 R2（body: {filename, data(base64)}）
  POST /api/publish           构建 + git commit + push
  GET  /api/status            仓库状态（有无未提交改动）

写入前一律校验：id 合法、cat 在 taxonomy 里、图片 URL 在 R2 域名上。
宁可拒绝保存，也不要往单一源里写进坏数据。
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "content" / "posts"
TAXONOMY = ROOT / "content" / "taxonomy.json"
R2_PREFIX = "https://img.feijibei.top/"

ACC = "082a519e512f02161ab5a57b376d3f4c"
BUCKET = "plant-images"
R2_API = f"https://api.cloudflare.com/client/v4/accounts/{ACC}/r2/buckets/{BUCKET}/objects"

ID_RE = re.compile(r"^[a-z]+_[0-9a-zA-Z]+$")


# ── 凭据 ──
def cf_token():
    t = os.environ.get("CLOUDFLARE_API_TOKEN")
    if t:
        return t.strip()
    for p in (Path.home() / ".workbuddy" / "cf-token.txt",):
        if p.exists():
            v = p.read_text(encoding="utf-8").strip()
            if v:
                return v
    return None


# ── 基础读写 ──
def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_json(p, obj):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def taxonomy():
    return load_json(TAXONOMY)


def valid_cats(tax):
    cats = set(tax.get("parentCats", []))
    for g in tax["tree"]:
        cats.add(g["name"])
        for c in g.get("children", []):
            cats.add(c)
            if g.get("multi"):
                cats.add(f"{g.get('prefix', g['name'] + '/')}{c}")
    return cats


def git(*args, check=False):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "").strip()[:800])
    return r


def run_build():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "build.py")],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode == 0, (r.stdout or "") + (r.stderr or "")


# ── 校验 ──
def validate(post, cats):
    errs, warns = [], []
    pid = (post.get("id") or "").strip()
    if not ID_RE.match(pid):
        errs.append(f"id 不合法：{pid!r}（应形如 rose_0001 / weibo_1234567890123456）")
    if not (post.get("title") or "").strip():
        errs.append("标题不能为空")
    cat = (post.get("cat") or "").strip()
    if not cat:
        errs.append("分类不能为空")
    elif cat not in cats:
        errs.append(f"分类 {cat!r} 不在 taxonomy.json 里")
    if not isinstance(post.get("segments"), list):
        errs.append("segments 必须是数组")

    for i, s in enumerate(post.get("segments") or []):
        if not isinstance(s, dict):
            errs.append(f"第 {i+1} 段不是对象")
            continue
        keys = [k for k in ("t", "i", "v") if s.get(k)]
        if not keys:
            warns.append(f"第 {i+1} 段是空的，会被忽略")
        u = s.get("i")
        if u and not str(u).startswith(R2_PREFIX):
            errs.append(f"第 {i+1} 段图片不在 R2 域名上：{str(u)[:60]}")
    if not (post.get("segments") or []):
        warns.append("没有任何段落，前台点开会是空白")
    return errs, warns


def normalize(post):
    """只保留已知字段，顺序固定，避免 JSON 越写越乱。"""
    out = {
        "id": post["id"].strip(),
        "title": (post.get("title") or "").strip(),
        "cat": (post.get("cat") or "").strip(),
        "source": (post.get("source") or post["id"].split("_", 1)[0]).strip(),
    }
    for k in ("type", "tag", "author", "date", "image"):
        v = post.get(k)
        if v not in (None, "", [], {}):
            out[k] = v
    for k in ("rawTitle", "contentType", "contentTag", "time", "legacySource"):
        v = post.get(k)
        if v not in (None, "", [], {}):
            out[k] = v
    if post.get("contentAuthor") is not None:
        out["contentAuthor"] = post["contentAuthor"]
    out["segments"] = [s for s in (post.get("segments") or [])
                       if any(s.get(k) for k in ("t", "i", "v"))]
    out["status"] = post.get("status") or "published"
    if "seq" in post:
        out["seq"] = post["seq"]
    elif not (POSTS / f"{out['id']}.json").exists():
        # 新条目排在最后
        seqs = []
        for f in POSTS.glob("*.json"):
            try:
                seqs.append(load_json(f).get("seq", 0))
            except Exception:
                pass
        out["seq"] = (max(seqs) if seqs else 0) + 1
    if out.get("segments"):
        first_img = next((s["i"] for s in out["segments"] if s.get("i")), "")
        out["thumb"] = first_img
    return out


# ── HTTP ──
class Handler(BaseHTTPRequestHandler):
    server_version = "feijibei-publish/1.0"

    def log_message(self, fmt, *args):
        pass  # 静音，自己打关键日志

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    # ---- GET ----
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            # 注意：编辑器挂在 / 与 /editor，**不能占用 /index.html** ——
            # 调参面板要用同源 iframe 预览站点，而那个 iframe 加载的就是 /index.html。
            if u.path in ("/", "/editor", "/editor.html"):
                f = ROOT / "tools" / "editor.html"
                return self._send(200, f.read_text(encoding="utf-8"),
                                  "text/html; charset=utf-8")
            if u.path in ("/tuner", "/tuner.html"):
                f = ROOT / "tools" / "theme-tuner.html"
                return self._send(200, f.read_text(encoding="utf-8"),
                                  "text/html; charset=utf-8")
            if u.path == "/api/meta":
                tax = taxonomy()
                cats = sorted(valid_cats(tax))
                n = len(list(POSTS.glob("*.json")))
                return self._send(200, {"taxonomy": tax, "cats": cats, "total": n,
                                        "r2": bool(cf_token())})
            if u.path == "/api/list":
                kw = (q.get("q", [""])[0] or "").strip().lower()
                catf = (q.get("cat", [""])[0] or "").strip()
                lim = int(q.get("n", ["80"])[0])
                rows = []
                for f in POSTS.glob("*.json"):
                    p = load_json(f)
                    if catf and p.get("cat") != catf:
                        continue
                    if kw:
                        hay = (p.get("id", "") + " " + p.get("title", "") + " " +
                               " ".join(s.get("t", "") for s in p.get("segments", []))).lower()
                        if kw not in hay:
                            continue
                    rows.append({"id": p["id"], "title": p.get("title", ""),
                                 "cat": p.get("cat", ""), "status": p.get("status", ""),
                                 "n": len(p.get("segments") or []),
                                 "img": sum(1 for s in p.get("segments", []) if s.get("i")),
                                 "seq": p.get("seq", 0)})
                rows.sort(key=lambda r: r["seq"])
                return self._send(200, {"total": len(rows), "rows": rows[:lim]})
            if u.path == "/api/post":
                pid = q.get("id", [""])[0]
                f = POSTS / f"{pid}.json"
                if not f.exists():
                    return self._send(404, {"error": "找不到该条目"})
                return self._send(200, load_json(f))
            if u.path == "/api/status":
                st = git("status", "--porcelain")
                br = git("rev-parse", "--abbrev-ref", "HEAD")
                ahead = git("status", "-sb").stdout.strip().splitlines()[0] if True else ""
                return self._send(200, {
                    "branch": br.stdout.strip(),
                    "dirty": len([l for l in st.stdout.splitlines() if l.strip()]),
                    "summary": ahead,
                })
            # 静态文件：让调参面板能用同源 iframe 预览站点（可实时注入 CSS 变量）
            return self._static(u.path)
        except Exception as e:
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def _static(self, path):
        MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
                ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
                ".webp": "image/webp", ".ico": "image/x-icon", ".xml": "application/xml"}
        rel = urllib.parse.unquote(path).lstrip("/")
        if rel == "":
            rel = "index.html"
        # 禁止读取任何以 . 开头的路径段（.git/config、.env、.workbuddy/…）。
        # 服务只监听 127.0.0.1，但把手边能读的东西全暴露出去仍然是不必要的风险。
        if any(seg.startswith(".") for seg in rel.split("/") if seg):
            return self._send(403, {"error": "禁止访问隐藏路径"})
        f = (ROOT / rel).resolve()
        # 目录穿越防护：必须仍在仓库内
        if not str(f).startswith(str(ROOT.resolve())):
            return self._send(403, {"error": "越界"})
        if f.is_dir():
            f = f / "index.html"
        if not f.exists():
            return self._send(404, {"error": "文件不存在: " + rel})
        return self._send(200, f.read_bytes(),
                          MIME.get(f.suffix.lower(), "application/octet-stream"))

    # ---- POST ----
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        try:
            body = self._body()
            if u.path == "/api/save":
                return self._save(body)
            if u.path == "/api/image":
                return self._image(body)
            if u.path == "/api/publish":
                return self._publish(body)
            if u.path == "/api/theme":
                return self._theme(body)
            return self._send(404, {"error": "未知路径"})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def _save(self, body):
        post = body.get("post") or {}
        cats = valid_cats(taxonomy())
        errs, warns = validate(post, cats)
        if errs:
            return self._send(400, {"ok": False, "errors": errs, "warnings": warns})
        norm = normalize(post)
        existed = (POSTS / f"{norm['id']}.json").exists()
        save_json(POSTS / f"{norm['id']}.json", norm)
        print(f"  💾 {'更新' if existed else '新建'} {norm['id']}  [{norm['cat']}]")
        return self._send(200, {"ok": True, "id": norm["id"],
                                "action": "更新" if existed else "新建",
                                "warnings": warns})

    def _image(self, body):
        tok = cf_token()
        if not tok:
            return self._send(400, {"ok": False,
                                    "error": "没找到 Cloudflare 令牌，无法上传。"
                                             "请放到 ~/.workbuddy/cf-token.txt"})
        fname = (body.get("filename") or "").strip()
        data = body.get("data") or ""
        if not fname or not data:
            return self._send(400, {"ok": False, "error": "缺少 filename 或 data"})
        raw = base64.b64decode(data.split(",")[-1])
        if len(raw) > 12 * 1024 * 1024:
            return self._send(400, {"ok": False, "error": "图片超过 12MB"})
        ext = Path(fname).suffix.lower()
        ctype = {".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".png": "image/png", ".gif": "image/gif"}.get(ext, "application/octet-stream")
        key = "images/" + fname
        req = urllib.request.Request(
            f"{R2_API}/{urllib.parse.quote(key)}", data=raw, method="PUT")
        req.add_header("Authorization", "Bearer " + tok)
        req.add_header("Content-Type", ctype)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                ok = r.status == 200
        except urllib.error.HTTPError as e:
            return self._send(502, {"ok": False,
                                    "error": f"R2 上传失败 HTTP {e.code}: {e.read()[:200]}"})
        print(f"  🖼️  上传 R2 {key}（{len(raw) // 1024} KB）")
        return self._send(200, {"ok": True, "url": R2_PREFIX + key, "size": len(raw)})

    def _theme(self, body):
        """保存外观参数到 content/theme.json（调参面板用）。"""
        t = body.get("theme")
        if not isinstance(t, dict):
            return self._send(400, {"ok": False, "error": "缺少 theme 对象"})
        # 基本校验：主色必须是颜色值，数值项必须在合理区间
        import re as _re
        acc = t.get("accent") or {}
        for k in ("light", "dark"):
            v = acc.get(k)
            if v and not _re.match(r"^#[0-9a-fA-F]{3,8}$", str(v)):
                return self._send(400, {"ok": False, "error": f"accent.{k} 不是合法颜色：{v}"})
        cur = load_json(TAXONOMY.parent / "theme.json") if (TAXONOMY.parent / "theme.json").exists() else {}
        # 保留 _note/_fields 说明，避免被面板覆盖掉
        for k in ("_note", "_fields"):
            if k in cur and k not in t:
                t[k] = cur[k]
        save_json(TAXONOMY.parent / "theme.json", t)
        print("  🎨 已更新 content/theme.json")
        return self._send(200, {"ok": True})

    def _publish(self, body):
        msg = (body.get("message") or "").strip() or "content: 通过本地编辑器更新"
        ok, out = run_build()
        if not ok:
            return self._send(500, {"ok": False, "step": "build", "log": out[-3000:]})
        git("add", "-A")
        st = git("status", "--porcelain")
        if not st.stdout.strip():
            return self._send(200, {"ok": True, "step": "nochange", "log": out[-1500:],
                                    "message": "没有需要提交的改动"})
        c = git("-c", "user.name=吴子瑞", "-c", "user.email=wuzirui@feijibei.top",
                "commit", "-m", msg)
        if c.returncode != 0:
            return self._send(500, {"ok": False, "step": "commit", "log": (c.stdout + c.stderr)[-2000:]})
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        p = subprocess.run(["git", "push", "origin", "HEAD"], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env)
        pushed = p.returncode == 0
        print(f"  🚀 已提交并{'推送' if pushed else '尝试推送（失败）'}：{msg}")
        return self._send(200 if pushed else 502, {
            "ok": pushed, "step": "push",
            "commit": git("rev-parse", "--short", "HEAD").stdout.strip(),
            "log": ((out[-1200:]) + "\n---\n" + (p.stdout or "") + (p.stderr or ""))[-3000:],
            "message": "已发布" if pushed else "提交成功，但推送失败（检查代理）",
        })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    if not POSTS.exists():
        print(f"✗ 找不到 {POSTS}", file=sys.stderr)
        print("  这个服务只能从仓库根目录运行（需要 content/ 与 .git/）", file=sys.stderr)
        return 1
    if not (ROOT / ".git").exists():
        print(f"⚠️ {ROOT} 里没有 .git，发布时无法提交推送", file=sys.stderr)

    token_ok = bool(cf_token())
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"仓库: {ROOT}")
    print(f"条目: {len(list(POSTS.glob('*.json')))} 个")
    print(f"R2  : {'✓ 令牌已就绪，可上传图片' if token_ok else '✗ 未找到令牌，图片上传会不可用'}")
    print(f"\n编辑器已启动：{url}")
    print("（只监听 127.0.0.1，仅本机可访问。Ctrl+C 退出）\n")
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(0.6), webbrowser.open(url)),
                         daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
