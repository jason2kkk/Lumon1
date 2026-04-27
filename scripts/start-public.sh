#!/usr/bin/env bash
# 一键外网可访问：构建前端 + 本机 8000 托管 + Cloudflare Tunnel
#
# 用法：
#   ./scripts/start-public.sh              # 默认会 npm run build
#   SKIP_BUILD=1 ./scripts/start-public.sh # 跳过构建（已有 frontend/dist）
#   PORT=8000 ./scripts/start-public.sh    # 端口（须与 cloudflare/config.yml 中 service 一致）
#
# 前置：已执行 cloudflare/setup-lumon-vbradar.sh，存在 cloudflare/config.yml；已安装 cloudflared（PATH 或 ~/.local/bin）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PORT="${PORT:-8000}"
SKIP_BUILD="${SKIP_BUILD:-0}"
CFG="${ROOT}/cloudflare/config.yml"

export PATH="${HOME}/.local/bin:${PATH}"

die() { echo "错误: $*" >&2; exit 1; }

[[ -f "$CFG" ]] || die "缺少 ${CFG}，请先运行: ./cloudflare/setup-lumon-vbradar.sh"

CLOUDFLARED="${CLOUDFLARED:-}"
if command -v cloudflared >/dev/null 2>&1; then
  CLOUDFLARED="$(command -v cloudflared)"
elif [[ -x "${HOME}/.local/bin/cloudflared" ]]; then
  CLOUDFLARED="${HOME}/.local/bin/cloudflared"
else
  die "未找到 cloudflared，请安装或加入 PATH（见 README Cloudflare 一节）"
fi

if [[ ! -x "${ROOT}/.venv/bin/python" ]]; then
  die "缺少 ${ROOT}/.venv，请先: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
fi

if ! grep -q "127.0.0.1:${PORT}" "$CFG" && ! grep -q "localhost:${PORT}" "$CFG"; then
  echo "警告: cloudflare/config.yml 中的回源端口可能不是 ${PORT}，隧道可能连错服务。可执行 ORIGIN_PORT=${PORT} ./cloudflare/setup-lumon-vbradar.sh 对齐。" >&2
fi

if [[ "$SKIP_BUILD" != "1" ]]; then
  echo ">>> 构建前端…"
  (cd "${ROOT}/frontend" && npm run build)
else
  [[ -d "${ROOT}/frontend/dist" ]] || die "无 frontend/dist，请去掉 SKIP_BUILD=1 先构建"
  echo ">>> 跳过构建（使用已有 frontend/dist）"
fi

PUBLIC_HOST="$(grep -E '^\s+-\s+hostname:\s+' "$CFG" | head -1 | awk '{print $3}' || true)"
if [[ -n "${PUBLIC_HOST}" ]]; then
  echo ">>> 外网地址: https://${PUBLIC_HOST}"
else
  echo ">>> 外网: 见 Cloudflare 隧道配置的 hostname"
fi
echo ">>> 本机: http://127.0.0.1:${PORT}"
echo ">>> Ctrl+C 停止后端与隧道"
echo ""

cleanup() {
  kill "${UV_PID:-}" "${CF_PID:-}" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

"${ROOT}/.venv/bin/python" -m uvicorn server:app --host 127.0.0.1 --port "${PORT}" &
UV_PID=$!

sleep 1
if ! kill -0 "${UV_PID}" 2>/dev/null; then
  die "uvicorn 启动失败"
fi

"${CLOUDFLARED}" tunnel --config "$CFG" run &
CF_PID=$!

while kill -0 "${UV_PID}" 2>/dev/null && kill -0 "${CF_PID}" 2>/dev/null; do
  sleep 2
done
wait "${UV_PID}" 2>/dev/null || true
wait "${CF_PID}" 2>/dev/null || true
