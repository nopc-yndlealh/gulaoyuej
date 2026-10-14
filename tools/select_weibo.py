#!/usr/bin/env python3
"""
从 cat="微博" 的条目里挑出真正的园艺内容，其余下架。

背景
----
这个分类里的 1161 条几乎全部来自同一个微博账号「朱玛交柯」（886 条）。
那个账号是「月季 + 汉服服饰史 + 文博 + 社会评论」混着发的，抓取时全量拉了
下来，于是园艺图鉴里混进了大量无关内容（实测：抽样 10 条没有一条是园艺）。

做法
----
两层筛选，都可审计：
  1. 关键词规则：命中园艺词且不含明显无关词 → 进入候选
  2. 人工复核：候选逐条看过，把误命中挑出来，在 EXCLUDE 里写明**每条的原因**

为什么不只用关键词：中文误命中太多 —— 实测「苗寨」命中"苗"、「阿香」命中"香"、
「旺旺」命中"玫瑰"、「第n次逼逼」命中"育种"。只靠词表会放进去一堆无关条目。

用法
----
  python3 tools/select_weibo.py            # 预演，列出保留与剔除
  python3 tools/select_weibo.py --apply    # 保留的置 published，其余置 draft
  python3 tools/select_weibo.py --report   # 把两份名单写进 content/_weibo_selection.md
"""
import argparse
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "content" / "posts"
CAT = "微博"

PLANT = [
    "月季", "蔷薇", "玫瑰", "奥斯汀", "丹陶", "科尔德斯", "戴尔巴德", "玫昂", "吉洛",
    "育种", "花苞", "花期", "花开", "开花", "修剪", "扦插", "嫁接", "盆栽", "花友",
    "花粉", "杂交", "藤本", "多肉", "景天", "石莲", "莲花掌", "绣球", "铁线莲",
    "品种", "花径", "花瓣", "种植", "栽培", "植物", "园艺", "苗", "花朵",
    "开花性", "抗病", "花香", "花墙", "初花", "冬花", "花谱", "香",
]
NEG = [
    "发型", "发髻", "汉服", "服饰", "衣冠", "妆容", "头饰", "首饰", "文物", "博物馆",
    "考古", "壁画", "古画", "朝代", "明制", "清制", "唐制", "宋制", "仕女", "论文",
    "商家", "弹幕", "裙子", "交窬", "道袍", "考据", "砖", "妓", "缠足", "裹脚",
    "装束", "木屐", "赤脚",
]

