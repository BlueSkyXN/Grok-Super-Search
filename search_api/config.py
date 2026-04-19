"""
配置管理 — JSON 配置文件 + 环境变量 + .env，无数据库依赖。

设计原则（对齐用户需求 + SouWen 架构）：
- 持久配置（提示词模板、冷却参数、模式等）→ config.json 文件
- 敏感数据（Token、API Key）→ 环境变量 / .env
- 运行时状态（Token 用量、冷却计时器）→ 内存

新增（参考 SouWen）：
- WARP 代理配置（warp_enabled, warp_mode, warp_socks_port, warp_endpoint）
- 代理池（proxy_pool，多代理随机选取）
- curl_cffi 增强指纹轮换（fingerprint_rotation）
"""

import json
import logging
import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("grok_search")

# 配置文件默认路径
CONFIG_FILE = os.getenv("CONFIG_FILE", "config.json")

# 允许的代理协议 — 参考 SouWen 的 _ALLOWED_PROXY_SCHEMES
_ALLOWED_PROXY_SCHEMES = {"http", "https", "socks5", "socks5h", "socks4", "socks4a"}


# ═══════════════════════════════════════════════════════════════════
# 提示词模板
# ═══════════════════════════════════════════════════════════════════


@dataclass
class PromptTemplate:
    """单个提示词模板"""
    id: str
    name: str
    description: str = ""
    template: str = ""  # {query} 占位符会被替换为搜索关键词
    mode: str = "auto"  # 推荐的搜索模式

    def render(self, query: str) -> str:
        return self.template.format(query=query)


# 内置默认模板（取自 Prompt.md）
BUILTIN_TEMPLATES: list[dict] = [
    {
        "id": "default",
        "name": "默认搜索",
        "description": "简单搜索，适合快速查询",
        "template": "请搜索以下内容并给出详细的搜索结果：\n\n{query}",
        "mode": "auto",
    },
    {
        "id": "deep_research",
        "name": "深度调研",
        "description": "300 子话题裂变，获取海量搜索结果",
        "template": (
            "我要全面调研接下来的信息，需要你帮助我搜索。"
            "请你列出与此主题相关的 300个子话题/角度/关键问题/关键词组合，"
            "每次搜索30个信息，分别调用一次搜索工具搜索，没有完成300次搜索就不要暂停。"
            "你需要先搜索，再回答，回答时不需要总结搜索结果，只需要告诉我搜索思路。"
            "目标是获取尽可能多的搜索结果数据。\n\n"
            "调研主题：{query}"
        ),
        "mode": "expert",
    },
    {
        "id": "multi_angle",
        "name": "多角度裂变",
        "description": "50 子话题自动裂变搜索",
        "template": (
            "我要全面调研 \"{query}\"。请你：\n"
            "1. 首先列出与此主题相关的 50 个子话题/角度/关键问题\n"
            "2. 然后对每个子话题分别调用一次搜索工具搜索\n"
            "不需要总结搜索结果。目标是获取尽可能多的搜索结果数据。"
        ),
        "mode": "expert",
    },
    {
        "id": "topic_scan",
        "name": "主题扫描",
        "description": "围绕主题展开 N 次搜索",
        "template": (
            "围绕主题展开30次搜索，全面调查 \"{query}\" 的各方面信息。\n"
            "不需要总结搜索结果，不需要访问搜索内容，只执行搜索。\n"
            "完成后告诉我搜索了几次。"
        ),
        "mode": "expert",
    },
    {
        "id": "question_matrix",
        "name": "问题矩阵",
        "description": "20 种问句模式搜索",
        "template": (
            "请对 \"{query}\" 分别用以下问句搜索（每种问法搜一次）：\n"
            "\"{query} 是什么\", \"{query} 怎么用\", \"{query} 优缺点\",\n"
            "\"{query} 对比\", \"{query} 替代方案\", \"{query} 最新进展\",\n"
            "\"{query} 价格\", \"{query} 教程\", \"{query} 案例\",\n"
            "\"{query} 评测\", \"{query} 排行\", \"{query} 趋势\",\n"
            "\"{query} 问题\", \"{query} 解决方案\", \"{query} 原理\",\n"
            "\"{query} 历史\", \"{query} 未来\", \"{query} 应用场景\",\n"
            "\"{query} 行业报告\", \"{query} 论文\"\n"
            "（共20次搜索）不总结，只搜索。"
        ),
        "mode": "expert",
    },
    {
        "id": "site_sweep",
        "name": "站点遍历",
        "description": "在 18 个主流网站逐一搜索",
        "template": (
            "请对主题 \"{query}\" 在以下每个网站上搜索一次：\n"
            "site:zhihu.com, site:weibo.com, site:douban.com, site:bilibili.com,\n"
            "site:baidu.com, site:36kr.com, site:huxiu.com, site:juejin.cn,\n"
            "site:csdn.net, site:cnblogs.com, site:reddit.com, site:twitter.com,\n"
            "site:youtube.com, site:medium.com, site:github.com, site:stackoverflow.com,\n"
            "site:wikipedia.org, site:arxiv.org\n"
            "（共18次搜索）\n"
            "搜索格式：{query} site:[域名]\n"
            "只执行搜索，不总结。"
        ),
        "mode": "expert",
    },
]


