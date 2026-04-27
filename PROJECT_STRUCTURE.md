# Lumon — 项目结构

## 代码行数统计

| 语言 | 文件数 | 代码行数 |
|---|---|---|
| Python | 18 | 11,843 |
| TypeScript / TSX | 27 | 9,340 |
| CSS | 1 | 112 |
| HTML | 2 | 33 |
| **合计（源码）** | **48** | **21,328** |

> 统计不含 `node_modules`、`.venv`、`__pycache__`、`dist`、配置文件。

### Python 主要文件行数

| 文件 | 行数 | 职责 |
|---|---|---|
| api_routes.py | 4,574 | REST / SSE 接口 |
| web_search.py | 1,064 | 联网搜索（Tavily / Claude / GPT） |
| st_client.py | 1,041 | SensorTower 市场数据 |
| session_context.py | 770 | 多会话上下文管理 |
| debate.py | 735 | 多角色讨论引擎 |
| llm_client.py | 591 | LLM 调用封装 |
| rdt_client.py | 548 | Reddit 双引擎采集 |
| scrapers.py | 511 | HN + Reddit 数据抓取 |
| prompts/ | 1,368 | Prompt 模板包（6 模块） |
| 其他 | 641 | server / feishu / quote_extractor |

### 前端主要文件行数

| 文件 | 行数 | 职责 |
|---|---|---|
| FetchView.tsx | 1,379 | 采集页（挖掘控制 + 需求卡片） |
| SettingsDialog.tsx | 1,207 | 设置弹窗（模型 / 引擎 / 角色） |
| TrendingView.tsx | 1,088 | 热度排行视图 |
| api/client.ts | 868 | API 请求封装（REST + SSE） |
| ReportsView.tsx | 846 | 报告列表 + POC 评价 |
| ReportView.tsx | 834 | 报告 Markdown / JSON 渲染 |
| ChatPanel.tsx | 525 | 讨论对话面板 |
| DetailPanel.tsx | 321 | 右侧需求详情面板 |
| stores/app.ts | 309 | Zustand 全局状态 |
| animations/ | 791 | 动画组件库（8 组件） |

## 根目录总览

```
lumon/
│
├── server.py              # FastAPI 入口，CORS + 静态文件托管 + 路由挂载
├── api_routes.py          # REST/SSE 端点（采集、讨论、报告、POC 评价、配置）
├── llm_client.py          # LLM 调用封装（Claude/GPT），配置管理，角色模型映射
├── debate.py              # 多角色讨论引擎（导演 / 产品经理 / 杠精 / 投资人）
├── scrapers.py            # HackerNews + Reddit 数据抓取，板块分类，信号检测
├── rdt_client.py          # Reddit 双引擎封装（rdt-cli 优先 + Apify 备用）
├── quote_extractor.py     # 原文摘录提取 + FEMWC 五维评分
├── web_search.py          # 联网搜索封装（Tavily / Claude Search / GPT Search）
├── st_client.py           # SensorTower 数据客户端（市场趋势分析）
├── session_context.py     # 多会话上下文管理（会话隔离 + 状态持久化）
├── feishu_client.py       # 飞书在线文档导出
│
├── prompts/               # LLM Prompt 模板包
│   ├── __init__.py        # 统一导出所有 prompt 常量
│   ├── search.py          # 搜索规划、相关性检查、深挖查询、自主发现
│   ├── extraction.py      # 原文摘录、FEMWC 评分、帖子过滤、需求聚类
│   ├── debate.py          # 导演/产品经理/杠精/投资人 system prompt
│   ├── report.py          # 产品提案、信号提炼、最终报告、直接报告
│   ├── competitor.py      # 竞品搜索规划 & 信息提取
│   └── poc_eval.py        # POC 产品准入评价 prompt
│
├── frontend/              # 前端工程（详见下方）
├── data/                  # 运行时数据（不入库）
├── docs/                  # 项目文档
│   └── 技术架构文档.md     # 核心架构 + 流程图（Mermaid）
│
├── cloudflare/            # Cloudflare Tunnel 配置
│   ├── config.example.yml # 隧道配置模板
│   ├── run-tunnel.sh      # 隧道启动脚本
│   └── setup-lumon-vbradar.sh # 固定域名隧道初始化
│
├── scripts/               # 运维脚本
│   ├── cloudflare-quick-tunnel.sh # 快速临时隧道
│   └── start-public.sh    # 一键生产启动（构建 + 后端 + 隧道）
│
├── Dockerfile             # Docker 镜像构建
├── docker-compose.yml     # Docker Compose 编排
├── README.md              # 项目说明 + 快速开始
├── PROJECT_STRUCTURE.md   # 本文件
├── requirements.txt       # Python 依赖
├── .env.example           # 环境变量模板
├── .env                   # 环境变量（不入库）
└── .gitignore
```

