#!/usr/bin/env python3
"""R2 图片缓存：把第三方外链封面下载 → 转 WebP → 上传 Cloudflare R2，改写永久 URL。

所有 R2 配置一律从环境变量读取，**绝不硬编码**任何凭证。
设计原则：单条封面缓存失败只告警并返回原始链接，绝不中断整期运行。
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
from datetime import date
from typing import Optional
from urllib.parse import urlsplit

import boto3
import httpx
from PIL import Image

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# R2 配置（仅从环境变量读取，缺失时使用安全默认值）
# ---------------------------------------------------------------------------
R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET = os.environ.get("R2_BUCKET", "plant-images")
R2_PUBLIC_BASE = os.environ.get("R2_PUBLIC_BASE", "https://img.feijibei.top")
R2_ENDPOINT_URL = os.environ.get(
    "R2_ENDPOINT_URL",
    f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
)


def s3_client():
    """构造一个指向 Cloudflare R2 的 S3 client（region_name 固定为 auto）。"""
    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT_URL,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def norm_url(u: Optional[str]) -> Optional[str]:
    """规整 URL。

    - 空值（None / 空串 / 纯空白）返回 None；
    - scheme 为 http 则升级为 https（R2 公开域名只认 https）；
    - 其余原样返回。
    """
    if not u:
        return None
    u = str(u).strip()
    if not u:
        return None
    try:
        parts = urlsplit(u)
    except Exception:  # noqa: BLE001 - 解析失败视为无效 URL
        return None
    if parts.scheme == "http":
        return "https://" + u[len("http://"):]
    return u


def download_bytes(url: str) -> bytes:
    """下载 URL 内容为字节，带 User-Agent / Referer 对抗防盗链。

    失败（网络错误、4xx/5xx）直接抛异常，由上层 cache_one 兜底。
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.bilibili.com/",
        "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8",
    }
    resp = httpx.get(url, headers=headers, follow_redirects=True, timeout=15)
    resp.raise_for_status()
    return resp.content


def to_webp(data: bytes, max_edge: int = 800, quality: int = 82) -> bytes:
    """把图片字节转成 WebP 字节；最长边超过 max_edge 则等比缩略。

    统一转 RGB（规避 PNG 带 alpha / 调色板等模式），保证 WebP 编码稳定。
    """
    img = Image.open(io.BytesIO(data))
    img = img.convert("RGB")
    if max(img.size) > max_edge:
        img.thumbnail((max_edge, max_edge))
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=quality)
    return buf.getvalue()


def object_key(issue: str, url: str) -> str:
    """生成 R2 对象 key：daily/<issue>/<md5(url)前12位>.webp。"""
    digest = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
    return f"daily/{issue}/{digest}.webp"


def cache_one(cover: Optional[str], issue: str) -> Optional[str]:
    """缓存单条封面，返回 R2 永久 URL；任何异常都告警并返回原始 cover。

    失败场景：
    - cover 为空 / None / 非法 → 直接返回原值；
    - 下载失败 / 转码失败 / 上传失败 → logging.warning 并返回原值。
    """
    norm = norm_url(cover)
    if norm is None:
        return cover
    try:
        raw = download_bytes(norm)
        webp_bytes = to_webp(raw)
        key = object_key(issue, norm)
        s3_client().put_object(
            Bucket=R2_BUCKET,
            Key=key,
            Body=webp_bytes,
            ContentType="image/webp",
            CacheControl="public, max-age=31536000",
        )
        return f"{R2_PUBLIC_BASE}/{key}"
    except Exception as exc:  # noqa: BLE001 - 单条失败不应中断整期
        logger.warning("R2 缓存封面失败，保留原链接 %s: %s", cover, exc)
        return cover


def iso_week_issue(d: Optional[date] = None) -> str:
    """ISO 周标签 YYYY-Www（如 2026-W29），自带避免与 run_pipeline 循环 import。"""
    d = d or date.today()
    return d.strftime("%G-W%V")


def cache_covers(items: list, cfg: Optional[dict] = None) -> list:
    """改写每条 item 的 cover 为 R2 永久 URL，并返回 items。

    issue 优先取 cfg["issue"]，否则用 iso_week_issue() 自动生成。
    """
    cfg = cfg or {}
    issue = cfg.get("issue") or iso_week_issue()
    for it in items:
        if isinstance(it, dict) and "cover" in it:
            it["cover"] = cache_one(it.get("cover"), issue)
    return items
