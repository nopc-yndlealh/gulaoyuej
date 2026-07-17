#!/usr/bin/env python3
"""image_cache 离线测试（纯 mock，不连真实网络 / R2）。

在仓库根运行：python -m pytest tests/ -q
"""
from __future__ import annotations

import hashlib
import io
import sys
from pathlib import Path
from unittest.mock import MagicMock

import httpx
from PIL import Image

# 把仓库根加入 sys.path，确保能从任意工作目录 import 到 daily.image_cache
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from daily import image_cache  # noqa: E402


def _fake_png(width: int = 1200, height: int = 800) -> bytes:
    """生成一张真实 PNG 字节（用于模拟下载结果，to_webp 可正常处理）。"""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 120, 30)).save(buf, format="PNG")
    return buf.getvalue()


class _FakeResp:
    """模拟 httpx 响应：content 为图片字节，raise_for_status 不抛错。"""

    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


def _patch_network(monkeypatch, png_bytes: bytes) -> MagicMock:
    """同时 mock httpx.get 与 boto3.client，返回捕获 put_object 的 client。"""
    monkeypatch.setattr(
        image_cache.httpx, "get",
        lambda url, **kwargs: _FakeResp(png_bytes),
    )
    fake_client = MagicMock()
    monkeypatch.setattr(image_cache.boto3, "client", lambda *a, **k: fake_client)
    return fake_client


# ----------------------------- norm_url -----------------------------
def test_norm_url_empty_returns_none():
    assert image_cache.norm_url("") is None
    assert image_cache.norm_url(None) is None
    assert image_cache.norm_url("   ") is None


def test_norm_url_http_upgraded_to_https():
    assert image_cache.norm_url("http://a.com/x.png") == "https://a.com/x.png"
    assert image_cache.norm_url("https://a.com/x.png") == "https://a.com/x.png"


# --------------------------- object_key -----------------------------
def test_object_key_format():
    key = image_cache.object_key("2026-W29", "https://a.com/x.png")
    digest = hashlib.md5("https://a.com/x.png".encode("utf-8")).hexdigest()[:12]
    assert key == f"daily/2026-W29/{digest}.webp"
    assert key.endswith(".webp")


# ----------------------------- to_webp ------------------------------
def test_to_webp_produces_webp_bytes():
    out = image_cache.to_webp(_fake_png(), max_edge=800, quality=82)
    assert isinstance(out, bytes) and len(out) > 0
    # 能被 PIL 读回且格式为 WEBP
    with Image.open(io.BytesIO(out)) as img:
        assert img.format == "WEBP"
        # 1200x800 超过 max_edge=800 → 等比缩略到 800x533
        assert max(img.size) <= 800


# ---------------------------- cache_one -----------------------------
def test_cache_one_success(monkeypatch):
    png = _fake_png()
    fake_client = _patch_network(monkeypatch, png)
    out = image_cache.cache_one("http://a.com/x.png", "2026-W29")

    expected = (
        f"{image_cache.R2_PUBLIC_BASE}/"
        f"{image_cache.object_key('2026-W29', 'https://a.com/x.png')}"
    )
    assert out == expected
    assert fake_client.put_object.called
    _, kwargs = fake_client.put_object.call_args
    assert kwargs["ContentType"] == "image/webp"
    assert kwargs["CacheControl"] == "public, max-age=31536000"
    assert kwargs["Bucket"] == image_cache.R2_BUCKET
    assert isinstance(kwargs["Body"], bytes)


def test_cache_one_download_failure_returns_original(monkeypatch):
    def _boom(url, **kwargs):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(image_cache.httpx, "get", _boom)
    original = "https://a.com/x.png"
    assert image_cache.cache_one(original, "2026-W29") == original


def test_cache_one_empty_cover_returns_original():
    assert image_cache.cache_one("", "2026-W29") == ""
    assert image_cache.cache_one(None, "2026-W29") is None


# --------------------------- cache_covers ---------------------------
def test_cache_covers_iterates_items(monkeypatch):
    png = _fake_png()
    _patch_network(monkeypatch, png)
    items = [
        {"cover": "http://a.com/x.png", "title": "t1"},
        {"cover": "", "title": "t2"},
        {"cover": None, "title": "t3"},
        {"cover": "https://b.com/y.jpg", "title": "t4"},
    ]
    cfg = {"issue": "2026-W29"}
    out = image_cache.cache_covers(items, cfg)

    assert out[0]["cover"].endswith(".webp")   # 有效 http → 已缓存
    assert out[1]["cover"] == ""               # 空串 → 原值
    assert out[2]["cover"] is None             # None → 原值
    assert out[3]["cover"].endswith(".webp")   # 有效 https → 已缓存


def test_cache_covers_falls_back_to_iso_week(monkeypatch):
    png = _fake_png()
    _patch_network(monkeypatch, png)
    items = [{"cover": "http://a.com/x.png"}]
    # 不提供 cfg["issue"] 时自动用 iso_week_issue()
    out = image_cache.cache_covers(items, {})
    issue = image_cache.iso_week_issue()
    assert out[0]["cover"].startswith(
        f"{image_cache.R2_PUBLIC_BASE}/daily/{issue}/"
    )
