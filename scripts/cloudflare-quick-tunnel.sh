#!/usr/bin/env bash
# 快速临时隧道（无需 Cloudflare 账号，URL 每次会变）
# 依赖: brew install cloudflared 或从官网安装
#
# 用法:
#   1) 终端 A: 后端  uvicorn server:app --reload --port 8000
#   2) 终端 B: 前端  cd frontend && npm run dev
#   3) 终端 C: 本脚本  ./scripts/cloudflare-quick-tunnel.sh
#
# 若需热更新 HMR 走 HTTPS，把 cloudflared 打印的域名（不含 https://）
# 写入 frontend/.env.development.local 中的 VITE_DEV_TUNNEL_HOST，然后重启 Vite。
#
set -euo pipefail
PORT="${1:-5173}"
exec cloudflared tunnel --url "http://127.0.0.1:${PORT}"