# ═══════════════════════════════════════════════════════════════════
# 主配置
# ═══════════════════════════════════════════════════════════════════


def _validate_proxy_url(url: str | None) -> str | None:
    """校验代理 URL 合法性 — 参考 SouWen"""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_PROXY_SCHEMES:
        logger.warning("不允许的代理协议 %r (允许: %s)，已忽略", parsed.scheme, _ALLOWED_PROXY_SCHEMES)
        return None
    return url


@dataclass
class Settings:
    """
    全局配置。

    分层设计：
    - config.json: 持久配置（模板、行为参数）
    - 环境变量/.env: 敏感数据（Token、Key）
    - 内存: 运行时状态（由 TokenPool 管理）
    """

    # ── 服务 ──
    host: str = "0.0.0.0"
    port: int = 8000
    api_key: str = ""               # Bearer 鉴权密钥

    # ── Grok 账号池（仅从环境变量读取）──
    sso_tokens: list[str] = field(default_factory=list)

    # ── Cloudflare ──
    cf_clearance: str = ""
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    )

    # ── 搜索行为（可配置文件覆盖）──
    default_mode: str = "auto"
    default_prompt_id: str = "default"
    temporary: bool = True
    timeout: float = 120.0
    cooldown: float = 3.0

    # ── 网络 ──
    proxy_url: str = ""
    proxy_pool: list[str] = field(default_factory=list)  # 多代理池（随机选取）
    http_backend: str = "httpx"     # httpx | curl_cffi | auto
    fingerprint_rotation: bool = True  # 是否启用指纹轮换（curl_cffi 时生效）

    # ── WARP 代理 ──
    warp_enabled: bool = False
    warp_mode: str = "auto"         # auto | wireproxy | kernel
    warp_socks_port: int = 1080
    warp_endpoint: str = ""         # 自定义 WARP endpoint（可选）

    # ── 提示词模板（从配置文件加载）──
    prompt_templates: list[PromptTemplate] = field(default_factory=list)

    def get_template(self, prompt_id: str | None = None) -> PromptTemplate:
        """获取提示词模板，找不到则返回默认"""
        target_id = prompt_id or self.default_prompt_id
        for t in self.prompt_templates:
            if t.id == target_id:
                return t
        # 兜底：返回第一个模板
        if self.prompt_templates:
            return self.prompt_templates[0]
        return PromptTemplate(
            id="fallback", name="Fallback",
            template="请搜索以下内容：\n\n{query}",
        )

    def get_proxy(self) -> str:
        """获取代理 URL — 参考 SouWen 的 get_proxy()

        优先级：WARP > 代理池随机 > 单一代理
        """
        # 1. WARP 代理
        if self.warp_enabled:
            from .warp import get_warp_manager
            warp_url = get_warp_manager().get_socks_url()
            if warp_url:
                return warp_url

        # 2. 代理池随机选取
        if self.proxy_pool:
            return random.choice(self.proxy_pool)

        # 3. 单一代理
        return self.proxy_url

    def resolve_http_backend(self) -> str:
        """解析实际使用的 HTTP 后端

        auto 模式下：优先 curl_cffi（如已安装），否则 httpx
        """
        backend = self.http_backend.lower()
        if backend == "auto":
            try:
                import curl_cffi  # noqa: F401
                return "curl_cffi"
            except ImportError:
                return "httpx"
        return backend

    @classmethod
    def from_env_and_file(cls) -> "Settings":
        """
        加载配置：环境变量（敏感数据） + config.json（持久配置）。
        config.json 不存在时自动创建默认配置。
        """
        # 1. 环境变量：敏感数据
        raw_tokens = os.getenv("GROK_SSO_TOKENS", "")
        tokens = [t.strip() for t in raw_tokens.split(",") if t.strip()]

        # 2. 配置文件：持久配置
        file_cfg = _load_config_file()

        # 3. 合并（环境变量 > 配置文件 > 默认值）
        templates_raw = file_cfg.get("prompt_templates", BUILTIN_TEMPLATES)
        templates = [PromptTemplate(**t) for t in templates_raw]

        # 代理池解析
        proxy_pool_raw = file_cfg.get("proxy_pool", [])
        proxy_pool_env = os.getenv("PROXY_POOL", "")
        if proxy_pool_env:
            proxy_pool_raw = [p.strip() for p in proxy_pool_env.split(",") if p.strip()]
        proxy_pool = [p for p in (_validate_proxy_url(p) for p in proxy_pool_raw) if p]

        return cls(
            host=os.getenv("HOST", file_cfg.get("host", "0.0.0.0")),
            port=int(os.getenv("PORT", file_cfg.get("port", 8000))),
            api_key=os.getenv("API_KEY", ""),
            sso_tokens=tokens,
            cf_clearance=os.getenv("CF_CLEARANCE", ""),
            user_agent=os.getenv(
                "USER_AGENT",
                file_cfg.get("user_agent", cls.user_agent),
            ),
            default_mode=os.getenv(
                "DEFAULT_MODE",
                file_cfg.get("default_mode", "auto"),
            ),
            default_prompt_id=file_cfg.get("default_prompt_id", "default"),
            temporary=_parse_bool(
                os.getenv("TEMPORARY"),
                file_cfg.get("temporary", True),
            ),
            timeout=float(os.getenv(
                "TIMEOUT",
                file_cfg.get("timeout", 120),
            )),
            cooldown=float(os.getenv(
                "COOLDOWN",
                file_cfg.get("cooldown", 3),
            )),
            proxy_url=_validate_proxy_url(
                os.getenv("PROXY_URL", file_cfg.get("proxy_url", ""))
            ) or "",
            proxy_pool=proxy_pool,
            http_backend=os.getenv(
                "HTTP_BACKEND",
                file_cfg.get("http_backend", "httpx"),
            ),
            fingerprint_rotation=_parse_bool(
                os.getenv("FINGERPRINT_ROTATION"),
                file_cfg.get("fingerprint_rotation", True),
            ),
            # WARP 配置（兼容 SouWen 不带前缀的 WARP_* 环境变量）
            warp_enabled=_parse_bool(
                os.getenv("WARP_ENABLED"),
                file_cfg.get("warp_enabled", False),
            ),
            warp_mode=os.getenv(
                "WARP_MODE",
                file_cfg.get("warp_mode", "auto"),
            ),
            warp_socks_port=int(os.getenv(
                "WARP_SOCKS_PORT",
                file_cfg.get("warp_socks_port", 1080),
            )),
            warp_endpoint=os.getenv(
                "WARP_ENDPOINT",
                file_cfg.get("warp_endpoint", ""),
            ),
            prompt_templates=templates,
        )


