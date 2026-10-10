#!/usr/bin/env bash
# ===========================================================================
# ZY-Agents · 密钥生成脚本（bootstrap）
# ---------------------------------------------------------------------------
# 为裸进程部署生成全部所需密钥并写入 ./.env（模板 .env.example）。
# 生成密钥：
#   JWT_SIGNING_SECRET / KERNEL_HOST_TOKEN
#   KERNEL_MODEL_GATEWAY_SECRET / KERNEL_TOOL_GATEWAY_SECRET / ARTIFACT_URL_SECRET
#
# 用法：
#   ./deploy/bootstrap/generate-secrets.sh [路径/to/.env]
#   默认写到仓库根 ./.env（不入库）。
#
# 安全说明：生成的密钥为 48 字节 base64（≥ 256-bit 熵）。网关两类密钥
# 各自独立生成（域分离：模型/工具 token 不可互用）。
# ===========================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

ENV_FILE="${1:-$REPO_ROOT/.env}"
TEMPLATE="${2:-$REPO_ROOT/.env.example}"

if [ ! -f "$TEMPLATE" ]; then
  echo "错误：未找到模板 $TEMPLATE" >&2
  exit 1
fi

gen() {
  # 48 random bytes → base64（含 padding 适量 trim）；至少 256-bit 熵
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 48 | tr -d '\n' | tr -d '=' 
  else
    head -c 48 /dev/urandom | base64 | tr -d '\n' | tr -d '='
  fi
}

apply_env() {
  local key="$1" value="$2"
  if grep -qE "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i.bak -E "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    rm -f "${ENV_FILE}.bak"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

# 基于模板生成初始 .env（保留注释），随后回填生成的密钥
if [ ! -f "$ENV_FILE" ]; then
  cp "$TEMPLATE" "$ENV_FILE"
fi

apply_env "JWT_SIGNING_SECRET"            "$(gen)"
apply_env "KERNEL_HOST_TOKEN"             "$(gen)"
apply_env "KERNEL_MODEL_GATEWAY_SECRET"   "$(gen)"
apply_env "KERNEL_TOOL_GATEWAY_SECRET"    "$(gen)"
apply_env "ARTIFACT_URL_SECRET"           "$(gen)"

echo "✅ 密钥已生成并写入 $ENV_FILE"
echo "   生成项：JWT_SIGNING_SECRET / KERNEL_HOST_TOKEN / KERNEL_MODEL_GATEWAY_SECRET / KERNEL_TOOL_GATEWAY_SECRET / ARTIFACT_URL_SECRET"
echo "   ⚠️ 请确保 $ENV_FILE 已加入 .gitignore（不应入库）。"