## 前端（React + TypeScript + Vite）

```
frontend/
├── index.html             # HTML 入口
├── package.json           # npm 依赖与脚本
├── vite.config.ts         # Vite 配置（开发代理后端 /api）
├── tsconfig.json          # TypeScript 配置
├── public/                # 静态资源（图标、Logo、字体）
└── src/
    ├── main.tsx            # React 入口
    ├── App.tsx             # 根组件 + 视图路由 + 布局（含移动端适配）
    ├── types.ts            # TypeScript 类型定义
    ├── index.css           # 全局样式（Tailwind v4 + 响应式 + 自定义滚动条）
    ├── api/
    │   └── client.ts       # API 请求封装（REST + SSE 流式）
    ├── stores/
    │   └── app.ts          # Zustand 全局状态管理
    └── components/
        ├── NavSidebar.tsx       # 桌面左侧导航 + 移动端底部 Tab 栏
        ├── FetchView.tsx        # 采集页（挖掘控制 + 需求卡片列表）
        ├── ChatPanel.tsx        # 讨论对话面板（SSE 流式）
        ├── ChatMessage.tsx      # 单条对话消息组件
        ├── DetailPanel.tsx      # 右侧详情面板（需求分析 / 方案 / 报告）
        ├── SettingsDialog.tsx   # 设置弹窗（模型 / 引擎 / 角色 / 飞书）
        ├── ReportsView.tsx      # 报告列表 + 报告详情 + POC 评价
        ├── ReportView.tsx       # 报告 Markdown / JSON 渲染
        ├── TrendingView.tsx     # 热度排行视图（暂未上线）
        ├── AnalysisCard.tsx     # 需求分析结构化卡片（FEMWC）
        ├── HelpDialog.tsx       # 帮助弹窗组件
        ├── ConfirmDialog.tsx    # 确认弹窗
        ├── ResizeHandle.tsx     # 面板拖动调整宽度（支持触屏）
        └── animations/          # 动画组件库（8 个）
            ├── index.ts
            ├── BlurText.tsx
            ├── CountUp.tsx
            ├── DecryptedText.tsx
            ├── GradientText.tsx
            ├── LogosCarousel.tsx
            ├── RotatingText.tsx
            ├── ShineBorder.tsx
            └── ShimmerText.tsx
```

## 数据目录（运行时生成，不入库）

```
data/
├── sessions/                     # 多用户会话（每个会话独立目录）
│   └── {session-id}/
│       ├── config.json           # 会话级配置（模型偏好等）
│       ├── .last_active          # 最后活跃时间戳
│       └── cache/                # 会话缓存（需求、讨论状态等）
├── cache/                        # 全局缓存（兼容旧版）
│   ├── fetched_needs.json        # 缓存的需求数据
│   ├── debate_state.json         # 讨论状态持久化
│   ├── role_models.json          # 角色模型映射
│   ├── role_names.json           # 角色名称自定义
│   ├── engine_preference.json    # Reddit 引擎偏好
│   ├── web_search_preference.json # 搜索引擎偏好
│   └── general_model.json        # 全局模型偏好
├── reports/                      # 生成的研究报告 JSON
├── poc_evaluations/              # POC 评价结果 JSON
├── demo/                         # 演示模式缓存
│   └── demo_debate.json
├── trending/                     # 热度数据快照
└── global_stats.json             # 全局使用统计
```

## 启动方式

```bash
# 后端
source .venv/bin/activate
uvicorn server:app --reload --port 8000

# 前端（开发）
cd frontend && npm run dev

# 生产部署（前端构建后由后端托管）
cd frontend && npm run build && cd ..
uvicorn server:app --host 0.0.0.0 --port 8000

# Docker
docker-compose up
```
