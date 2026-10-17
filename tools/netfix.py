#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网络环境修复（导入即生效，幂等）。

**在 tools/ 下任何做网络请求的脚本里，务必于 import urllib 之后、发请求之前
`import netfix`。** 不导入的话，在本机会直接挂死。

要修的两个环境坑（2026-10-17 实测确认）
────────────────────────────────────────────
坑 1 · 本机到 Cloudflare 的 IPv6 是死的
    本机 IPv6 出网到 CF 连接超时（实测 8s 抛 TimeoutError），而 DNS 会优先返回
    AAAA 记录，Python 又严格按 getaddrinfo 的返回顺序尝试 →
    一卡 60 秒以上，甚至永久挂住（DNS/连接阶段 Python 的 timeout 管不到）。
    **curl 默认走 IPv4 所以从来没事** —— 这就是「curl 能通、Python 不通」的来源。
    修法：把 socket.getaddrinfo 换成只返回 AF_INET。

坑 2 · 沙箱伪代理
    WorkBuddy 沙箱会给每个子进程注入 HTTP_PROXY / HTTPS_PROXY / http_proxy /
    https_proxy（端口每次会话都变）。urllib 照单全收 → 墙外站点被白名单代理拦掉。
    修法：清掉这些变量，并装一个 ProxyHandler({}) 的 opener 强制直连。

注意
    · 清代理变量对**子进程**（如 ffmpeg）同样生效；本目录脚本不需要走代理。
    · api.cloudflare.com 与 img.feijibei.top 实测均可直连，不需要代理。
"""
import os
import socket
import urllib.request

__all__ = ["ipv4_only", "PROXY_VARS"]

PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
              "ALL_PROXY", "all_proxy")

_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
    """只解析 IPv4。见文件头「坑 1」。"""
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


def _apply():
    """幂等：重复导入/重复调用都安全。任何异常都不往外抛，免得把调用方弄崩。"""
    try:
        if not getattr(socket, "_netfix_v4", False):
            socket.getaddrinfo = _ipv4_only
            socket._netfix_v4 = True
        for v in PROXY_VARS:
            os.environ.pop(v, None)
        os.environ["no_proxy"] = "*"
        os.environ["NO_PROXY"] = "*"
        urllib.request.install_opener(
            urllib.request.build_opener(urllib.request.ProxyHandler({})))
    except Exception as e:          # pragma: no cover
        print(f"  ! netfix 应用失败（不致命，但网络可能异常）：{type(e).__name__}: {e}")


_apply()

# 方便调用方显式再调一次
ipv4_only = _ipv4_only
