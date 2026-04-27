# Lumon — 海外社区需求挖掘与分析平台

从 Reddit、HackerNews 等海外社区挖掘真实用户需求，通过多 Agent 讨论验证，输出可落地的产品机会报告。

## 核心功能

- **需求挖掘**：三种模式（指定赛道 / 关键词搜索 / 自主发现），自动采集、过滤、聚类社区帖子为需求主题
- **多 Agent 讨论**：导演 / 产品经理 / 杠精 / 投资人 四角色深入探讨需求真实性与可行性
- **产品方案生成**：从讨论中提取产品方案，可直接进入深度研究
- **深度研究报告**：联网搜索竞品、目标人群、信号提炼，生成完整 Markdown 研究报告
- **POC 准入评价**：基于红毛丹准则，从「清晰的用户 / 真实的需求 / 简单的产品」三个维度 AI 评审
- **FEMWC 评分**：频率、情感、市场、付费意愿、竞争五维度量化评估
- **飞书导出**：一键将报告导出为飞书在线文档

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.14 · FastAPI · Uvicorn |
| 前端 | React 19 · TypeScript · Vite · Tailwind CSS · Framer Motion |
| LLM | Claude + GPT（兼容 OpenAI 格式中转站） |
| 数据源 | Reddit（rdt-cli + Apify 双引擎）· HackerNews（Algolia API） |
| 搜索 | Tavily API · Claude Search · GPT Search |
| 存储 | JSON 文件（无数据库依赖） |

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+
- rdt-cli（可选，用于免费 Reddit 采集）

### 安装

```bash
cd requirement_agents_app

# Python 依赖
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 前端依赖
cd frontend && npm install && cd ..
```

### 配置

```bash
cp .env.example .env
```

编辑 `.env` 填入你的 API Key，或启动后通过 Web 界面的「设置」页面配置。

### 启动

```bash
# 终端 1 — 后端
source .venv/bin/activate
uvicorn server:app --reload --port 8000

# 终端 2 — 前端（开发模式）
cd frontend && npm run dev
```

打开 http://localhost:5173 即可使用。

### Cloudflare Tunnel（外网访问）

无需公网 IP、不必开端口映射，用 [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/) 把本机服务暴露到 HTTPS。

**快速隧道（临时 URL，适合联调）**：先照常启动后端与 `frontend` 的 `npm run dev`，再执行：

```bash
# 任选其一
./scripts/cloudflare-quick-tunnel.sh
cd frontend && npm run tunnel
```

终端会打印 `https://*.trycloudflare.com`，用该地址外网访问；`/api` 仍由 Vite 反代到本机 8000。若需要热更新走隧道，把打印的主机名写入 `frontend/.env.development.local` 中的 `VITE_DEV_TUNNEL_HOST`（参见 `frontend/.env.example`），然后重启 Vite。

**生产单端口**：`cd frontend && npm run build && cd ..` 后只跑 `uvicorn server:app --host 0.0.0.0 --port 8000`，再执行 `cd frontend && npm run tunnel:prod`（或 `cloudflared tunnel --url http://127.0.0.1:8000`），外网只暴露 8000。

**固定域名（lumon.vbradar.com）**：

1. 将 `~/.local/bin` 加入 `PATH`（若使用本仓库协助安装的 `cloudflared`）。
2. 执行 `cloudflared tunnel login`，在浏览器中授权 **vbradar.com** 所在 Cloudflare 账户。
3. 在项目根目录执行 `./cloudflare/setup-lumon-vbradar.sh`（会创建隧道 `lumon-vbradar`、添加 `lumon.vbradar.com` 的 DNS、并生成本地 `cloudflare/config.yml`）。默认回源 `http://127.0.0.1:8000`；开发模式可执行 `ORIGIN_PORT=5173 ./cloudflare/setup-lumon-vbradar.sh` 重写配置。
4. 一键外网启动（构建 + 本机 8000 + 隧道）：`./scripts/start-public.sh`（`Ctrl+C` 同时停后端与隧道；仅重试可设 `SKIP_BUILD=1`）。

`cloudflare/config.yml` 与 `cloudflare/*.json` 已加入 `.gitignore`；隧道凭证默认在 `~/.cloudflared/`，勿提交到仓库。

更通用的示例见 `cloudflare/config.example.yml`。

**隧道日志里 QUIC 超时**（`failed to accept QUIC stream: timeout: no recent network activity`）：生成的 `cloudflare/config.yml` 已默认 `protocol: http2`。若你仍在用旧配置，在 `tunnel:` 上一行加上 `protocol: http2` 后重启 `cloudflared`。

**聚类 / LLM 报 `Connection error`**：这是**本机后端**访问 Claude/GPT API 失败（与隧道无关）。请检查设置里的 Base URL、API Key、本机能否直连该 API（防火墙、代理、地区网络）；`POST /api/config/test` 通过不代表长请求一定稳定，可换节点或稍后重试。

### 生产部署

```bash
# 构建前端
cd frontend && npm run build && cd ..

# 启动后端（自动托管前端静态文件）
uvicorn server:app --host 0.0.0.0 --port 8000
```

打开 http://your-server:8000 即可。

## 代码规模

| 语言 | 文件数 | 代码行数 |
|---|---|---|
| Python | 18 | 11,843 |
| TypeScript / TSX | 27 | 9,340 |
| CSS | 1 | 112 |
| HTML | 2 | 33 |
| **合计（源码）** | **48** | **21,328** |

## 项目结构

详见 [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)，技术架构图详见 [docs/技术架构文档.md](docs/技术架构文档.md)

## 数据流

```
社区数据采集 → 硬性过滤 → LLM 相关性检查 → 聚类为需求主题
    → 多 Agent 讨论 → 产品方案 → 联网深挖 → 研究报告 → POC 评价
```
