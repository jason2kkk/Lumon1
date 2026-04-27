#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLOUDFLARED="${CLOUDFLARED:-${HOME}/.local/bin/cloudflared}"
command -v cloudflared >/dev/null 2>&1 && CLOUDFLARED="$(command -v cloudflared)"
exec "${CLOUDFLARED}" tunnel --config "${REPO_ROOT}/cloudflare/config.yml" run
