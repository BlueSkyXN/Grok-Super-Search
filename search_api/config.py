"""
配置管理 — 环境变量 + .env 文件，无数据库依赖。

参考 SouWen 的 config.py 模式：纯环境变量驱动，支持 .env 文件。
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    """全局配置（只从环境变量读取，无数据库依赖）"""

    # ── 服务 ──
    host: str = "0.0.0.0"
    port: int = 8000
    api_key: str = ""               # Bearer 鉴权密钥，空则不鉴权

    # ── Grok 账号池 ──
    # 逗号分隔的 SSO Token 列表
    sso_tokens: list[str] = field(default_factory=list)

    # ── Cloudflare ──
    cf_clearance: str = ""
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    )

    # ── 搜索行为 ──
    default_mode: str = "auto"      # auto / fast / expert
    temporary: bool = True          # 临时对话（不保存历史）
    timeout: float = 120.0          # SSE 流超时（秒）
    cooldown: float = 3.0           # Token 冷却时间（秒）

    # ── 网络 ──
    proxy_url: str = ""             # HTTP/SOCKS5 代理
    http_backend: str = "httpx"     # httpx / curl_cffi

    # ── 提示词 ──
    # 预设提示词模板，{query} 会被替换为搜索关键词
    search_prompt_template: str = (
        "请搜索以下内容并给出详细的搜索结果：\n\n{query}"
    )

    @classmethod
    def from_env(cls) -> "Settings":
        raw_tokens = os.getenv("GROK_SSO_TOKENS", "")
        tokens = [t.strip() for t in raw_tokens.split(",") if t.strip()]

        return cls(
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "8000")),
            api_key=os.getenv("API_KEY", ""),
            sso_tokens=tokens,
            cf_clearance=os.getenv("CF_CLEARANCE", ""),
            user_agent=os.getenv(
                "USER_AGENT",
                cls.user_agent,
            ),
            default_mode=os.getenv("DEFAULT_MODE", "auto"),
            temporary=os.getenv("TEMPORARY", "true").lower() in ("true", "1", "yes"),
            timeout=float(os.getenv("TIMEOUT", "120")),
            cooldown=float(os.getenv("COOLDOWN", "3")),
            proxy_url=os.getenv("PROXY_URL", ""),
            http_backend=os.getenv("HTTP_BACKEND", "httpx"),
            search_prompt_template=os.getenv(
                "SEARCH_PROMPT_TEMPLATE",
                cls.search_prompt_template,
            ),
        )


# 全局单例
_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings
