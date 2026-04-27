#!/usr/bin/env bash
# 为 lumon.vbradar.com 创建命名隧道、写入 DNS、生成本仓库 cloudflare/config.yml
# 前置：域名 vbradar.com 已在 Cloudflare；本机已执行 cloudflared tunnel login
set -euo pipefail

TUNNEL_NAME="${TUNNEL_NAME:-lumon-vbradar}"
# 勿用 HOSTNAME：在 macOS 上常被设为计算机名，会覆盖默认域名
TUNNEL_HOSTNAME="${TUNNEL_HOSTNAME:-lumon.vbradar.com}"
ORIGIN_PORT="${ORIGIN_PORT:-8000}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CFG_OUT="${REPO_ROOT}/cloudflare/config.yml"

CLOUDFLARED="${CLOUDFLARED:-}"
if [[ -z "${CLOUDFLARED}" ]]; then
  if command -v cloudflared >/dev/null 2>&1; then
    CLOUDFLARED="$(command -v cloudflared)"
  elif [[ -x "${HOME}/.local/bin/cloudflared" ]]; then
    CLOUDFLARED="${HOME}/.local/bin/cloudflared"
  else
    echo "未找到 cloudflared。若已下载到 ~/.local/bin，请执行: export PATH=\"\$HOME/.local/bin:\$PATH\""
    exit 1
  fi
fi

CERT="${HOME}/.cloudflared/cert.pem"
if [[ ! -f "${CERT}" ]]; then
  echo "尚未登录 Cloudflare。请执行（会打开浏览器授权）："
  echo "  ${CLOUDFLARED} tunnel login"
  echo "授权完成后重新运行: $0"
  exit 1
fi

read_uuid() {
  "${CLOUDFLARED}" tunnel list -o json | TUNNEL_NAME="${TUNNEL_NAME}" python3 -c '
import json, os, sys
name = os.environ["TUNNEL_NAME"]
try:
    data = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(1)
items = data if isinstance(data, list) else data.get("tunnels", data.get("result", []))
if not isinstance(items, list):
    sys.exit(1)
for t in items:
    if isinstance(t, dict) and t.get("name") == name:
        print(t["id"])
        sys.exit(0)
sys.exit(1)
'
}

UUID="$(read_uuid || true)"
if [[ -z "${UUID}" ]]; then
  echo "创建隧道: ${TUNNEL_NAME}"
  "${CLOUDFLARED}" tunnel create "${TUNNEL_NAME}"
  UUID="$(read_uuid || true)"
fi
if [[ -z "${UUID}" ]]; then
  echo "无法获取隧道 UUID，请运行: ${CLOUDFLARED} tunnel list -o json"
  exit 1
fi
echo "隧道: ${TUNNEL_NAME} (${UUID})"

CRED="${HOME}/.cloudflared/${UUID}.json"
if [[ ! -f "${CRED}" ]]; then
  echo "未找到凭证: ${CRED}"
  exit 1
fi

echo "配置 DNS: ${TUNNEL_HOSTNAME} -> 隧道 ${TUNNEL_NAME}"
set +e
DNS_OUT="$("${CLOUDFLARED}" tunnel route dns "${TUNNEL_NAME}" "${TUNNEL_HOSTNAME}" 2>&1)"
DNS_EC=$?
set -e
if [[ ${DNS_EC} -ne 0 ]]; then
  echo "${DNS_OUT}"
  echo "若提示记录已存在，可在 Cloudflare DNS 中确认 ${TUNNEL_HOSTNAME} 指向隧道。"
fi

cat >"${CFG_OUT}" <<EOF
# 由 setup-lumon-vbradar.sh 生成 — ${TUNNEL_HOSTNAME} -> http://127.0.0.1:${ORIGIN_PORT}
# 使用 http2 连接 Cloudflare 边缘，避免部分网络下 QUIC 出现 "no recent network activity"
protocol: http2
tunnel: ${UUID}
credentials-file: ${CRED}

ingress:
  - hostname: ${TUNNEL_HOSTNAME}
    service: http://127.0.0.1:${ORIGIN_PORT}
  - service: http_status:404
EOF

echo ""
echo "已写入: ${CFG_OUT}"
echo "启动隧道:"
echo "  ${REPO_ROOT}/cloudflare/run-tunnel.sh"
echo ""
echo "请在本机启动应用（默认 ${ORIGIN_PORT}）：生产请先 npm run build 再 uvicorn；开发可设 ORIGIN_PORT=5173 后重新运行本脚本。"
