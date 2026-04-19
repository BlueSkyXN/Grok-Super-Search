"""
入口脚本 — 对齐 SouWen 的 __main__.py 模式。

用法:
    python -m search_api
    # 或
    python search_api/main.py
"""

import uvicorn
from .config import get_settings


def main():
    settings = get_settings()
    uvicorn.run(
        "search_api.app:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
