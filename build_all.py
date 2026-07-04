#!/usr/bin/env python3
"""
统一构建：正确生成 index.json + chunked content/ + content-index.json + search-index.json
分类规则：贴吧/微博 → 月季；小红书 → 多肉/小红书
"""
import json, os, csv, re, shutil, struct, sys, argparse
from pathlib import Path
from collections import defaultdict

SCRIPT_DIR = Path(__file__).parent  # build_all.py 所在目录
R2_PUBLIC = "https://img.feijibei.top"
SITE_DATA = SCRIPT_DIR / "data"
DEFAULT_DATA_DIR = Path.home() / "WorkBuddy" / "2026-05-27-17-57-56" / "output"
DEFAULT_MAPPING_CSV = Path("E:/WebP_Compressed/image_mapping_all.csv")
IMG_DIR = SCRIPT_DIR / "images"
MIN_IMG_DIM = 200  # 最小图片尺寸阈值（像素）

def parse_args():
    p = argparse.ArgumentParser(description="构建 feijibei.top 数据管道")
    p.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR,
                   help=f"源数据目录（默认: {DEFAULT_DATA_DIR}）")
    p.add_argument("--mapping-csv", type=Path, default=DEFAULT_MAPPING_CSV,
                   help=f"图片映射 CSV 路径（默认: {DEFAULT_MAPPING_CSV}）")
    return p.parse_args()

args = parse_args()
DATA_DIR = args.data_dir
MAPPING_CSV = args.mapping_csv

# ── 备份现有（只备份原数据，不备份我们之前生成的） ──
for fn in ["content-index.json", "search-index.json"]:
    bak_path = SITE_DATA / (fn + ".bak_new")
    src_path = SITE_DATA / fn
    if src_path.exists():
        shutil.copy2(src_path, bak_path)

# ── 读取现有 index.json，保留原月季/多肉条目 ──
with open(SITE_DATA / "index.json", "r", encoding="utf-8") as f:
    idx_all = json.load(f)
original = [a for a in idx_all if a["id"].startswith(("rose_", "succ_"))]
print(f"保留原有: {len(original)} 条")

# ── 读取旧 content chunk data ──
old_content = {}
content_dir = SITE_DATA / "content"
for cat_file in content_dir.iterdir():
    if cat_file.suffix == ".json" and cat_file.name != "content-index.json":
        try:
            with open(cat_file, "r", encoding="utf-8") as f:
                old_content.update(json.load(f))
        except (json.JSONDecodeError, OSError) as e:
            print(f"⚠️ 跳过损坏的 content 文件 {cat_file.name}: {e}")
print(f"保留原有content: {len(old_content)} 条")

# ── 预建 weibo 映射 ──
# Step 1: 从 unified_posts.json 获取原始 JPG 路径
stem_to_pid = {}  # original JPG stem → post_id
unified_path = DATA_DIR / "unified_posts.json"
if unified_path.exists():
    with open(unified_path, "r", encoding="utf-8") as f:
        unified = json.load(f)
    for up in unified:
        if up.get("platform") == "weibo":
            pid = up["id"]
            for img in up.get("local_images", []):
                stem = Path(str(img)).stem
                if not stem.startswith("weibo_"):  # 原始 JPG stem
                    stem_to_pid[stem] = pid
print(f"unified stem→pid: {len(stem_to_pid)}")

