# Grok Search API — 完整 API 文档

> **版本**: 2.0.0 | **协议**: REST / JSON | **兼容**: SouWen 搜索平台

本文档覆盖所有 API 端点的输入/输出语法、配置项、部署方式和 SouWen 集成指南。

---

## 目录

- [快速开始](#快速开始)
- [架构概览](#架构概览)
- [配置说明](#配置说明)
  - [环境变量](#环境变量)
  - [config.json](#configjson)
  - [HTTP 后端选择](#http-后端选择)
  - [代理配置](#代理配置)
  - [WARP 代理](#warp-代理)
  - [浏览器指纹轮换](#浏览器指纹轮换)
- [API 端点](#api-端点)
  - [搜索接口](#搜索接口)
  - [提示词模板](#提示词模板)
  - [管理接口](#管理接口)
  - [WARP 管理](#warp-管理)
- [响应格式](#响应格式)
- [错误处理](#错误处理)
- [SouWen 集成指南](#souwen-集成指南)
- [Docker 部署](#docker-部署)

---

## 快速开始

```bash
cd search_api
pip install -r requirements.txt

# 可选：安装 curl_cffi 以启用 TLS 指纹模拟
pip install curl_cffi

cp .env.example .env
# 编辑 .env，填入 Grok SSO Token

python -m search_api
# 首次运行会自动生成 config.json
# 访问 http://localhost:8000/docs 查看 Swagger 文档
```

---

## 架构概览

```
┌── 配置加载 ──────────────────────────────────┐
│ ENV (.env) → config.json → 内置默认值        │
│  (分层优先级: 环境变量 > 配置文件 > 默认值)  │
└──────────────────────┬───────────────────────┘
                       │
    ┌──────────────────┴──────────────────┐
    │                                     │
┌── HTTP 后端 ────────┐          ┌── 代理配置 ────────┐
│ curl_cffi            │          │ WARP SOCKS5        │
│  ├─ TLS 指纹伪装    │          │ 代理池（随机选取） │
│  ├─ JA3 指纹绕过    │          │ 单一代理           │
│  └─ 自动降级 httpx   │          └────────────────────┘
│ OR                   │
│ httpx（默认）        │
│  └─ 轻量可靠         │
└──────────────────────┘
    │
    ├── 浏览器指纹轮换（每次请求随机 UA + Sec-CH-UA + TLS）
    │
    ├── Token 号池（轮询 + 冷却 + 自动禁用）
    │
    └── 搜索核心（SSE 流解析 → webSearchResults 提取）

┌── WARP 管理器 ──────────────────────────┐
│ ├─ wireproxy（用户态，无需 root）       │
│ └─ kernel WireGuard + microsocks        │
│    → SOCKS5://127.0.0.1:1080            │
│    → 配置三级回退: B64 > 文件 > wgcf    │
└─────────────────────────────────────────┘
```

---

## 配置说明

### 环境变量

所有敏感数据通过环境变量（`.env` 文件）配置。

| 变量 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `GROK_SSO_TOKENS` | string | ✅ | - | SSO Token 列表（逗号分隔）|
| `API_KEY` | string | | 空 | API 鉴权密钥（Bearer Token）|
| `CF_CLEARANCE` | string | | 空 | Cloudflare cf_clearance Cookie |
| `USER_AGENT` | string | | Chrome/136 | 自定义 User-Agent |
| **代理** | | | | |
| `PROXY_URL` | string | | 空 | 单一代理 URL |
| `PROXY_POOL` | string | | 空 | 代理池（逗号分隔）|
| **HTTP 后端** | | | | |
| `HTTP_BACKEND` | string | | httpx | `httpx` / `curl_cffi` / `auto` |
| `FINGERPRINT_ROTATION` | bool | | true | 浏览器指纹轮换开关 |
| **WARP 代理** | | | | |
| `WARP_ENABLED` | bool | | false | 启用 Cloudflare WARP |
| `WARP_MODE` | string | | auto | `auto` / `wireproxy` / `kernel` |
| `WARP_SOCKS_PORT` | int | | 1080 | WARP SOCKS5 端口 |
| `WARP_ENDPOINT` | string | | 空 | 自定义 WARP endpoint |
| `WARP_CONFIG_B64` | string | | 空 | Base64 WireGuard 配置 |
| **服务** | | | | |
| `HOST` | string | | 0.0.0.0 | 监听地址 |
| `PORT` | int | | 8000 | 监听端口 |
| `CONFIG_FILE` | string | | config.json | 配置文件路径 |

### config.json

持久配置文件，首次运行自动生成。

```json
{
  "_comment": "Grok Search API 配置文件。Token 等敏感信息请放在 .env 中。",
  "default_mode": "auto",
  "default_prompt_id": "default",
  "temporary": true,
  "timeout": 120,
  "cooldown": 3,
  "http_backend": "httpx",
  "fingerprint_rotation": true,
  "proxy_pool": [],
  "warp_enabled": false,
  "warp_mode": "auto",
  "warp_socks_port": 1080,
  "warp_endpoint": "",
  "prompt_templates": [
    {
      "id": "default",
      "name": "默认搜索",
      "description": "简单搜索，适合快速查询",
      "template": "请搜索以下内容并给出详细的搜索结果：\n\n{query}",
      "mode": "auto"
    }
  ]
}
```

### HTTP 后端选择

| 值 | 说明 | 适用场景 |
|----|------|---------|
| `httpx` | 默认后端，轻量异步 | 通用场景，无需额外安装 |
| `curl_cffi` | TLS 指纹模拟 | 需要绕过 TLS 指纹检测时 |
| `auto` | 优先 curl_cffi，不可用则 httpx | 推荐：自动选择最佳后端 |

**curl_cffi 安装：**

```bash
pip install curl_cffi
```

安装后设置 `HTTP_BACKEND=auto` 或 `HTTP_BACKEND=curl_cffi` 即可启用。未安装时自动降级到 httpx，不会报错。

### 代理配置

代理优先级：**WARP > 代理池 > 单一代理**

```bash
# 方案一：单一代理
PROXY_URL=socks5://127.0.0.1:1080

# 方案二：代理池（每次请求随机选取）
PROXY_POOL=socks5://proxy1:1080,http://proxy2:7890,socks5h://proxy3:1080

# 方案三：WARP 代理（自动提供 SOCKS5）
WARP_ENABLED=true
WARP_MODE=auto

# 方案四：config.json 中配置代理池
# "proxy_pool": ["socks5://proxy1:1080", "http://proxy2:7890"]
```

支持的代理协议：`http`, `https`, `socks5`, `socks5h`, `socks4`, `socks4a`

### WARP 代理

Cloudflare WARP 提供免费的加密代理通道，支持两种运行模式：

| 模式 | 说明 | 要求 |
|------|------|------|
| `wireproxy` | 用户态 SOCKS5 | 安装 `wireproxy` 即可 |
| `kernel` | 内核 WireGuard + microsocks | `wg-quick` + `microsocks` + `NET_ADMIN` |
| `auto` | 自动检测最优 | kernel > wireproxy |

**配置来源（三级回退）：**

1. `WARP_CONFIG_B64` 环境变量（Base64 编码的 WireGuard 配置）
2. `/app/data/wireproxy.conf` 或 `/app/data/wg0.conf`（持久化文件）
3. 自动通过 `wgcf` 注册新 WARP 账号

**Docker 部署示例（启用 WARP kernel 模式）：**

```yaml
services:
  grok-search:
    build: ./search_api
    ports:
      - "8000:8000"
    cap_add:
      - NET_ADMIN
    devices:
      - /dev/net/tun:/dev/net/tun
    environment:
      GROK_SSO_TOKENS: "token1,token2"
      WARP_ENABLED: "true"
      WARP_MODE: "kernel"
```

### 浏览器指纹轮换

启用后（默认开启），每次请求随机选择浏览器指纹：

- **User-Agent**: 随机 Chrome/Edge/Safari 版本
- **Sec-CH-UA**: 匹配的 Client Hints 头
- **TLS 指纹** (curl_cffi): 对应的 impersonate 参数

指纹库包含 8+ 浏览器版本（Chrome 124-136、Edge 137、Safari 17）和 3 种操作系统。

---

## API 端点

### 搜索接口

#### `POST /v1/search` — 单次搜索

**请求体 (JSON):**

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `query` | string | ✅ | - | 搜索关键词（1-2000字符）|
| `mode` | string | | "auto" | 搜索模式 |
| `prompt_id` | string\|null | | null | 提示词模板 ID |

**搜索模式 (`mode`):**

| 值 | Grok 模式 | 说明 |
|----|----------|------|
| `auto` | auto | 自动选择 |
| `fast` | fast | 快速搜索 |
| `expert` | expert | 专家搜索（更深入）|
| `heavy` | heavy | 重量级搜索 |
| `deepsearch` | expert | 别名 |
| `deepersearch` | expert | 别名 |

**请求示例:**

```bash
curl -X POST http://localhost:8000/v1/search \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "query": "2024年最新的AI框架对比",
    "mode": "expert",
    "prompt_id": "deep_research"
  }'
```

**响应体 (JSON):**

```json
{
  "query": "2024年最新的AI框架对比",
  "mode": "expert",
  "prompt_id": "deep_research",
  "search_queries": [
    "latest AI frameworks 2024 comparison",
    "PyTorch vs TensorFlow 2024",
    "JAX performance benchmarks"
  ],
  "search_results": [
    {
      "title": "Top AI Frameworks in 2024",
      "url": "https://example.com/ai-frameworks-2024",
      "preview": "A comprehensive comparison of..."
    }
  ],
  "total_results": 150,
  "total_search_queries": 30,
  "error": null
}
```

**响应字段说明:**

| 字段 | 类型 | 说明 |
|------|------|------|
| `query` | string | 原始搜索关键词 |
| `mode` | string | 使用的搜索模式 |
| `prompt_id` | string | 使用的提示词模板 ID |
| `search_queries` | string[] | Grok 实际执行的搜索词列表 |
| `search_results` | object[] | 搜索结果数组（保留 Grok 原始字段）|
| `total_results` | int | 搜索结果总数 |
| `total_search_queries` | int | 搜索词总数 |
| `error` | string\|null | 错误信息（仅批量搜索部分失败时）|

---

#### `POST /v1/search/batch` — 批量搜索

**请求体 (JSON):**

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `queries` | string[] | ✅ | - | 搜索关键词列表（1-20个）|
| `mode` | string | | "auto" | 搜索模式 |
| `prompt_id` | string\|null | | null | 提示词模板 ID |
| `concurrency` | int | | 3 | 并发数（1-5）|

**请求示例:**

```bash
curl -X POST http://localhost:8000/v1/search/batch \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "queries": ["Rust vs Go 2024", "WebAssembly 现状", "RISC-V 生态"],
    "mode": "auto",
    "prompt_id": "topic_scan",
    "concurrency": 3
  }'
```

**响应体 (JSON):**

```json
{
  "batch_size": 3,
  "total_results": 450,
  "results": [
    {
      "query": "Rust vs Go 2024",
      "mode": "auto",
      "prompt_id": "topic_scan",
      "search_queries": ["..."],
      "search_results": [{...}],
      "total_results": 150,
      "total_search_queries": 30,
      "error": null
    }
  ]
}
```

---

### 提示词模板

#### `GET /v1/prompts` — 查看提示词模板

```bash
curl http://localhost:8000/v1/prompts
```

**响应体:**

```json
{
  "total": 6,
  "default_prompt_id": "default",
  "templates": [
    {
      "id": "default",
      "name": "默认搜索",
      "description": "简单搜索，适合快速查询",
      "mode": "auto",
      "template_preview": "请搜索以下内容并给出详细的搜索结果：\n\n{query}"
    },
    {
      "id": "deep_research",
      "name": "深度调研",
      "description": "300 子话题裂变，获取海量搜索结果",
      "mode": "expert",
      "template_preview": "我要全面调研接下来的信息，需要你帮助我搜索。请你列出与此主题相关的 300个子话题/角度/关键问题/关键词组合..."
    }
  ]
}
```

**内置模板:**

| ID | 名称 | 搜索次数 | 推荐模式 |
|----|------|---------|---------|
| `default` | 默认搜索 | 1 | auto |
| `deep_research` | 深度调研 | 300 | expert |
| `multi_angle` | 多角度裂变 | 50 | expert |
| `topic_scan` | 主题扫描 | 30 | expert |
| `question_matrix` | 问题矩阵 | 20 | expert |
| `site_sweep` | 站点遍历 | 18 | expert |

---

### 管理接口

#### `GET /health` — 健康检查

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "ok",
  "tokens_total": 3,
  "tokens_available": 2,
  "version": "2.0.0"
}
```

---

#### `GET /admin/system` — 系统信息

返回完整的系统状态，包括 HTTP 后端、代理配置、WARP 状态。**SouWen 接入时推荐先调用此接口检查服务能力。**

```bash
curl http://localhost:8000/admin/system \
  -H "Authorization: Bearer YOUR_API_KEY"
```

```json
{
  "version": "2.0.0",
  "http_backend": "curl_cffi",
  "http_backend_config": "auto",
  "curl_cffi_available": true,
  "fingerprint_rotation": true,
  "proxy": "socks5://127.0.0.1:1080",
  "proxy_pool_size": 3,
  "warp_status": "enabled",
  "tokens_total": 3,
  "tokens_available": 2
}
```

---

#### `GET /admin/pool` — Token 池状态

```bash
curl http://localhost:8000/admin/pool \
  -H "Authorization: Bearer YOUR_API_KEY"
```

```json
{
  "total": 3,
  "available": 2,
  "slots": [
    {
      "index": 0,
      "token_prefix": "eyJh...",
      "in_flight": 0,
      "total_used": 42,
      "total_errors": 1,
      "disabled": false,
      "disable_reason": "",
      "cooldown_remaining": 0.0
    }
  ]
}
```

---

#### `POST /admin/pool/enable-all` — 重新启用所有 Token

```bash
curl -X POST http://localhost:8000/admin/pool/enable-all \
  -H "Authorization: Bearer YOUR_API_KEY"
```

```json
{"enabled": 1, "total": 3}
```

---

#### `GET /admin/quota` — 查询额度

```bash
# 自动选择 Token
curl http://localhost:8000/admin/quota \
  -H "Authorization: Bearer YOUR_API_KEY"

# 指定 Token
curl "http://localhost:8000/admin/quota?token_index=0" \
  -H "Authorization: Bearer YOUR_API_KEY"
```

```json
{
  "token_prefix": "eyJh...",
  "total_remaining": 150,
  "limits": {
    "fast": {
      "label": "Fast",
      "remaining": 50,
      "total": 50,
      "wait_seconds": 0,
      "window_seconds": 7200
    },
    "expert": {
      "label": "Expert",
      "high_remaining": 30,
      "high_wait_seconds": 0,
      "low_remaining": 50,
      "low_wait_seconds": 0,
      "remaining": 80,
      "window_seconds": 7200
    },
    "heavy": {
      "label": "Heavy",
      "remaining": 20,
      "total": 20,
      "wait_seconds": 0,
      "window_seconds": 86400
    }
  }
}
```

---

#### `POST /admin/config/reload` — 热更新配置

```bash
curl -X POST http://localhost:8000/admin/config/reload \
  -H "Authorization: Bearer YOUR_API_KEY"
```

```json
{
  "status": "ok",
  "templates_loaded": 6,
  "default_mode": "auto",
  "default_prompt_id": "default",
  "cooldown": 3.0,
  "http_backend": "curl_cffi",
  "warp_enabled": true
}
```

---

### WARP 管理

#### `GET /admin/warp/status` — WARP 代理状态

```bash
curl http://localhost:8000/admin/warp/status \
  -H "Authorization: Bearer YOUR_API_KEY"
```

```json
{
  "status": "enabled",
  "mode": "wireproxy",
  "owner": "python",
  "socks_port": 1080,
  "ip": "104.28.xxx.xxx",
  "pid": 12345,
  "interface": null,
  "last_error": "",
  "available_modes": {
    "wireproxy": true,
    "kernel": false
  }
}
```

**状态值说明:**

| 状态 | 说明 |
|------|------|
| `disabled` | 未启用 |
| `starting` | 正在启动 |
| `enabled` | 已启用 |
| `stopping` | 正在关闭 |
| `error` | 错误（查看 last_error）|

---

#### `POST /admin/warp/enable` — 启用 WARP 代理

**请求体 (JSON, 可选):**

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `mode` | string | "auto" | `auto` / `wireproxy` / `kernel` |
| `socks_port` | int | 1080 | SOCKS5 监听端口（1-65535）|
| `endpoint` | string\|null | null | 自定义 WARP endpoint |

```bash
# 使用默认设置
curl -X POST http://localhost:8000/admin/warp/enable \
  -H "Authorization: Bearer YOUR_API_KEY"

# 指定模式和端口
curl -X POST http://localhost:8000/admin/warp/enable \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{"mode": "wireproxy", "socks_port": 2080}'
```

**成功响应:**

```json
{"ok": true, "mode": "wireproxy", "ip": "104.28.xxx.xxx"}
```

---

#### `POST /admin/warp/disable` — 禁用 WARP 代理

```bash
curl -X POST http://localhost:8000/admin/warp/disable \
  -H "Authorization: Bearer YOUR_API_KEY"
```

```json
{"ok": true, "message": "WARP 已关闭"}
```

---

## 响应格式

所有响应均为 JSON 格式（`Content-Type: application/json`），使用 `orjson` 序列化。

### 通用响应头

| 头 | 值 | 说明 |
|----|-----|------|
| `Content-Type` | `application/json` | JSON 响应 |
| `Content-Encoding` | `gzip` | 大于 1KB 的响应自动 GZIP 压缩 |
| `Access-Control-Allow-Origin` | `*` | CORS 允许所有来源 |

---

## 错误处理

### HTTP 状态码

| 状态码 | 含义 | 场景 |
|--------|------|------|
| 200 | 成功 | 正常响应 |
| 400 | 请求错误 | 参数校验失败 |
| 401 | 未认证 | 缺少 Authorization 头 |
| 403 | 禁止 | API Key 无效 |
| 422 | 参数验证失败 | Pydantic 校验错误 |
| 500 | 内部错误 | 未处理异常 |
| 502 | 上游错误 | Grok 返回非 200 |
| 503 | 服务不可用 | 无可用 Token |

### 错误响应格式

```json
{
  "error": "error_type",
  "detail": "错误详情描述"
}
```

### Grok 上游错误

当 Grok 返回非 200 状态码时，API 返回 502：

```json
{
  "error": "upstream_error",
  "detail": "Grok upstream error: 429 - Rate limited"
}
```

**特殊处理：** 401/403 响应会自动禁用对应的 SSO Token，避免重复使用无效凭证。

---

## SouWen 集成指南

### 接口兼容设计

Grok Search API 的接口设计参考 SouWen 架构，可直接作为 SouWen 的搜索数据源接入。

### 在 SouWen 中配置

在 SouWen 的 `souwen.yaml` 中添加 Grok Search 数据源：

```yaml
sources:
  grok_search:
    enabled: true
    base_url: http://localhost:8000
    api_key: YOUR_GROK_SEARCH_API_KEY
    proxy: none  # Grok Search API 内部已处理代理
    params:
      mode: auto
      prompt_id: deep_research
```

### SouWen 集成请求流程

```
SouWen 搜索请求
    ↓
POST http://grok-search:8000/v1/search
    {"query": "...", "mode": "auto", "prompt_id": "deep_research"}
    ↓
Grok Search API 内部:
    1. Token 池获取可用 Token
    2. 提示词模板渲染
    3. 选择 HTTP 后端 (curl_cffi/httpx)
    4. 通过代理 (WARP/代理池) 发送请求
    5. SSE 流解析 → webSearchResults 提取
    6. 去重返回
    ↓
SouWen 接收搜索结果 JSON
```

### 能力检测

SouWen 接入前，调用 `/admin/system` 检查服务能力：

```python
import httpx

async with httpx.AsyncClient() as client:
    resp = await client.get(
        "http://localhost:8000/admin/system",
        headers={"Authorization": "Bearer YOUR_KEY"},
    )
    info = resp.json()

    # 检查关键能力
    assert info["tokens_available"] > 0, "无可用 Token"
    print(f"HTTP 后端: {info['http_backend']}")
    print(f"curl_cffi: {'可用' if info['curl_cffi_available'] else '不可用'}")
    print(f"WARP: {info['warp_status']}")
    print(f"代理: {info['proxy'] or '无'}")
```

### 搜索结果字段映射

Grok 返回的 `search_results` 数组中，每个对象包含以下字段（由 Grok 原始返回）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `title` | string | 页面标题 |
| `url` | string | 页面 URL |
| `preview` | string | 摘要预览 |

SouWen 集成时，可按需映射到 SouWen 的统一结果格式。

### Python 客户端示例

```python
import httpx

API_BASE = "http://localhost:8000"
API_KEY = "your_api_key"

headers = {"Authorization": f"Bearer {API_KEY}"}


async def search(query: str, mode: str = "auto", prompt_id: str = "default"):
    """单次搜索"""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{API_BASE}/v1/search",
            json={"query": query, "mode": mode, "prompt_id": prompt_id},
            headers=headers,
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()


async def batch_search(queries: list[str], mode: str = "auto", concurrency: int = 3):
    """批量搜索"""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{API_BASE}/v1/search/batch",
            json={"queries": queries, "mode": mode, "concurrency": concurrency},
            headers=headers,
            timeout=300,
        )
        resp.raise_for_status()
        return resp.json()


# 使用示例
import asyncio

result = asyncio.run(search("AI 框架对比 2025", prompt_id="deep_research"))
print(f"找到 {result['total_results']} 条结果")
for r in result["search_results"][:5]:
    print(f"  - {r.get('title', 'N/A')}: {r.get('url', 'N/A')}")
```

### curl 示例合集

```bash
# 1. 检查服务状态
curl http://localhost:8000/health

# 2. 查看系统信息
curl http://localhost:8000/admin/system -H "Authorization: Bearer KEY"

# 3. 简单搜索
curl -X POST http://localhost:8000/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "Python asyncio 教程"}'

# 4. 深度搜索（300 次搜索裂变）
curl -X POST http://localhost:8000/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "量子计算最新进展", "prompt_id": "deep_research"}'

# 5. 批量搜索
curl -X POST http://localhost:8000/v1/search/batch \
  -H "Content-Type: application/json" \
  -d '{"queries": ["Rust", "Go", "Zig"], "concurrency": 3}'

# 6. 启用 WARP 代理
curl -X POST http://localhost:8000/admin/warp/enable \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer KEY" \
  -d '{"mode": "auto"}'

# 7. 查看 WARP 状态
curl http://localhost:8000/admin/warp/status -H "Authorization: Bearer KEY"

# 8. 查询额度
curl http://localhost:8000/admin/quota -H "Authorization: Bearer KEY"

# 9. 热更新配置
curl -X POST http://localhost:8000/admin/config/reload -H "Authorization: Bearer KEY"
```

---

## Docker 部署

### 基础部署

```bash
cd search_api
docker build -t grok-search-api .
docker run -p 8000:8000 --env-file .env grok-search-api
```

### 启用 WARP (wireproxy 模式，无需特权)

```bash
docker run -p 8000:8000 \
  -e GROK_SSO_TOKENS=token1,token2 \
  -e WARP_ENABLED=true \
  -e WARP_MODE=wireproxy \
  grok-search-api
```

### 启用 WARP (kernel 模式，高性能)

```bash
docker run -p 8000:8000 \
  --cap-add NET_ADMIN \
  --device /dev/net/tun:/dev/net/tun \
  -e GROK_SSO_TOKENS=token1,token2 \
  -e WARP_ENABLED=true \
  -e WARP_MODE=kernel \
  grok-search-api
```

### 启用 curl_cffi

```bash
# 在 Dockerfile 中添加
RUN pip install curl_cffi

# 或使用环境变量
docker run -p 8000:8000 \
  -e HTTP_BACKEND=auto \
  -e FINGERPRINT_ROTATION=true \
  --env-file .env \
  grok-search-api
```

### docker-compose 完整示例

```yaml
version: '3.8'
services:
  grok-search:
    build: ./search_api
    ports:
      - "8000:8000"
    cap_add:
      - NET_ADMIN
    devices:
      - /dev/net/tun:/dev/net/tun
    environment:
      GROK_SSO_TOKENS: "${GROK_SSO_TOKENS}"
      API_KEY: "${API_KEY}"
      HTTP_BACKEND: "auto"
      FINGERPRINT_ROTATION: "true"
      WARP_ENABLED: "true"
      WARP_MODE: "auto"
      WARP_SOCKS_PORT: "1080"
    volumes:
      - warp-data:/app/data
    restart: unless-stopped

volumes:
  warp-data:
```

---

## 文件结构

```
search_api/
├── __init__.py          # 包声明
├── __main__.py          # 入口（python -m search_api）
├── app.py               # FastAPI 应用（路由、中间件、异常处理、WARP 管理端点）
├── config.py            # 配置管理（config.json + 环境变量 + WARP + 代理池）
├── token_pool.py        # Token 号池（轮询、冷却、禁用）
├── grok_search.py       # 搜索核心（Payload、SSE 解析、结果提取、额度查询）
├── http_client.py       # HTTP 传输（httpx/curl_cffi 双后端 + 指纹 + 自动降级）
├── fingerprint.py       # 浏览器指纹管理（TLS + UA + Sec-CH-UA 轮换）
├── warp.py              # WARP 代理管理（wireproxy + kernel 双模式）
├── schemas.py           # Pydantic 请求/响应模型
├── config.json          # 持久配置（自动生成）
├── requirements.txt     # 依赖（curl_cffi 可选）
├── .env.example         # 环境变量模板
├── API_DOCS.md          # 本文件
└── README.md            # 快速入门文档
```
