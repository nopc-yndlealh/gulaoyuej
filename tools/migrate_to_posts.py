#!/usr/bin/env python3
"""
一次性迁移：把 data/ 里四份互相打架的派生产物，收敛为 content/posts/ 单一源。

迁移前的问题（实测）：
  同一份内容存在 4 套「真相」，全靠脚本手工对齐，已漂移 44%：
    - data/index.json              的 cat   ：29 类，读者分类（权威）
    - data/content/content-index.json 的值  ：30 类，旧存储分块名（贴吧 / 多肉-石莲花属 …）
    - data/content/{分块}.json     的 type  ：与 index.json 的 type 含义不同
    - data/search-index.json       的 cat   ：第三份 cat
  实测 1711 / 3903 条两套口径对不上；贴吧 637 条已在 index 里被按主题重分类
  （486+89+25+22+13+2 = 637 ✓）但 content-index 还停在旧的「贴吧」口径。

迁移原则：零信息损失。两侧所有原字段都保留，只做：
  ① 确定权威：cat 以 index.json 为准（它是控制侧边栏的字段，且是较新的整理结果）
  ② 声明来源：新增 source 字段（由 id 前缀派生），不销毁来源信息
  ③ 标记状态：只在 content 里存在、index 里缺失的条目 → status="draft"（读者看不到，不上架）
  ④ 生成审计报告：把所有口径分歧写进 content/_migration_report.md

用法：
  python tools/migrate_to_posts.py            # 预演，只报告不写盘
  python tools/migrate_to_posts.py --apply    # 实际写盘
"""
import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "content"
POSTS = OUT / "posts"

# 分类树：这是分类的唯一真相。
# 与 app.js 的 TREE_CONFIG 对应，并补上原先漏掉的两项：
#   - 「微博」899 条：app.js 里 "微博" 出现 0 次，这 899 条在侧边栏/移动端完全不可达
#   - 「多肉」1 条（知识图谱）：同样不可达
TREE = [
    {
        "name": "月季",
        "multi": False,
        "children": [
            "丹陶", "吉洛", "奥斯汀", "戴尔巴德", "玫昂", "科尔德斯",
            "古老月季", "育种相关", "特性合集", "种植分享",
            "竞赛,评选以及月季园实测", "其他育种家", "云小123456",
            "微博",
        ],
    },
    {
        "name": "多肉",
        "multi": True,
        "prefix": "多肉/",
        "children": [
            "石莲花属", "莲花掌属", "风车草属", "青锁龙属", "景天属",
            "仙女杯属", "Lenophyllum", "美丽莲属", "厚叶草属",
            "Prometheum", "瓦松属", "综合日常",
        ],
    },
    {"name": "古代园艺", "multi": False, "children": []},
    {"name": "养花日常", "multi": False, "children": []},
    {"name": "园艺导航", "multi": False, "children": []},
    {"name": "花友推荐", "multi": False, "children": []},
]

