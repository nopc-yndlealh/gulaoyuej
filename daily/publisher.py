"""发布器：生成 data/daily.json + 归档，并 git push 触发 GitHub Pages 重部署。

push 失败不阻塞本地生成（下次运行会重试）。
"""
from __future__ import annotations

import json
import subprocess
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
DAILY_JSON = DATA_DIR / "daily.json"
ARCHIVE_DIR = DATA_DIR / "daily" / "archive"


def iso_week_issue(d: date | None = None) -> str:
    d = d or date.today()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _count(items: list[dict]) -> dict:
    c: dict[str, int] = {}
    for it in items:
        c[it.get("platform", "?")] = c.get(it.get("platform", "?"), 0) + 1
    return c


def build_issue(items: list[dict], cfg: dict) -> dict:
    return {
        "issue": iso_week_issue(),
        "generated_at": datetime.now().astimezone().isoformat(),
        "frequency": cfg.get("frequency", "weekly"),
        "source_counts": _count(items),
        "items": items,
    }


def publish(items: list[dict], cfg: dict) -> dict:
    issue = build_issue(items, cfg)
    DAILY_JSON.write_text(json.dumps(issue, ensure_ascii=False, indent=2), encoding="utf-8")
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_file = ARCHIVE_DIR / f"{issue['issue']}.json"
    archive_file.write_text(json.dumps(issue, ensure_ascii=False, indent=2), encoding="utf-8")
    _git_push(issue["issue"])
    return issue


def _git_push(issue_label: str) -> None:
    try:
        subprocess.run(
            ["git", "add", "data/daily.json", f"data/daily/archive/{issue_label}.json"],
            cwd=REPO_ROOT, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"花卉周报：{issue_label}"],
            cwd=REPO_ROOT, check=True,
        )
        subprocess.run(["git", "push"], cwd=REPO_ROOT, check=True)
        print("✅ 已推送到 gulaoyuej，GitHub Pages 将自动重部署。")
    except subprocess.CalledProcessError as e:
        print(f"⚠️ git 推送失败（不影响本地 daily.json 生成）：{e}")
        print("   下次成功推送后站点即更新；本地文件已就绪。")