# 人工复核结果：候选里被判定为「误命中 / 非园艺内容」的，逐条写明原因。
# key 是 post id，value 是剔除理由（会写进报告，便于事后复核与推翻）。
EXCLUDE = {
    # —— 中文误命中 ——
    "weibo_5255086415813589": "缠足/金莲鞋（命中「苗寨」）",
    "weibo_4873523338680469": "「香港侄子」（命中「香」）",
    "weibo_4720801229833663": "正文只有「旺旺」（命中「玫瑰」）",
    "weibo_5087317078839633": "正文只有「可能是第n次逼逼了」（命中「育种」）",
    "weibo_4810456420583844": "正文只有「关注点偏离」（命中「月季」「香」）",
    "weibo_4522886163922560": "动画《怪化猫》与源氏香（命中「香」）",
    "weibo_4678796144019919": "仿妆话题（命中「阿香」）",
    "weibo_4702341792270742": "金腰燕（命中「香」），讲鸟不涉植物",
    "weibo_5291135678546317": "《岭南杂记》服饰史（命中「香」）",
    "weibo_4644696221025705": "白头鹎筑巢（命中「修剪」），讲鸟",
    "weibo_5109710204702902": "「麦苗灯」，语义不明（命中「苗」）",
    "weibo_4654789683843123": "祈福（命中「苗头」）",
    "weibo_5116686703527709": "圈内互骂（命中「树苗」）",
    "weibo_4707097075516482": "山东大葱（命中「种植」）",
    "weibo_4847050758295927": "番薯传入史（命中「苗」）",
    # —— 内容过短 / 只是评论片段，无独立价值 ——
    "weibo_4766995994640571": "正文只有「这就是在植物群待久的下场」",
    "weibo_4779980049093055": "正文只有「认识了个植物友」",
    "weibo_4787295716905025": "正文只有「建议选为新浪微博专属植物」",
    "weibo_5139147497671185": "正文只有「在我国园艺史上写下了浓墨重彩的一笔」",
    "weibo_4524804151628149": "骂战（命中「植物上的虫豸」）",
    # —— 社会 / 时政话题，不是园艺内容 ——
    "weibo_5005402528549078": "「樱花中国论」民族话题",
    "weibo_4882424134369880": "濒危动物保护的社会批评",
    "weibo_4854498307346945": "社会批评",
    "weibo_4915392893423866": "网络争辩",
    "weibo_4839380261536410": "批评媒体炒作",
    "weibo_4549765762917471": "讨论「墙」，仅提到园艺网站",
    "weibo_5108706809481304": "农产品经济评论",
    "weibo_4847692776214280": "「女子野山摘花被刑拘」新闻评论",
    "weibo_4847066923139662": "同上（重复帖）",
    # —— 生活随笔 ——
    "weibo_4959363016164095": "路边木瓜的生活随笔",
    "weibo_4575922667651795": "校园白玉兰的生活回忆",
    # —— 第二轮复核补漏（扩了关键词表后新进来的）——
    "weibo_5280327341708876": "正文只有「只是人的主观想象」（命中「花苞」「开花」「苗」）",
    "weibo_4756738509636063": "金腰燕/家燕，讲鸟不涉植物（命中「香」）",
    "weibo_4560598744437179": "正文只有「猪肉是野猪肉」",
    "weibo_4745836221432278": "「精日/文化侵略」民族话题",
    "weibo_4699232475547616": "「文化输出超话」话题，主体是服饰与文化争论",
    "weibo_4529564813107283": "濒危野生植物保护的社会争论（与前述同类，保持一致剔除）",
}


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def dump(p, obj):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def body(p):
    return p.get("title", "") + " " + " ".join(s.get("t", "") for s in p.get("segments", []))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写入")
    ap.add_argument("--report", action="store_true", help="同时写出名单文件")
    args = ap.parse_args()

    posts = {}
    for f in glob.glob(str(POSTS / "*.json")):
        p = load(f)
        posts[p["id"]] = p

    wb = sorted([p for p in posts.values() if p.get("cat") == CAT], key=lambda x: x.get("seq", 0))
    if not wb:
        print(f"✗ 没有 cat=\"{CAT}\" 的条目", file=sys.stderr)
        return 1

    candidates, dropped_kw = [], []
    for p in wb:
        t = body(p)
        hits = [k for k in PLANT if k in t]
        negs = [k for k in NEG if k in t]
        if hits and not negs:
            candidates.append((p, hits))
        else:
            dropped_kw.append((p, negs))

    keep, drop_manual = [], []
    for p, hits in candidates:
        (drop_manual if p["id"] in EXCLUDE else keep).append((p, hits))

    print(f"cat=\"{CAT}\" 共 {len(wb)} 条")
    print(f"  关键词候选      : {len(candidates)}")
    print(f"  人工剔除        : {len(drop_manual)}")
    print(f"  → **保留**      : {len(keep)}")
    print(f"  → 下架          : {len(wb) - len(keep)}（关键词未命中 {len(dropped_kw)} + 人工剔除 {len(drop_manual)}）\n")

    print("=== 保留（这些会留在「微博」分类里）===")
    for i, (p, hits) in enumerate(keep, 1):
        n = sum(1 for s in p.get("segments", []) if s.get("i"))
        print(f"{i:>3}. {p['id']}  图{n:<2} {p.get('title','')[:56]}")

    print("\n=== 人工剔除（附原因，可推翻）===")
    for p, hits in drop_manual:
        print(f"  {p['id']}  {EXCLUDE[p['id']]}")
        print(f"       原标题: {p.get('title','')[:56]}")

    if args.report or args.apply:
        L = ["# 微博分类筛选结果\n", f"- 分类内总计 {len(wb)} 条",
             f"- 保留 **{len(keep)}** 条，其余下架（`status=draft`，数据保留、可随时恢复）\n",
             "## 保留\n", "| # | id | 标题 | 图 |", "|---|---|---|---|"]
        for i, (p, hits) in enumerate(keep, 1):
            n = sum(1 for s in p.get("segments", []) if s.get("i"))
            L.append(f"| {i} | `{p['id']}` | {p.get('title','').replace('|','\\|')[:70]} | {n} |")
        L.append("\n## 人工剔除（附原因）\n")
        L.append("| id | 剔除原因 | 原标题 |")
        L.append("|---|---|---|")
        for p, _ in drop_manual:
            L.append(f"| `{p['id']}` | {EXCLUDE[p['id']]} | {p.get('title','').replace('|','\\|')[:60]} |")
        L.append(f"\n## 关键词未命中而下架（{len(dropped_kw)} 条）\n")
        L.append("| id | 标题 |")
        L.append("|---|---|")
        for p, _ in dropped_kw:
            L.append(f"| `{p['id']}` | {p.get('title','').replace('|','\\|')[:70]} |")
        (ROOT / "content" / "_weibo_selection.md").write_text("\n".join(L) + "\n", encoding="utf-8")
        print(f"\n名单已写入 content/_weibo_selection.md")

    if not args.apply:
        print("\n── 预演结束，未写盘。加 --apply 实际执行。──")
        return 0

    keep_ids = {p["id"] for p, _ in keep}
    changed = 0
    for p in wb:
        want = "published" if p["id"] in keep_ids else "draft"
        if p.get("status") != want:
            p["status"] = want
            dump(POSTS / f"{p['id']}.json", p)
            changed += 1
    print(f"\n✓ 已修改 {changed} 条（保留 {len(keep_ids)}，下架 {len(wb)-len(keep_ids)}）")
    print("下一步：python3 tools/build.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
