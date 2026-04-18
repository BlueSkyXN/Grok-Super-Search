"""
Grok 搜索核心 — SSE 流解析 + webSearchResults 提取。

核心逻辑来自两个来源：
1. grok2api 的 xai_chat.py（classify_line + StreamAdapter.feed）
2. GrokHelper.js 的 gatherSearchResults（webSearchResults 提取路径）

本模块只关心搜索结果，无视 AI 的文字回答。
"""

import logging
import re
from typing import Any

import orjson

from .config import get_settings
from .http_client import grok_stream_request, GrokUpstreamError
from .token_pool import TokenSlot

logger = logging.getLogger("grok_search")

# Grok 新建对话端点
GROK_CHAT_URL = "https://grok.com/rest/app-chat/conversations/new"

# 搜索模式映射
MODE_MAP = {
    "auto": "auto",
    "fast": "fast",
    "expert": "expert",
    "heavy": "heavy",
    "deepsearch": "expert",
    "deepersearch": "expert",
}


# ═══════════════════════════════════════════════════════════════════
# Payload 构造（提取自 grok2api: xai_chat.py → build_chat_payload）
# ═══════════════════════════════════════════════════════════════════


def build_search_message(query: str) -> str:
    """用预设提示词模板拼装搜索消息"""
    settings = get_settings()
    return settings.search_prompt_template.format(query=query)


def build_chat_payload(message: str, mode: str = "auto") -> dict[str, Any]:
    """
    构造 Grok 对话请求体。

    核心参数对齐 grok2api 的 build_chat_payload，
    关键差异：disableSearch=False 确保触发网页搜索。
    """
    settings = get_settings()
    mode_id = MODE_MAP.get(mode.lower(), "auto")

    return {
        "message": message,
        "modeId": mode_id,
        "temporary": settings.temporary,
        "disableSearch": False,         # 核心：启用搜索
        "disableMemory": True,          # 搜索 API 不需要记忆
        "disableTextFollowUps": True,   # 不需要后续推荐
        "disableSelfHarmShortCircuit": False,
        "enableImageGeneration": False,
        "enableImageStreaming": False,
        "enableSideBySide": False,
        "isAsyncChat": False,
        "returnImageBytes": False,
        "returnRawGrokInXaiRequest": False,
        "sendFinalMetadata": True,
        "forceConcise": True,           # 搜索场景优先简洁
        "forceSideBySide": False,
        "searchAllConnectors": False,
        "imageGenerationCount": 0,
        "fileAttachments": [],
        "imageAttachments": [],
        "collectionIds": [],
        "connectors": [],
        "responseMetadata": {},
        "toolOverrides": {
            "gmailSearch": False,
            "googleCalendarSearch": False,
            "outlookSearch": False,
            "outlookCalendarSearch": False,
            "googleDriveSearch": False,
        },
        "deviceEnvInfo": {
            "darkModeEnabled": False,
            "devicePixelRatio": 2,
            "screenHeight": 1329,
            "screenWidth": 2056,
            "viewportHeight": 1083,
            "viewportWidth": 2056,
        },
    }


# ═══════════════════════════════════════════════════════════════════
# SSE 行分类（提取自 grok2api: xai_chat.py → classify_line）
# ═══════════════════════════════════════════════════════════════════


def classify_sse_line(line: str) -> tuple[str, str]:
    """
    分类 SSE 行。
    返回: (event_type, data_str)
    - "data" + JSON 字符串
    - "done" + ""
    - "skip" + ""
    """
    stripped = line.strip()
    if not stripped:
        return "skip", ""
    if stripped.startswith("data:"):
        data = stripped[5:].strip()
        if data == "[DONE]":
            return "done", ""
        return "data", data
    if stripped.startswith("event:"):
        return "skip", ""
    # grok2api: 有些帧直接返回 JSON（无 data: 前缀）
    if stripped.startswith("{"):
        return "data", stripped
    return "skip", ""


# ═══════════════════════════════════════════════════════════════════
# 搜索结果提取（结合 grok2api StreamAdapter + GrokHelper.js）
# ═══════════════════════════════════════════════════════════════════


