#!/usr/bin/env python3
"""花卉周报总入口。

用法：
  python run_pipeline.py            # 完整跑：抓取→过滤→生成 daily.json→推送部署
  python run_pipeline.py --probe    # 探针：只抓取+过滤，输出命中统计与样本，不发布

建议先跑 --probe 验证两平台能抓到足量相关内容，再去掉 --probe 正式发布。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aggregator import load_config, aggregate  # noqa: E402
from image_cache import cache_covers  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="花卉周报流水线")
    ap.add_argument("--probe", action="store_true", help="只跑抓取+过滤，输出统计/样本，不发布")
    args = ap.parse_args()

    cfg = load_config()
    print(f"▶ 开始抓取（关键词 {len(cfg.get('keywords', []))} 个，白名单 "
          f"B站 {len(cfg.get('whitelist', {}).get('bilibili', []))} / "
          f"小红书 {len(cfg.get('whitelist', {}).get('xiaohongshu', []))}）...")
    items, stats = aggregate(cfg)

    # 发布前把第三方外链封面缓存到 R2，改写 cover 为永久 URL（probe 也走，便于验证）
    items = cache_covers(items, cfg)

    if args.probe:
        report = {"stats": stats, "samples": items}
        out = Path(__file__).resolve().parent / "probe_result.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n========== 探针结果 ==========")
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        print(f"\n--- 保留全部 {len(items)} 条 ---")
        for i, s in enumerate(items, 1):
            print(f"{i:2}. [{s['platform']}/{s.get('source','?')}] {s['title']} | 赞 {s['likes']} | {s['url']}")
        print(f"\n探针完成：原始 {stats['total_raw']} 条 → 保留 {stats['kept']} 条。"
              f"完整结果见 daily/probe_result.json")
        return

    from publisher import publish
    issue = publish(items, cfg)
    print(f"✅ 已发布 {issue['issue']}，共 {len(issue['items'])} 条。")


if __name__ == "__main__":
    main()