# 多肉组下、直接挂在组名上的条目（cat == "多肉" 本身）
PARENT_CATS = ["多肉"]


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump(path, obj, compact=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def source_of(pid):
    """由 id 前缀派生来源平台，不销毁 info。"""
    return pid.split("_", 1)[0]


def chunk_slug(cat):
    """与旧 build_all.py 一致的存储分块命名规则，仅用于审计对照。"""
    return cat.replace("/", "-").replace(" ", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写盘（默认只预演）")
    args = ap.parse_args()

    # ── 读取 ──
    idx = load(DATA / "index.json")
    ci = load(DATA / "content" / "content-index.json")
    content = {}
    for f in sorted((DATA / "content").glob("*.json")):
        if f.name == "content-index.json":
            continue
        content.update(load(f))

    legacy = {}
    legacy_path = DATA / "content.json"
    if legacy_path.exists():
        legacy = load(legacy_path)

    idx_by_id = {e["id"]: e for e in idx}

    print(f"index.json        {len(idx):>5} 条")
    print(f"content-index     {len(ci):>5} 条")
    print(f"content 分块合并   {len(content):>5} 条")
    print(f"content.json 遗留  {len(legacy):>5} 条（无任何代码引用）")
    print()

    # ── 集合关系体检 ──
    ids_idx, ids_ci, ids_ct = set(idx_by_id), set(ci), set(content)
    orphans = sorted(ids_ct - ids_idx)          # 有正文、无列表项 → 读者看不到
    dangling = sorted(ids_idx - ids_ct)         # 有列表项、无正文 → 点开空白
    ci_only = sorted(ids_ci - ids_ct)           # 映射指向不存在的正文
    legacy_only = sorted(set(legacy) - ids_ct)  # 只在遗留单体里的

    print("=== 集合关系 ===")
    print(f"  有正文但读者看不到（孤儿）      : {len(orphans)}")
    print(f"  有列表项但点开空白              : {len(dangling)}  {dangling[:5]}")
    print(f"  映射指向不存在的正文            : {len(ci_only)}  {ci_only[:5]}")
    print(f"  仅在 content.json 里存在        : {len(legacy_only)}")
    print()

    # ── 分类口径分歧统计 ──
    divergence = collections.Counter()
    unknown_cats = collections.Counter()
    for e in idx:
        cat = e.get("cat") or ""
        if cat and cat not in _iter_valid_cats():
            unknown_cats[cat] += 1
    for pid, chunk in ci.items():
        e = idx_by_id.get(pid)
        if e is None:
            divergence[("(不在 index.json 里)", chunk)] += 1
        elif chunk_slug(e.get("cat") or "") != chunk:
            divergence[(e.get("cat"), chunk)] += 1

    print("=== 分类口径分歧（index.cat  vs  content-index 分块名）===")
    total_div = sum(divergence.values())
    format_only = 0
    for pid, chunk in ci.items():
        e = idx_by_id.get(pid)
        if e is None:
            continue
        raw = e.get("cat") or ""
        if raw != chunk and chunk_slug(raw) == chunk:
            format_only += 1
    print(f"  纯格式差异（多肉/石莲花属 vs 多肉-石莲花属）: {format_only}   ← 规范化后无影响")
    print(f"  语义分歧（真的分到不同类）                  : {total_div} / {len(ci)}  ({total_div/len(ci)*100:.1f}%)")
    for (a, b), n in divergence.most_common(10):
        print(f"    {str(a):<24} vs {str(b):<20} {n:>5}")
    print()

    # ── 生成 posts ──
    # 注意：all_ids 必须并入 legacy。实测 data/content.json 里有 141 条
    # 在 content/ 分块中不存在的条目 —— 它不是子集，直接删会丢数据。
    all_ids = sorted(ids_idx | ids_ct | set(legacy))
    # 原始展示顺序：index.json 里的位置。它按来源分组（rose→succ→tieba→weibo→xhs）
    # 且末尾有 7 条后追加的 weibo，无法用规则推导，所以显式记下来。
    seq_of = {e["id"]: i for i, e in enumerate(idx)}
    posts = {}
    stats = collections.Counter()

    for pid in all_ids:
        ie = idx_by_id.get(pid, {})
        ce = content.get(pid) or legacy.get(pid) or {}

        cat = ie.get("cat") or ci.get(pid) or ""
        post = {
            "id": pid,
            "title": ie.get("title") or ce.get("title") or "",
            "cat": cat,
            "source": source_of(pid),
            "seq": seq_of.get(pid, 100000 + all_ids.index(pid)),
        }

        # 原样保留 index.json 的全部字段（含不规则的 date / image）
        for k in ("type", "thumb", "tag", "author", "date", "image"):
            if k in ie and ie[k] not in (None, "", []):
                post[k] = ie[k]

        # 原样保留 content 的全部字段
        post["segments"] = ce.get("segments", [])
        if "title" in ce and ce["title"] != post["title"]:
            post["rawTitle"] = ce["title"]
        if "type" in ce:
            post["contentType"] = ce["type"]
        if "source" in ce:
            post["legacySource"] = ce["source"]
        if ce.get("time") not in (None, ""):
            post["time"] = ce["time"]

        # content 侧的 author / tag 逐字保留。
        # 注意：index.json 的 author 是字符串（"跌水繁落º"），content 的 author
        # 是 {name, avatar, url} 对象。两者含义不同，不能让一个覆盖另一个 ——
        # 旧版这里把 content 的对象挤进 conflicts 字段，构建时被丢弃，
        # 导致 2482 条的头像 URL 静默消失（当前 UI 不渲染头像所以没人发现）。
        if ce.get("author") not in (None, "", [], {}):
            post["contentAuthor"] = ce["author"]
        if ce.get("tag") not in (None, ""):
            post["contentTag"] = ce["tag"]

        post["status"] = "published" if pid in ids_idx else "draft"
        if pid not in ids_ct and pid in legacy:
            post["legacyOnly"] = True

        if not post["segments"]:
            stats["无正文"] += 1
        if post["status"] == "draft":
            stats["草稿(读者原本看不到)"] += 1
        if post.get("legacyOnly"):
            stats["仅在遗留 content.json 里"] += 1
        if not cat:
            stats["分类为空"] += 1
        elif cat not in _iter_valid_cats():
            stats["分类不在 taxonomy 里"] += 1
        posts[pid] = post

    print("=== 迁移结果统计 ===")
    for k, v in stats.items():
        print(f"  {k:<26} {v}")
    print(f"  {'合计 post 文件':<26} {len(posts)}")
    print()

    print("=== 分类分布 ===")
    for c, n in collections.Counter(p["cat"] for p in posts.values()).most_common():
        flag = "" if c in _iter_valid_cats() else "   ← 不在 taxonomy"
        print(f"  {str(c):<26} {n:>5}{flag}")
    print()

    seg_kinds = collections.Counter()
    for p in posts.values():
        for s in p["segments"]:
            seg_kinds[tuple(sorted(s.keys()))] += 1
    print("=== segment 形态 ===")
    for k, n in seg_kinds.most_common():
        print(f"  {'+'.join(k):<12} {n:>7}")
    print()

    if not args.apply:
        print("── 预演结束，未写盘。加 --apply 实际执行。──")
        return 0

    # ── 写盘 ──
    if POSTS.exists():
        print(f"⚠️ {POSTS} 已存在，中止以免覆盖。请先手动清理。", file=sys.stderr)
        return 1

    taxonomy = {
        "version": 1,
        "note": "分类的唯一真相。post 里的 cat 必须命中此处的某个分组名 / children 项 / parentCats 项，否则构建脚本会报错。",
        "tree": TREE,
        "parentCats": PARENT_CATS,
    }
    dump(OUT / "taxonomy.json", taxonomy)

    for pid, post in posts.items():
        dump(POSTS / f"{pid}.json", post)

    # 转存非内容型配置
    for name in ("featured.json", "nav-links.json", "authors.json"):
        src = DATA / name
        if src.exists():
            dump(OUT / name, load(src))

    # ── 审计报告 ──
    lines = []
    lines.append("# 数据迁移报告\n")
    lines.append(f"- 迁移来源：`data/index.json` + `data/content/content-index.json` + `data/content/*.json`")
    lines.append(f"- 产出：`content/posts/{{id}}.json` × {len(posts)}")
    lines.append(f"- 分类权威：`index.json` 的 `cat`（贴吧 637 条已按主题重分类，比 content-index 的旧口径新）\n")

    lines.append("## 修复的问题\n")
    lines.append(f"1. **{len(orphans)} 条孤儿内容**：有正文、没进 `index.json`，读者完全看不到 → 已迁入并标记 `status: draft`")
    lines.append(f"2. **{len(dangling)} 条空壳列表项**：`index.json` 有、正文缺失，点开是空白 → 见下方清单")
    lines.append(f"3. **{len(ci_only)} 条悬空映射**：`content-index` 指向不存在的正文 → 已丢弃该映射")
    lines.append(f"4. **{total_div} 条分类口径分歧**（占 {total_div/len(ci)*100:.1f}%）：已统一到 `cat`，来源信息保留在 `source` 字段\n")

    lines.append("## 悬空映射（有列表项、无正文，需要人工补或删）\n")
    for d in dangling:
        e = idx_by_id[d]
        lines.append(f"- `{d}` — {e.get('title','')}（cat={e.get('cat')}）")
    lines.append("")

    lines.append("## 仅在遗留 content.json 里的条目\n")
    if legacy_only:
        for l in legacy_only[:50]:
            lines.append(f"- `{l}`")
    else:
        lines.append("（无，`data/content.json` 是 `content/` 分块的子集，可安全删除）")
    lines.append("")

    lines.append("## 分类口径分歧明细（前 60 条）\n")
    for (a, b), n in divergence.most_common(20):
        lines.append(f"- `index.cat={a}` ←→ `content-index 分块={b}`：{n} 条")
    lines.append("")

    lines.append("## 下一步\n")
    lines.append(f"- `status: draft` 的 {len(orphans)} 条需你过一遍：确认是垃圾就删，是内容就改 `status` 为 `published`")
    lines.append("- `cat=\"微博\"` 的条目是待重分类队列（你已有 weibo-classifier 流程）")
    lines.append("- 确认无误后，把 `data/` 加进 `.gitignore`，由构建脚本在 CF Pages 构建时现场生成")

    report_path = OUT / "_migration_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"✓ 已写入 content/taxonomy.json")
    print(f"✓ 已写入 content/posts/ × {len(posts)}")
    print(f"✓ 已写入 content/_migration_report.md")
    for name in ("featured.json", "nav-links.json", "authors.json"):
        if (DATA / name).exists():
            print(f"✓ 已转存 content/{name}")
    return 0


def _iter_valid_cats():
    cats = set(PARENT_CATS)
    for g in TREE:
        cats.add(g["name"])
        for c in g["children"]:
            cats.add(c)
            if g["multi"]:
                cats.add(f"{g['prefix']}{c}")
    return cats


if __name__ == "__main__":
    sys.exit(main())