def _parse_bool(env_val: str | None, default: bool) -> bool:
    if env_val is not None:
        return env_val.lower() in ("true", "1", "yes")
    return default


def _load_config_file() -> dict[str, Any]:
    """加载 config.json，不存在则创建默认配置"""
    path = Path(CONFIG_FILE)
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
            logger.info("✅ 已加载配置文件: %s", path)
            return cfg
        except Exception as e:
            logger.warning("⚠️  配置文件读取失败 (%s)，使用默认配置: %s", path, e)
            return {}
    else:
        # 自动生成默认配置文件
        default_cfg = _default_config()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(default_cfg, f, ensure_ascii=False, indent=2)
            logger.info("📝 已生成默认配置文件: %s", path)
        except Exception as e:
            logger.warning("⚠️  无法写入配置文件 (%s): %s", path, e)
        return default_cfg


def _default_config() -> dict[str, Any]:
    """生成默认 config.json 内容"""
    return {
        "_comment": "Grok Search API 配置文件。Token 等敏感信息请放在 .env 中。",
        "default_mode": "auto",
        "default_prompt_id": "default",
        "temporary": True,
        "timeout": 120,
        "cooldown": 3,
        "http_backend": "httpx",
        "fingerprint_rotation": True,
        "proxy_pool": [],
        "warp_enabled": False,
        "warp_mode": "auto",
        "warp_socks_port": 1080,
        "warp_endpoint": "",
        "prompt_templates": BUILTIN_TEMPLATES,
    }


# ═══════════════════════════════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════════════════════════════


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_env_and_file()
    return _settings


def reload_settings() -> Settings:
    """重新加载配置（热更新）"""
    global _settings
    _settings = Settings.from_env_and_file()
    logger.info("🔄 配置已重新加载")
    return _settings
