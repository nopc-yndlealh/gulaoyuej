#!/usr/bin/env python3
"""
构建 feijibei.top 数据管道：content/ 单一源 → data/ 四份派生产物。

设计要点
--------
1. **单向派生**：只读 content/，只写 data/。不再"读旧产物改一改"，
   因此不可能再出现两套口径各自漂移（旧 build_all.py 就是这么漂移的）。
2. **幂等**：同输入必同输出。可随时全量重建，结果一致。
3. **零外部依赖**：不需要 images/、CSV 映射表、也不需要的本地 output 目录
   （旧 build_all.py 依赖 ~/WorkBuddy/2026-05-27-17-57-56/output/ 等已失效路径）。
4. **自校验**：--verify 会把新产物与现有 data/ 逐条比对，报告差异。

产物
----
  data/index.json                     卡片列表（按 seq 排序，仅 published）
  data/content/content-index.json      id → 分块 slug
  data/content/{slug}.json            正文分块（按 cat 分块）
  data/search-index.json              {id:{title,cat,text,thumb}}，text 截断 500 字
  data/nav-links.json / authors.json / featured.json   直接转存

用法
----
  python3 tools/build.py              # 生成到 data/
  python3 tools/build.py --verify     # 生成到临时目录并与现有 data/ 比对
  python3 tools/build.py --stamp      # 额外刷新 index.html / sw.js 的构建号
"""
import argparse
import collections
import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "content"
POSTS = SRC / "posts"
DATA = ROOT / "data"

# 由 app.js 的 SPECIAL_PAGES 接管，不需要正文条目，也不该进 content-index
SPECIAL_PAGES = {"sedum_evo_2019"}

# index.json 里可选字段的输出顺序（与原文件保持一致）
OPTIONAL_INDEX_FIELDS = ("date", "tag", "author", "image")