# Step 2: CSV → stem → webp filename
stem_to_webp = {}
try:
    with open(MAPPING_CSV, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["platform"] == "weibo":
                stem_to_webp[Path(r["original"]).stem] = Path(r["webp_rel"]).name
except FileNotFoundError:
    print(f"⚠️ 映射表未找到: {MAPPING_CSV}，跳过 CSV 桥接匹配")

# Step 3: 直接 post ID 匹配（旧 weibo webp）
pid_webps_direct = defaultdict(list)
old_dir = IMG_DIR
for f in old_dir.glob("weibo_*.webp"):
    m = re.match(r"weibo_(\d+)_.*", f.name)
    if m:
        pid_webps_direct[f"weibo_{m.group(1)}"].append(f.name)

# Step 4: 通过 CSV 桥接匹配（新 weibo webp，无 post ID）
pid_webps_csv = defaultdict(list)
for stem, webp_fname in stem_to_webp.items():
    pid = stem_to_pid.get(stem)
    if pid:
        pid_webps_csv[pid].append(webp_fname)

# 合并
weibo_webps = defaultdict(list)
for pid, files in pid_webps_direct.items():
    weibo_webps[pid].extend(files)
for pid, files in pid_webps_csv.items():
    for f in files:
        if f not in weibo_webps[pid]:
            weibo_webps[pid].append(f)
print(f"weibo posts with webp: {len(weibo_webps)}")

# ── 辅助函数 ──
def get_webp_size(filepath):
    """读取 WebP 文件头获取尺寸"""
    try:
        with open(filepath, 'rb') as f:
            header = f.read(30)
        if header[:4] == b'RIFF' and header[8:12] == b'WEBP':
            if header[12:16] == b'VP8 ':
                w = struct.unpack('<H', header[26:28])[0]
                h = struct.unpack('<H', header[28:30])[0]
                return w, h
            elif header[12:16] == b'VP8L':
                bits = struct.unpack('<I', header[21:25])[0]
                w = (bits & 0x3FFF) + 1
                h = ((bits >> 14) & 0x3FFF) + 1
                return w, h
    except (OSError, struct.error) as e:
        print(f"⚠️ 读取 WebP 尺寸失败 {filepath}: {e}")
    return None, None

# 预扫描所有 webp 文件尺寸，构建小图黑名单
print("预扫描 webp 文件尺寸...")
small_webp_set = set()  # 存储小图的文件名
total_scanned = 0
for wf in Path(IMG_DIR).glob("tieba_*.webp"):
    total_scanned += 1
    w, h = get_webp_size(wf)
    if w is not None and (w < MIN_IMG_DIM or h < MIN_IMG_DIM):
        small_webp_set.add(wf.name)
print(f"  扫描 {total_scanned} 个 tieba webp，发现 {len(small_webp_set)} 个小图（<{MIN_IMG_DIM}px）")

def load_jsonl_or_json(path):
    """加载 json 或 jsonl 数据"""
    with open(path, "r", encoding="utf-8") as f:
        first = f.read(1)
        f.seek(0)
        if first == "[":
            return json.load(f)
        elif first == "{":
            d = json.load(f)
            return d.get("posts", d)
        else:
            # jsonl
            f.seek(0)
            return [json.loads(line) for line in f if line.strip()]

def extract_title(post):
    """从帖子中提取标题"""
    title = post.get("title", "").strip()
    if title:
        return title
    # 微博/贴吧等源数据 title 可能为空，从 content 提取首句
    content = post.get("content", "").strip()
    if not content:
        return "无标题"
    # 去除 //@xxx: 转发标记
    cleaned = re.sub(r'//@[^:：\s]+[:：]\s*', '', content).strip()
    # 去除 "→ 查看图片" 等标记
    cleaned = re.sub(r'[→]\s*查看图片', '', cleaned).strip()
    if not cleaned:
        cleaned = content  # fallback
    # 取第一句（按 。！？\n 分割）
    parts = re.split(r'[。！？\n]', cleaned)
    first = ''
    for p in parts:
        p = p.strip()
        if p:
            first = p
            break
    if not first and not cleaned.startswith(('？', '！', '。')):
        first = cleaned.strip()
    # 限制长度
    if len(first) > 60:
        first = first[:57] + '...'
    return first if first else "无标题"

def process_posts(posts, platform, cat_field, index_type, content_type):
    """
    cat_field: index.json 的 cat（控制侧边栏分类）
    index_type: index.json 的 type（如 '月季'、'多肉'）
    content_type: content.json 的 type（如 '微博'、'小红书'，前端用于布局判断）
    """
    index_entries = []
    content_entries = {}  # id → content
    search_entries = {}   # id → {title, cat, text, thumb}
    author_counter = defaultdict(int)

    for post in posts:
        pid = post.get("id", "")
        raw_title = extract_title(post)
        content_text = post.get("content", "").strip()
        author = post.get("author", {})
        author_name = author.get("name", "未知") if isinstance(author, dict) else str(author)
        author_counter[author_name] += 1
        tag = str(author_counter[author_name])
        # 标题前置标记：#N 作者名 - 原标题
        title = f"#{tag} {author_name} - {raw_title}"
        time_display = post.get("time_display", "")

        # 获取图片
        images = []
        # 贴吧/小红书：检查原始URL过滤小图
        remote_images = post.get("images", [])
        small_idx = set()
        for idx, url in enumerate(remote_images):
            # 1. URL中带尺寸参数且小于200
            m = re.search(r'w%3D(\d+)%3Bh%3D(\d+)', url)
            if m and (int(m.group(1)) < 200 or int(m.group(2)) < 200):
                small_idx.add(idx)
                continue
            # 2. sign= 路径的贴吧小图标/水印
            if '/sign=' in url:
                small_idx.add(idx)
                continue
            # 3. 任何 w%3D 但 h%3D 不在URL中的
            if re.search(r'w%3D\d+', url) and 'h%3D' not in url:
                small_idx.add(idx)
                continue

        if platform == "weibo":
            images = sorted(weibo_webps.get(pid, []))
        else:
            # tieba/xhs: 直接从 images_webp 获取，跳过小图
            webp_list = post.get("images_webp", post.get("local_images", []))
            for idx, img in enumerate(webp_list):
                if idx in small_idx:
                    continue
                fname = Path(str(img).replace("\\", "/")).name if img else ""
                if not fname or fname in images:
                    continue
                # 二次过滤：检查实际 webp 文件尺寸
                if fname in small_webp_set:
                    small_idx.add(idx)
                    continue
                images.append(fname)

        # 没有图片也保留帖子（文字内容可能仍有价值）
        # if not images:
        #     continue

            # 构建 segments
        segments = []
        if content_type in ("微博", "小红书"):
            # social 布局：图在前
            for fname in images:
                segments.append({"i": f"{R2_PUBLIC}/images/{fname}"})
            if content_text:
                segments.append({"t": content_text})
        elif post.get("segments"):
            # 贴吧 segments 格式：保留图文交错顺序（补全用）
            text_parts = []
            for seg in post["segments"]:
                if "i" in seg:
                    url = seg["i"]
                    segments.append({"i": url})
                    # 提取文件名用于 images 列表
                    fname = url.rsplit("/", 1)[-1].split("?")[0]
                    if fname not in images:
                        images.append(fname)
                elif "t" in seg and str(seg["t"]).strip():
                    segments.append({"t": seg["t"]})
                    text_parts.append(str(seg["t"]))
            if text_parts and not content_text:
                content_text = "\n".join(text_parts)
        else:
            # normal 布局：文图交错（旧逻辑）
            if content_text:
                segments.append({"t": content_text})
            for fname in images:
                segments.append({"i": f"{R2_PUBLIC}/images/{fname}"})

        r2_images = [f"{R2_PUBLIC}/images/{f}" for f in images]

        # Index entry
        index_entries.append({
            "id": pid, "title": title,
            "cat": cat_field,
            "type": index_type,
            "images": r2_images,
            "tag": tag, "author": author_name,
        })

        # Content entry (保留原始标题，弹窗用)
        content_entries[pid] = {
            "title": raw_title, "type": content_type,
            "segments": segments,
            "author": author if isinstance(author, dict) else {"name": author_name},
            "time": time_display, "tag": tag,
        }

        # Search entry
        thumb = r2_images[0] if r2_images else ""
        search_entries[pid] = {
            "title": title, "cat": cat_field,
            "text": content_text[:500] if content_text else "",
            "thumb": thumb,
        }

    return index_entries, content_entries, search_entries

# ── 处理三个平台 ──
new_idx = list(original)
new_content = dict(old_content)
new_search = {}
new_cidx = {}  # id → cat_slug

# 保留原搜索索引
si_path = SITE_DATA / "search-index.json"
if si_path.exists():
    with open(si_path, "r", encoding="utf-8") as f:
        new_search = json.load(f)
        # 只保留原有条目
        new_search = {k: v for k, v in new_search.items() if k.startswith(("rose_", "succ_"))}

for k, v in old_content.items():
    new_cidx[k] = v.get("_cat", "未知") if isinstance(v, dict) else "未知"  # 回头修正

# 读取原始 content-index
ci_path = SITE_DATA / "content" / "content-index.json"
if ci_path.exists():
    with open(ci_path, "r", encoding="utf-8") as f:
        new_cidx = json.load(f)

# 1. 贴吧 — cat=贴吧, index_type=月季, content_type=月季 (normal layout)
tieba_posts = load_jsonl_or_json(DATA_DIR / "tieba_clean.json")
print(f"贴吧: {len(tieba_posts)} posts")
ti_idx, ti_con, ti_srch = process_posts(tieba_posts, "tieba", "贴吧", "月季", "月季")
new_idx.extend(ti_idx)
new_content.update(ti_con)
new_search.update(ti_srch)
for pid in ti_con:
    new_cidx[pid] = "贴吧"
print(f"  +{len(ti_idx)} entries")

# 2. 微博 — cat=微博, index_type=月季, content_type=微博 (social layout)
weibo_posts = load_jsonl_or_json(DATA_DIR / "weibo_clean.json")
print(f"微博: {len(weibo_posts)} posts")
wb_idx, wb_con, wb_srch = process_posts(weibo_posts, "weibo", "微博", "月季", "微博")
new_idx.extend(wb_idx)
new_content.update(wb_con)
new_search.update(wb_srch)
for pid in wb_con:
    new_cidx[pid] = "微博"
print(f"  +{len(wb_idx)} entries")

# 3. 小红书 — cat=多肉/小红书, index_type=多肉, content_type=小红书 (social layout)
xhs_posts = load_jsonl_or_json(DATA_DIR / "xhs_clean.json")
print(f"小红书: {len(xhs_posts)} posts")
xhs_idx, xhs_con, xhs_srch = process_posts(xhs_posts, "xhs", "多肉/小红书", "多肉", "小红书")
new_idx.extend(xhs_idx)
new_content.update(xhs_con)
new_search.update(xhs_srch)
for pid in xhs_con:
    new_cidx[pid] = "多肉-小红书"
print(f"  +{len(xhs_idx)} entries")

# ── 特殊页面：独立 HTML 文件，非数据驱动 ──
SPECIAL_PAGES = [
    {
        "id": "sedum_evo_2019",
        "title": "墨西哥景天科演化树 · 知识图谱",
        "cat": "多肉",
        "type": "知识图谱",
        "images": [],
        "tag": "special",
        "author": ""
    }
]
new_idx.extend(SPECIAL_PAGES)
# 在 content-index 中注册映射，使 app.js 能通过 getContent 查找
for sp in SPECIAL_PAGES:
    new_cidx[sp["id"]] = "多肉"
print(f"  +{len(SPECIAL_PAGES)} 特殊页面")

# ── 写入文件 ──
# index.json
with open(SITE_DATA / "index.json", "w", encoding="utf-8") as f:
    json.dump(new_idx, f, ensure_ascii=False, indent=2)
print(f"\nindex.json: {len(new_idx)} 条 ({(SITE_DATA / 'index.json').stat().st_size / 1024:.0f} KB)")

# content-index.json
with open(ci_path, "w", encoding="utf-8") as f:
    json.dump(new_cidx, f, ensure_ascii=False, indent=2)
print(f"content-index.json: {len(new_cidx)} mappings")

# Chunked content files (按 cat 分块，直接覆盖旧文件)
content_dir = SITE_DATA / "content"

# Group by cat
cat_groups = defaultdict(dict)
for pid, data in new_content.items():
    cat = new_cidx.get(pid, "其他")
    cat_groups[cat][pid] = data

for cat, items in cat_groups.items():
    fname = cat.replace("/", "-").replace(" ", "") + ".json"
    fpath = content_dir / fname
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"  content/{fname}: {len(items)} 条")

# search-index.json
with open(SITE_DATA / "search-index.json", "w", encoding="utf-8") as f:
    json.dump(new_search, f, ensure_ascii=False, indent=2)
print(f"search-index.json: {len(new_search)} 条")

# ── 标签分布 ──
print("\n标签分布:")
for cat, entries in sorted(cat_groups.items()):
    tags = [e.get("tag", "?") for e in entries.values()]
    if tags and tags[0] != "?":
        authors = set(e.get("author", {}).get("name", "") for e in entries.values() if isinstance(e.get("author"), dict))
        print(f"  {cat}: {len(entries)} 条, {len(authors)} authors, tags 1-{max(int(t) for t in tags if t.isdigit())}")
print("\nDone!")
