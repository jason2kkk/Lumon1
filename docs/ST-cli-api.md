# SensorTower 外部数据接口文档

## 概述

通过 HTTP 接口查询 SensorTower 数据，包括单个 App 数据、批量竞品对比、品类/赛道市场数据。

**服务地址：** `http://192.168.43.201:8000`

## 认证

所有请求必须在 Header 中携带 API Key：

```
X-API-Key: nOffecONPdynwGnEMRBBtrS1L7o4d3TsUid89Zhz7UE
```

没有 Key 或 Key 错误会返回 `401`。

---

## 自测连通性

拿到服务地址和 API Key 后，先跑这条命令确认一切正常：

```bash
curl -s http://192.168.43.201:8000/api/cli/st/status \
  -H "X-API-Key: nOffecONPdynwGnEMRBBtrS1L7o4d3TsUid89Zhz7UE" | python3 -m json.tool
```

正常响应：

```json
{
    "ok": true,
    "data": {
        "installed": true,
        "available": true,
        "api_ok": true,
        "credential_source": "...",
        "error": ""
    },
    "stats": { ... }
}
```

如果 `installed` 或 `available` 为 `false`，说明服务器上的 st-cli 未安装或未登录，联系管理员处理。

---

## 接口列表

| 接口 | 方法 | 用途 | 耗时参考 |
|------|------|------|---------|
| `/api/cli/st/status` | GET | 检查 ST 可用状态 | < 15 秒 |
| `/api/cli/st/app` | POST | 查单个 App 数据 | < 30 秒 |
| `/api/cli/st/landscape` | POST | 批量竞品数据对比 | < 180 秒 |
| `/api/cli/st/market` | POST | 品类/赛道市场数据 | < 60 秒 |

**并发限制：** 同一时间只允许 1 个请求在处理。如果已有请求在执行，新请求会返回 `429`，稍后重试即可。

---

## 1. 检查状态

```
GET /api/cli/st/status
```

**curl 示例：**

```bash
curl -s http://192.168.43.201:8000/api/cli/st/status \
  -H "X-API-Key: nOffecONPdynwGnEMRBBtrS1L7o4d3TsUid89Zhz7UE"
```

**响应：**

```json
{
    "ok": true,
    "data": {
        "installed": true,
        "available": true,
        "api_ok": true,
        "credential_source": "cookie",
        "error": ""
    },
    "stats": {
        "status": { "calls": 1, "last_call": 1714276800.0 },
        "app": { "calls": 0, "last_call": 0.0 },
        "landscape": { "calls": 0, "last_call": 0.0 },
        "market": { "calls": 0, "last_call": 0.0 }
    }
}
```

`stats` 字段展示各接口的累计调用次数和最后调用时间（Unix 时间戳）。

---

## 2. 查询单个 App

```
POST /api/cli/st/app
```

根据 App 名称或 App Store 链接查询收入、下载量、发行商等信息。

**参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | 是 | App 名称（如 `"Duolingo"`）或 App Store URL |

**curl 示例：**

```bash
curl -s http://192.168.43.201:8000/api/cli/st/app \
  -H "X-API-Key: nOffecONPdynwGnEMRBBtrS1L7o4d3TsUid89Zhz7UE" \
  -H "Content-Type: application/json" \
  -d '{"query": "Duolingo"}'
```

**响应：**

```json
{
    "ok": true,
    "data": {
        "name": "Duolingo: Language & Chess",
        "publisher": "Duolingo",
        "revenue_last_month": 53000000,
        "revenue_display": "$53m",
        "downloads_last_month": 18000000,
        "downloads_display": "18m",
        "icon_url": "https://is1-ssl.mzstatic.com/image/thumb/Purple211/v4/ce/c5/22/...",
        "release_date": "2012-11-13T08:00:00Z"
    }
}
```

**未找到 App 时：**

```json
{
    "ok": false,
    "error": "未找到匹配的 App 或 SensorTower 返回为空",
    "data": null
}
```

---

## 3. 批量竞品对比

```
POST /api/cli/st/landscape
```

一次查询多个竞品的详细数据，包括收入、下载、MAU、市占率、增长率、App Store 用户评论等。

**注意：** 这是重量级操作，每个竞品都要单独查询，请求可能需要数分钟完成。

**参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `competitors` | array | 是 | 竞品列表，每项包含 `name`（必填）和 `url`（可选，App Store 链接） |
| `limit` | int | 否 | 查询数量上限，默认 5，最大 10 |

**curl 示例：**

```bash
curl -s http://192.168.43.201:8000/api/cli/st/landscape \
  -H "X-API-Key: nOffecONPdynwGnEMRBBtrS1L7o4d3TsUid89Zhz7UE" \
  -H "Content-Type: application/json" \
  -d '{
    "competitors": [
      {"name": "Duolingo", "url": "https://apps.apple.com/app/id570060128"},
      {"name": "Babbel"},
      {"name": "Rosetta Stone"}
    ],
    "limit": 5
  }'
```

**响应：**