def extract_from_frame(data_str: str) -> dict[str, Any]:
    """
    从单个 SSE 数据帧中提取搜索相关信息。

    对齐 grok2api 的 StreamAdapter.feed() 逻辑，
    同时参考 GrokHelper.js 中 webSearchResults 的提取路径。

    返回:
        {
            "web_search_results": [...],  # 搜索结果
            "tool_calls": [...],          # 工具调用（web_search 查询词等）
            "is_final": bool,             # 是否流结束
        }
    """
    try:
        obj = orjson.loads(data_str)
    except (orjson.JSONDecodeError, ValueError, TypeError):
        return {"web_search_results": [], "tool_calls": [], "is_final": False}

    result = obj.get("result")
    if not result:
        return {"web_search_results": [], "tool_calls": [], "is_final": False}

    resp = result.get("response")
    if not resp:
        return {"web_search_results": [], "tool_calls": [], "is_final": False}

    web_results: list[dict] = []
    tool_calls: list[dict] = []
    is_final = False

    # ── webSearchResults（核心搜索数据）──
    # 路径 1: resp.webSearchResults（直接，对齐 grok2api）
    wsr = resp.get("webSearchResults")
    if wsr and isinstance(wsr, list):
        web_results = wsr

    # 路径 2: resp.message.webSearchResults（GrokHelper.js 的备选路径）
    if not web_results:
        msg = resp.get("message")
        if msg and isinstance(msg, dict):
            wsr2 = msg.get("webSearchResults")
            if wsr2 and isinstance(wsr2, list):
                web_results = wsr2

    # ── 工具调用信息（提取搜索查询词）──
    tag = resp.get("messageTag")
    if tag == "tool_usage_card":
        card = resp.get("toolUsageCard")
        if card and isinstance(card, dict):
            for key, value in card.items():
                if key == "toolUsageCardId" or not isinstance(value, dict):
                    continue
                # camelCase → snake_case（同 grok2api）
                tool_name = re.sub(r"(?<!^)([A-Z])", r"_\1", key).lower()
                args = value.get("args", {})
                tool_calls.append({"tool": tool_name, "args": args})

    # ── 流结束信号 ──
    if resp.get("isSoftStop") or resp.get("finalMetadata"):
        is_final = True

    return {
        "web_search_results": web_results,
        "tool_calls": tool_calls,
        "is_final": is_final,
    }


# ═══════════════════════════════════════════════════════════════════
# 搜索执行器 — 完整流程
# ═══════════════════════════════════════════════════════════════════


async def execute_search(
    query: str,
    token_slot: TokenSlot,
    mode: str = "auto",
) -> dict[str, Any]:
    """
    执行一次 Grok 搜索。

    完整流程：
    1. 拼装提示词 + 构造 payload
    2. 向 Grok 发起 SSE 请求
    3. 逐帧解析，收集 webSearchResults
    4. 去重并返回结构化结果

    返回值只包含搜索结果，不包含 AI 文字回答
    （这是与 grok2api 的核心差异）。
    """
    message = build_search_message(query)
    payload = build_chat_payload(message, mode)
    payload_bytes = orjson.dumps(payload)

    all_results: list[dict] = []
    search_queries: list[str] = []
    seen_urls: set[str] = set()

    try:
        async for raw_line in grok_stream_request(
            GROK_CHAT_URL,
            token_slot.token,
            payload_bytes,
        ):
            event_type, data_str = classify_sse_line(raw_line)

            if event_type == "done":
                break
            if event_type != "data":
                continue

            frame = extract_from_frame(data_str)

            # 收集搜索结果（按 URL 去重）
            for item in frame["web_search_results"]:
                url = item.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(item)

            # 收集工具调用中的搜索查询词
            for tc in frame["tool_calls"]:
                if tc["tool"] == "web_search":
                    q = tc["args"].get("query") or tc["args"].get("q", "")
                    if q and q not in search_queries:
                        search_queries.append(q)

            if frame["is_final"]:
                break

    except GrokUpstreamError:
        raise
    except Exception as e:
        logger.error("search stream processing error: %s", e)
        raise

    return {
        "query": query,
        "mode": mode,
        "search_queries": search_queries,
        "search_results": all_results,
        "total_results": len(all_results),
        "total_search_queries": len(search_queries),
    }