SEARCH_TEXT_LIMIT = 500


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump(path, obj, indent=2, compact=False):
    """写 JSON。compact=True 用于首屏载荷（index.json），省掉缩进空白。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=indent)
        f.write("\n")


def slug_of(cat):
    """分类名 → 分块文件名。与历史规则一致：'/'→'-'，去空格。"""
    return cat.replace("/", "-").replace(" ", "")


def segments_text(segments):
    """把正文里的文字段落拼成检索文本。

    与历史行为一致：直接相连、不加分隔符（原文如此，例如
    「…令人想起粉彩画：」+「丹陶2004年德国推出…」 拼成一句）。
    """
    return "".join(s["t"] for s in segments if isinstance(s.get("t"), str))


def valid_cats(taxonomy):
    cats = set(taxonomy.get("parentCats", []))
    for g in taxonomy["tree"]:
        cats.add(g["name"])
        for c in g["children"]:
            cats.add(c)
            if g.get("multi"):
                cats.add(f"{g.get('prefix', g['name'] + '/')}{c}")
    return cats


def load_posts():
    posts = []
    for f in sorted(POSTS.glob("*.json")):
        posts.append(load(f))
    return posts


def validate(posts, taxonomy, errors, warnings):
    """构建前的硬校验：宁可构建失败，也不要产出坏数据。"""
    cats = valid_cats(taxonomy)
    seen = set()
    for p in posts:
        pid = p.get("id")
        if not pid:
            errors.append(f"{pid or '(无 id)'}: 缺少 id")
            continue
        if pid in seen:
            errors.append(f"{pid}: id 重复")
        seen.add(pid)

        if p.get("status") == "published":
            cat = p.get("cat")
            if not cat:
                errors.append(f"{pid}: status=published 但 cat 为空")
            elif cat not in cats:
                errors.append(f"{pid}: cat='{cat}' 不在 taxonomy.json 里")

            if not p.get("segments"):
                warnings.append(f"{pid}: published 但没有任何正文段落，点开会是空白")

            for s in p.get("segments", []):
                url = s.get("i")
                if url and not url.startswith("https://img.feijibei.top/"):
                    warnings.append(f"{pid}: 图片不在 R2 域名上 → {url[:70]}")
                if not (s.get("t") or s.get("i") or s.get("v")):
                    warnings.append(f"{pid}: 存在空 segment {s}")


def generate(posts, taxonomy, out):
    """生成四份产物，返回统计信息。

    会先清掉上一次生成的产物文件，避免「换分类后旧分块文件残留」——
    例如贴吧 637 条重分类到 云小123456 后，data/content/贴吧.json 必须消失，
    否则它会被部署上线，成为永远不会被读取的僵尸文件。
    """
    content_dir = out / "content"
    stale = 0
    if content_dir.exists():
        for f in content_dir.glob("*.json"):
            f.unlink()
            stale += 1
    for name in ("index.json", "search-index.json"):
        p = out / name
        if p.exists():
            p.unlink()

    published = sorted(
        (p for p in posts if p.get("status") == "published"),
        key=lambda p: p.get("seq", 10 ** 9),
    )

    slugs = {}
    chunks = collections.defaultdict(dict)
    search = collections.OrderedDict()

    for p in published:
        pid = p["id"]
        cat = p["cat"]

        # ── content-index + 分块 ──
        if pid not in SPECIAL_PAGES:
            slug = slug_of(cat)
            slugs[pid] = slug
            # 严格还原原始正文字段的形状，不多写也不少写：
            # 原 content 里 title/segments 恒有；type/author/time/tag/source
            # 只在原本存在时才输出（原状：type 2998、author 2750、time 2750、
            # tag 2750、source 248 条有值）。
            ce = {
                "title": p.get("rawTitle") or p.get("title", ""),
                "segments": p.get("segments", []),
            }
            if p.get("contentType"):
                ce["type"] = p["contentType"]
            if "contentAuthor" in p:
                # 逐字保留 content 侧的原值（对象，含头像 URL）。
                # 绝不能拿 index 侧的作者字符串顶替，那会丢掉头像。
                ce["author"] = p["contentAuthor"]
            if p.get("time"):
                ce["time"] = p["time"]
            if p.get("contentTag") is not None:
                ce["tag"] = p["contentTag"]
            if p.get("legacySource"):
                ce["source"] = p["legacySource"]
            chunks[slug][pid] = ce

        # ── search-index ──
        # 关键：即使没有文字段落也要收录。否则图片型条目虽然标题可搜，
        # 却因为不在索引里而永远搜不到（app.js 完全依赖这份索引做检索）。
        text = segments_text(p.get("segments", []))[:SEARCH_TEXT_LIMIT]
        search[pid] = {
            "title": p.get("title", ""),
            "cat": cat,
            "text": text,
            "thumb": p.get("thumb", ""),
        }

    # index.json 走紧凑格式（无缩进）—— 它是首屏必下载荷，
    # 原文件也是紧凑的（`{"id":"rose_0001",...}`）；用 indent=2 会凭空
    # 多出约 190KB（792KB → 981KB）拖慢首屏。
    # 其余产物（search-index / 分块 / 配置）保持 indent=2：它们是懒加载
    # 或需要人肉查看的，可读性比几 KB 更重要。
    dump(out / "index.json", [_index_entry(p) for p in published], compact=True)
    dump(out / "content" / "content-index.json", slugs)
    for slug, items in sorted(chunks.items()):
        dump(out / "content" / f"{slug}.json", items)
    dump(out / "search-index.json", search)

    # 分类树也要下发：app.js 从 ./data/taxonomy.json 读取，不再把
    # TREE_CONFIG 硬编码在前端（硬编码导致「微博」899 条不可达）
    dump(out / "taxonomy.json", taxonomy)

    for name in ("nav-links.json", "authors.json", "featured.json"):
        if (SRC / name).exists():
            dump(out / name, load(SRC / name))

    return {
        "published": len(published),
        "drafts": len(posts) - len(published),
        "blocks": len(chunks),
        "search": len(search),
        "cleaned": stale,
    }


def _index_entry(p):
    entry = {"id": p["id"], "title": p.get("title", ""), "cat": p["cat"]}
    if p.get("type"):
        entry["type"] = p["type"]
    entry["thumb"] = p.get("thumb", "")
    for k in OPTIONAL_INDEX_FIELDS:
        v = p.get(k)
        if k == "author":
            # index.json 的 author 必须是字符串 —— 卡片按字符串渲染。
            # 源里可能是 {name, avatar, url} 对象，取 name；
            # 对象本身已由 contentAuthor 逐字保留在正文里，不会丢信息。
            v = v.get("name", "") if isinstance(v, dict) else v
        if v not in (None, "", []):
            entry[k] = v
    return entry


# ── 往返校验 ──
def verify(new_dir, old_dir):
    """把新产物与现有 data/ 逐条比对，报告真实差异。"""
    print("\n" + "=" * 64)
    print("往返校验：新产物  vs  现有 data/")
    print("=" * 64)

    def cmp_json(name, key_field=None):
        a_path, b_path = new_dir / name, old_dir / name
        if not a_path.exists() or not b_path.exists():
            print(f"\n[{name}] 缺失：new={a_path.exists()} old={b_path.exists()}")
            return
        a, b = load(a_path), load(b_path)
        if isinstance(a, list):
            ka = {e["id"]: e for e in a}
            kb = {e["id"]: e for e in b}
            same_order = [e["id"] for e in a] == [e["id"] for e in b]
        else:
            ka, kb = a, b
            same_order = list(a) == list(b)

        only_new = sorted(set(ka) - set(kb))
        only_old = sorted(set(kb) - set(ka))
        diff_fields = collections.Counter()
        diff_ids = []
        for k in sorted(set(ka) & set(kb)):
            if ka[k] != kb[k]:
                diff_ids.append(k)
                if isinstance(ka[k], dict) and isinstance(kb[k], dict):
                    for f in set(ka[k]) | set(kb[k]):
                        if ka[k].get(f) != kb[k].get(f):
                            diff_fields[f] += 1
                else:
                    diff_fields["(整体)"] += 1

        print(f"\n[{name}]")
        print(f"  条数   新 {len(ka)}  /  旧 {len(kb)}")
        print(f"  顺序一致 : {same_order}")
        print(f"  仅新有 : {len(only_new)}   {only_new[:5]}")
        print(f"  仅旧有 : {len(only_old)}  {only_old[:5]}")
        print(f"  值不同 : {len(diff_ids)}  {diff_ids[:5]}")
        if diff_fields:
            print(f"    差异字段分布: {dict(diff_fields)}")

    cmp_json("index.json")
    cmp_json("content/content-index.json")
    cmp_json("search-index.json")

    # 分块文件
    new_blocks = {p.name: p for p in (new_dir / "content").glob("*.json")
                  if p.name != "content-index.json"}
    old_blocks = {p.name: p for p in (old_dir / "content").glob("*.json")
                  if p.name != "content-index.json"}
    print(f"\n[content/ 分块]  新 {len(new_blocks)} 个 / 旧 {len(old_blocks)} 个")
    print(f"  仅新有: {sorted(set(new_blocks) - set(old_blocks))}")
    print(f"  仅旧有: {sorted(set(old_blocks) - set(new_blocks))}")

    # 正文内容：把两侧所有分块按 id 摊平后逐字段比对。
    # 只比分块文件名是不够的 —— 曾经因此漏掉「author 对象被降级成字符串、
    # 头像 URL 静默消失」这类只在字段内部发生的退化。
    def flatten(d):
        out = {}
        for p in (d / "content").glob("*.json"):
            if p.name == "content-index.json":
                continue
            out.update(load(p))
        return out

    na, oa = flatten(new_dir), flatten(old_dir)
    print(f"\n[正文内容]  新 {len(na)} 条 / 旧 {len(oa)} 条")
    field_diff = collections.Counter()
    type_diff = collections.Counter()
    for k in sorted(set(na) & set(oa)):
        a, b = na[k], oa[k]
        for f in set(a) | set(b):
            if a.get(f) != b.get(f):
                field_diff[f] += 1
                if type(a.get(f)) is not type(b.get(f)) or f not in a or f not in b:
                    type_diff[f"{f}: {type(b.get(f)).__name__} → "
                              f"{type(a.get(f)).__name__ if f in a else 'MISSING'}"] += 1
    if field_diff:
        print(f"  值不同的字段: {dict(field_diff)}")
    else:
        print("  ✓ 正文内容逐字段完全一致")
    if type_diff:
        print(f"  ⚠️ 类型/存在性变化（很可能是信息退化，必须逐个确认）: {dict(type_diff)}")
    else:
        print("  ✓ 无类型或字段存在性变化")


# ── 产物自检 ──
def check_integrity(out):
    """对生成结果做闭环校验。

    这些不变量正是线上长期存在的 bug 的形态：
      - 261 条「有正文、无 index 条目」→ 读者看不到
      - 1 条「有 index 条目、无正文」  → 点开空白
      - 3 条「content-index 指向不存在的分块」→ 点开空白
    构建后立即闭环检查，任何一条不成立都说明管道坏了。
    """
    print("\n" + "=" * 64)
    print("产物自检")
    print("=" * 64)

    index = load(out / "index.json")
    slugs = load(out / "content" / "content-index.json")
    search = load(out / "search-index.json")

    ids_index = [e["id"] for e in index]
    set_index = set(ids_index)
    problems = []       # 结构错误 → 构建失败
    data_issues = []    # 内容质量 → 只报告

    # 1. index 内 id 不重复
    dup = [i for i, n in collections.Counter(ids_index).items() if n > 1]
    if dup:
        problems.append(f"index.json 里 id 重复 {len(dup)} 个：{dup[:5]}")

    # 2. index 与 search 必须一一对应（否则要么搜不到、要么搜出来点不开）
    only_index = sorted(set_index - set(search))
    only_search = sorted(set(search) - set_index)
    if only_index:
        problems.append(f"{len(only_index)} 条在 index 里但搜不到：{only_index[:5]}")
    if only_search:
        problems.append(f"{len(only_search)} 条能搜到但列表里没有：{only_search[:5]}")

    # 3. 每条 index 都要能在 content-index 里找到归属（特例页除外）
    no_slug = sorted(set_index - set(slugs) - SPECIAL_PAGES)
    if no_slug:
        problems.append(f"{len(no_slug)} 条没有正文归属：{no_slug[:5]}")

    # 4. 每个 slug 都要有真实分块文件
    blocks = {}
    for f in (out / "content").glob("*.json"):
        if f.name == "content-index.json":
            continue
        blocks[f.stem] = load(f)
    missing_block = sorted({s for s in slugs.values()} - set(blocks))
    if missing_block:
        problems.append(f"content-index 指向不存在的分块：{missing_block}")

    # 6. 数据质量问题（只报告，不阻断构建 —— 这些需要人来决定怎么处理）
    idx_by_id = {e["id"]: e for e in index}
    empty, foreign = [], []
    for slug, items in blocks.items():
        for pid, entry in items.items():
            meta = idx_by_id.get(pid, {})
            row = {
                "id": pid,
                "cat": meta.get("cat", slug),
                "title": entry.get("title") or meta.get("title", ""),
                "thumb": meta.get("thumb", ""),
                "n_img": sum(1 for s in entry.get("segments", []) if s.get("i")),
                "n_bad": 0,
                "bad_url": "",
            }
            if not entry.get("segments"):
                empty.append(row)
            for s in entry.get("segments", []):
                u = s.get("i")
                if u and "img.feijibei.top" not in u:
                    row["n_bad"] += 1
                    if not row["bad_url"]:
                        row["bad_url"] = u
            if row["n_bad"]:
                foreign.append(row)
    if empty:
        data_issues.append(("empty", "published 但没有正文（点开会是「暂无详情内容」）", empty))
    if foreign:
        data_issues.append(("foreign", "图片不在 R2 域名上（原图已失效）", foreign))

    print(f"  index.json          {len(index)} 条")
    print(f"  content-index.json  {len(slugs)} 条映射")
    print(f"  分块文件            {len(blocks)} 个")
    print(f"  search-index.json   {len(search)} 条")
    print()

    ok = True
    if problems:
        ok = False
        print("  ✗ 结构错误（构建应视为失败）：")
        for p in problems:
            print(f"    - {p}")
    else:
        print("  ✓ 结构不变量全部通过：无 id 重复、无孤儿、无空壳、无悬空映射")

    if data_issues:
        total = sum(len(items) for _, _, items in data_issues)
        print(f"\n  ⚠️  内容质量问题 {len(data_issues)} 类 / {total} 条（不阻断构建，需人工决定）：")
        for _, label, items in data_issues:
            print(f"    - {label}：{len(items)} 条")
        _write_report(data_issues)
        print("    明细见 content/_build_report.md")

    return ok


def _write_report(data_issues):
    """把内容质量问题写成可操作的清单。

    刻意保留缩略图与图数：单看 id 无法判断该补内容还是该下架，
    必须能看到「这条到底是什么」才能做决定。
    """
    L = ["# 内容体检报告\n", f"生成时间：{_now()}\n"]
    L.append("> 这里只列**内容质量**问题，不阻断构建。")
    L.append("> 结构问题（id 重复 / 孤儿 / 空壳 / 悬空映射）会直接让构建失败。\n")

    for kind, label, items in data_issues:
        L.append(f"## {label} — {len(items)} 条\n")
        if kind == "empty":
            L.append("判定依据：该条目的 `segments` 是空数组。卡片列表里能看到，点开是「暂无详情内容」。\n")
            L.append("| id | 分类 | 标题 | 缩略图 | 建议 |")
            L.append("|---|---|---|---|---|")
            for r in items:
                advice = "抓取残留，建议下架" if r["title"] in ("无标题", "") else "缺正文，建议补内容或下架"
                L.append(f"| `{r['id']}` | {r['cat']} | {r['title'] or '(空)'} | "
                         f"{'[查看](' + r['thumb'] + ')' if r['thumb'] else '无'} | {advice} |")
        elif kind == "foreign":
            L.append("判定依据：图片 URL 的域名不是 `img.feijibei.top`。\n")
            L.append("实测结论：`tiebapic.baidu.com` 带 Referer 返回 403；不带 Referer 返回 200，")
            L.append("但内容是**占位图** —— 两个完全不同的 URL 返回同一个 4262 字节文件（md5 相同），")
            L.append("说明原图已失效、拿不回来了。这些条目大多只坏 1 张，其余图都在 R2。\n")
            L.append("| id | 分类 | 标题 | 坏图/总图 | 失效链接 | 建议 |")
            L.append("|---|---|---|---|---|---|")
            for r in items:
                L.append(f"| `{r['id']}` | {r['cat']} | {r['title'] or '(空)'} | "
                         f"{r['n_bad']}/{r['n_img']} | `{r['bad_url'][:60]}…` | 删掉该图 |")
        L.append("")

    L.append("## 怎么处理\n")
    L.append("```bash")
    L.append("# 下架：status 改成 draft —— 条目从列表与搜索里消失，数据仍完整保留，随时可改回")
    L.append("python3 tools/set_status.py --draft --from-report          # 下架本报告里「没有正文」的全部条目（预演）")
    L.append("python3 tools/set_status.py --draft --from-report --apply  # 实际写入")
    L.append("python3 tools/set_status.py --draft <id> [<id> ...]        # 指定 id")
    L.append("")
    L.append("# 删掉失效图片（预演后加 --apply）：")
    L.append("python3 tools/fix_broken_images.py")
    L.append("python3 tools/fix_broken_images.py --apply")
    L.append("")
    L.append("# 补内容：直接编辑 content/posts/{id}.json 的 segments 数组，然后重新构建")
    L.append("python3 tools/build.py")
    L.append("```")
    (SRC / "_build_report.md").write_text("\n".join(L), encoding="utf-8")


def _now():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 构建产物只需要这些文件上线。content/ 与 tools/ 是开发资产，
# 通过 --dist 打包可以把它们挡在部署目录之外（否则会被当成静态文件公开）。
SITE_FILES = [
    "index.html", "detail.html", "app.js", "site-common.js", "style.css",
    "sw.js", "manifest.json", "icon-192.svg", "icon-512.svg",
    "_headers", "_redirects", "robots.txt", "sitemap.xml",
    "admin.html", "sedum-evo-2019.html", "景天科演化图谱.html",
]


def assemble_dist(dist):
    """把可部署文件 + 生成的 data/ 组装到 dist/。"""
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir(parents=True)
    copied = []
    for name in SITE_FILES:
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, dist / name)
            copied.append(name)
    return copied


def stamp(target=None, data_dir=None):
    """把构建号写进 index.html 的 ?v= 与 sw.js 的 __BUILD__。

    构建号 = 日期 + index.json 内容指纹。这样只要内容变了，构建号必变，
    Service Worker 的 CACHE 名随之变化，旧缓存会被 activate 阶段清掉；
    内容没变则构建号不变，不制造无意义的缓存失效。
    替代原来手写 `feijibei-v3.4` 与 `?v=20261014` 的做法（手写迟早忘）。
    """
    base = Path(target) if target else ROOT
    ddir = Path(data_dir) if data_dir else (base / "data")
    idx = ddir / "index.json"
    digest = hashlib.sha1(idx.read_bytes()).hexdigest()[:7] if idx.exists() else "0000000"
    build_id = f"{_date_str()}-{digest}"

    for name in ("index.html", "sw.js"):
        p = base / name
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        new = re.sub(r"\?v=[0-9A-Za-z\-]+", f"?v={build_id}", text)
        new = new.replace("__BUILD__", build_id)
        if new != text:
            p.write_text(new, encoding="utf-8")
            print(f"  ✓ {name} → {build_id}")
    return build_id


def _date_str():
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="生成到临时目录并与现有 data/ 比对")
    ap.add_argument("--stamp", action="store_true", help="刷新 index.html / sw.js 构建号")
    ap.add_argument("--dist", type=Path, default=None,
                    help="打包模式：组装可部署目录（含生成的 data/，不含 content/ 与 tools/）")
    ap.add_argument("--out", type=Path, default=DATA, help="输出目录")
    args = ap.parse_args()

    if not POSTS.exists():
        print(f"✗ 找不到源目录 {POSTS}", file=sys.stderr)
        return 1

    taxonomy = load(SRC / "taxonomy.json")
    posts = load_posts()

    errors, warnings = [], []
    validate(posts, taxonomy, errors, warnings)

    print(f"源: content/posts/  {len(posts)} 条")
    if warnings:
        print(f"\n⚠️  警告 {len(warnings)} 条（不阻断构建）:")
        for w in warnings[:15]:
            print(f"   - {w}")
        if len(warnings) > 15:
            print(f"   … 另有 {len(warnings) - 15} 条")
    if errors:
        print(f"\n✗ 校验失败 {len(errors)} 条:", file=sys.stderr)
        for e in errors[:20]:
            print(f"   - {e}", file=sys.stderr)
        return 1

    if args.verify:
        tmp = Path(tempfile.mkdtemp(prefix="build-verify-"))
        stats = generate(posts, taxonomy, tmp)
        print(f"\n生成: {stats}")
        verify(tmp, DATA)
        print(f"\n（临时产物保留在 {tmp}）")
        return 0

    if args.dist:
        dist = args.dist.resolve()
        copied = assemble_dist(dist)
        stats = generate(posts, taxonomy, dist / "data")
        print(f"\n生成完成: {stats}")
        print(f"打包到: {dist}")
        print(f"  上线文件 {len(copied)} 个：{'、'.join(copied)}")
        print("  （content/ 与 tools/ 已被排除在部署目录之外）")
        ok = check_integrity(dist / "data")
        if args.stamp:
            print("\n刷新构建号:")
            stamp(dist, dist / "data")
        return 0 if ok else 1

    stats = generate(posts, taxonomy, args.out)
    print(f"\n生成完成: {stats}")
    print(f"输出: {args.out}")
    ok = check_integrity(args.out)
    if args.stamp:
        print("\n刷新构建号:")
        stamp(ROOT, args.out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