```json
{
    "ok": true,
    "data": [
        {
            "name": "Duolingo",
            "store_url": "https://apps.apple.com/app/id570060128",
            "has_st_data": true,
            "revenue_last_month": 52000000,
            "revenue_display": "$52.0M",
            "downloads_last_month": 18000000,
            "downloads_display": "18.0M",
            "mau": 25000000,
            "mau_display": "25.0M",
            "market_share_percent": 32.5,
            "market_share_display": "32.5%",
            "growth_6m_percent": 12.3,
            "growth_6m_display": "+12.3%",
            "first_release": "2012-11-13",
            "release_year": "2012",
            "ai_label": "-",
            "segment": "-",
            "strengths": ["游戏化学习", "免费模式"],
            "weaknesses": ["高级内容不足"],
            "comments": [
                {
                    "rating": 2,
                    "title": "Too many ads",
                    "content": "...",
                    "sentiment": "unhappy",
                    "tags": ["ads"]
                }
            ],
            "publisher": "Duolingo Inc",
            "icon_url": "https://..."
        }
    ]
}
```

---

## 4. 市场数据查询

```
POST /api/cli/st/market
```

支持三种查询模式：

### 模式 A：按品类 ID 查询

查询 SensorTower 内置品类（如教育、健康、金融等）的市场聚合数据。

**参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `mode` | string | 是 | 固定 `"category"` |
| `category_id` | int | 是 | SensorTower 品类 ID（如教育=6017） |
| `top_n` | int | 否 | 返回 Top N 个 App，默认 20 |

```bash
curl -s http://192.168.43.201:8000/api/cli/st/market \
  -H "X-API-Key: nOffecONPdynwGnEMRBBtrS1L7o4d3TsUid89Zhz7UE" \
  -H "Content-Type: application/json" \
  -d '{"mode": "category", "category_id": 6017, "top_n": 10}'
```

### 模式 B：按关键词查细分赛道

用多个关键词搜索找到细分赛道的头部产品，聚合市场数据。

**参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `mode` | string | 是 | 固定 `"niche"` |
| `queries` | array | 是 | 搜索关键词列表 |
| `top_n` | int | 否 | 返回 Top N，默认 20 |

```bash
curl -s http://192.168.43.201:8000/api/cli/st/market \
  -H "X-API-Key: nOffecONPdynwGnEMRBBtrS1L7o4d3TsUid89Zhz7UE" \
  -H "Content-Type: application/json" \
  -d '{"mode": "niche", "queries": ["language learning", "vocabulary app"], "top_n": 10}'
```

### 模式 C：查产品 + 同赛道竞品排名

查自家产品在赛道中的位置，以及收入相近的竞品列表。

**参数：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `mode` | string | 是 | 固定 `"product"` |
| `product_name` | string | 是 | 产品名称 |
| `category_queries` | array | 是 | 赛道搜索关键词 |
| `peer_count` | int | 否 | 返回竞品数量，默认 8 |

```bash
curl -s http://192.168.43.201:8000/api/cli/st/market \
  -H "X-API-Key: nOffecONPdynwGnEMRBBtrS1L7o4d3TsUid89Zhz7UE" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "product",
    "product_name": "Duolingo",
    "category_queries": ["language learning", "education app"],
    "peer_count": 8
  }'
```

### 响应格式

**模式 A / B 响应：**

```json
{
    "ok": true,
    "data": {
        "product_count": 20,
        "revenue_sum": 150000000.00,
        "revenue_avg": 7500000.00,
        "downloads_sum": 45000000,
        "revenue_growth_pct": 8.5,
        "top_apps": [
            {
                "name": "Duolingo",
                "icon_url": "https://...",
                "publisher": "Duolingo Inc",
                "revenue": 52000000.00,
                "revenue_display": "$52.0M",
                "downloads": 18000000,
                "downloads_display": "18.0M",
                "growth_pct": 12.3,
                "downloads_growth_pct": 5.2,
                "dau": 3500000,
                "dau_display": "3.5M"
            }
        ]
    }
}
```

**模式 C 响应：**

```json
{
    "ok": true,
    "data": {
        "product": {
            "name": "Duolingo",
            "icon_url": "https://...",
            "publisher": "Duolingo Inc",
            "revenue": 52000000.00,
            "revenue_display": "$52.0M",
            "downloads": 18000000,
            "downloads_display": "18.0M",
            "dau": 3500000,
            "dau_display": "3.5M",
            "growth_pct": 12.3,
            "downloads_growth_pct": 5.2
        },
        "peers": [
            {
                "name": "Babbel",
                "revenue": 12000000.00,
                "revenue_display": "$12.0M",
                "downloads": 5000000,
                "rank": 2,
                "is_ours": false
            }
        ]
    }
}
```

---

## 错误码

| HTTP 状态码 | 含义 | 处理建议 |
|------------|------|---------|
| `401` | API Key 无效或缺失 | 检查 `X-API-Key` header |
| `400` | 请求参数错误 | 检查请求体格式和必填字段 |
| `429` | 并发超限（已有请求在处理） | 等几秒后重试 |
| `500` | SensorTower 查询内部错误 | 检查错误详情，可能是 ST 服务端问题 |
| `503` | 服务端未配置 CLI_API_KEY | 联系管理员配置 |

---

## 使用限制

- **并发：** 同一时间只能有 1 个请求在处理
- **landscape：** 单次最多查 10 个竞品，可能需要 1-3 分钟
- **market：** 品类/赛道查询依赖 SensorTower 账号权限，部分数据可能为空