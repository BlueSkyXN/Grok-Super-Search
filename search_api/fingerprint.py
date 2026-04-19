"""
浏览器指纹管理 — 参考 SouWen 的 fingerprint.py。

提供真实浏览器请求头和 TLS 指纹模拟，用于突破反爬检测（JA3、UA、Sec-CH-UA 等）。
支持多个浏览器版本和操作系统组合，增加指纹多样性。

在 curl_cffi 后端中，impersonate 参数控制 TLS 握手指纹；
在 httpx 后端中，仅使用 headers 伪装。
"""

from __future__ import annotations

import random

# Chrome / Edge / Safari 浏览器指纹库（定期更新）
# 注意：curl_cffi 最高仅支持 chrome124 的 TLS 指纹，UA 用新版无碍
_CHROME_VERSIONS: list[dict[str, str]] = [
    {
        "version": "136",
        "ua": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/136.0.0.0 Safari/537.36"
        ),
        "sec_ch_ua": '"Chromium";v="136", "Not-A.Brand";v="24", "Google Chrome";v="136"',
        "impersonate": "chrome124",
    },
    {
        "version": "135",
        "ua": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/135.0.0.0 Safari/537.36"
        ),
        "sec_ch_ua": '"Chromium";v="135", "Not-A.Brand";v="24", "Google Chrome";v="135"',
        "impersonate": "chrome124",
    },
    {
        "version": "133",
        "ua": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/133.0.0.0 Safari/537.36"
        ),
        "sec_ch_ua": '"Chromium";v="133", "Not-A.Brand";v="24", "Google Chrome";v="133"',
        "impersonate": "chrome124",
    },
    {
        "version": "131",
        "ua": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "sec_ch_ua": '"Chromium";v="131", "Not-A.Brand";v="24", "Google Chrome";v="131"',
        "impersonate": "chrome124",
    },
    {
        "version": "125",
        "ua": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "sec_ch_ua": '"Chromium";v="125", "Not-A.Brand";v="24", "Google Chrome";v="125"',
        "impersonate": "chrome120",
    },
    {
        "version": "124",
        "ua": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "sec_ch_ua": '"Chromium";v="124", "Not-A.Brand";v="24", "Google Chrome";v="124"',
        "impersonate": "chrome120",
    },
    # Edge (Chromium-based) — 复用 chrome124 TLS 指纹
    {
        "version": "edge137",
        "ua": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/137.0.0.0 Safari/537.36 Edg/137.0.0.0"
        ),
        "sec_ch_ua": '"Chromium";v="137", "Not-A.Brand";v="24", "Microsoft Edge";v="137"',
        "impersonate": "chrome124",
    },
    # Safari (macOS) — 使用 safari 系列 TLS 指纹
    {
        "version": "safari17",
        "ua": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/17.5 Safari/605.1.15"
        ),
        "sec_ch_ua": "",
        "impersonate": "safari17_0",
    },
]

# 操作系统指纹（与浏览器版本随机组合，增加指纹多样性）
_PLATFORMS: list[dict[str, str]] = [
    {"platform": '"Windows"', "ua_os": "Windows NT 10.0; Win64; x64"},
    {"platform": '"macOS"', "ua_os": "Macintosh; Intel Mac OS X 10_15_7"},
    {"platform": '"Linux"', "ua_os": "X11; Linux x86_64"},
]


class BrowserFingerprint:
    """浏览器指纹生成器 — 参考 SouWen 的同名类。

    为单次请求生成一致的浏览器指纹（UA + Sec-CH-UA + TLS impersonate），
    确保请求在网络层和应用层看起来像真实浏览器。
    """

    def __init__(self, chrome_version: dict[str, str] | None = None) -> None:
        self._chrome = chrome_version or random.choice(_CHROME_VERSIONS)
        self._platform = random.choice(_PLATFORMS)

    @property
    def user_agent(self) -> str:
        """完整 User-Agent 字符串"""
        return self._chrome["ua"]

    @property
    def impersonate(self) -> str:
        """curl_cffi impersonate 参数（TLS 指纹模拟）"""
        return self._chrome["impersonate"]

    @property
    def sec_ch_ua(self) -> str:
        """Sec-CH-UA 请求头值"""
        return self._chrome["sec_ch_ua"]

    @property
    def headers(self) -> dict[str, str]:
        """完整的浏览器请求头（含 Sec-CH-UA 系列）"""
        h: dict[str, str] = {
            "User-Agent": self._chrome["ua"],
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": self._platform["platform"],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }
        if self._chrome["sec_ch_ua"]:
            h["sec-ch-ua"] = self._chrome["sec_ch_ua"]
        return h

    def rotate(self) -> "BrowserFingerprint":
        """轮换到新的指纹（重新随机选择浏览器和操作系统）"""
        return BrowserFingerprint()


def get_default_fingerprint() -> BrowserFingerprint:
    """获取确定的默认浏览器指纹（Chrome 最新稳定版）"""
    return BrowserFingerprint(_CHROME_VERSIONS[0])


def get_random_fingerprint() -> BrowserFingerprint:
    """获取随机浏览器指纹"""
    return BrowserFingerprint()
