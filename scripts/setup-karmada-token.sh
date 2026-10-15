#!/usr/bin/env bash
# ==============================================================================
# 脚本名称: setup-karmada-token.sh
# 作用: 提取 Karmada 控制面 CA 根证书与 ServiceAccount Token，并在 arc-systems
#       命名空间下生成供 Listener 跨集群安全鉴权的 karmada-token Secret。
# ==============================================================================
set -euo pipefail

KUBECONFIG_KARMADA="${KUBECONFIG_KARMADA:-$HOME/karmada-data/karmada-apiserver.config}"

if [ ! -f "${KUBECONFIG_KARMADA}" ]; then
    echo "[ERROR] 未找到 Karmada Kubeconfig: ${KUBECONFIG_KARMADA}"
    echo "请通过环境变量指定: export KUBECONFIG_KARMADA=/path/to/karmada-apiserver.config"
    exit 1
fi

echo "==> 1. 创建命名空间 arc-systems 与 arc-runners (若不存在)..."
kubectl --kubeconfig="${KUBECONFIG_KARMADA}" apply -f - <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: arc-systems
---
apiVersion: v1
kind: Namespace
metadata:
  name: arc-runners
EOF

echo "==> 2. 确保 Listener ServiceAccount 存在于 arc-systems..."
# 使用固定的控制面身份，避免令牌绑定到升级时可能删除的 ARC 自动生成 SA。
SA_NAME="karmada-listener-sa"
kubectl --kubeconfig="${KUBECONFIG_KARMADA}" -n arc-systems create sa "${SA_NAME}" --dry-run=client -o yaml | kubectl --kubeconfig="${KUBECONFIG_KARMADA}" apply -f -

# 授权由 manifests/03-rbac.yaml 的最小权限 Role/RoleBinding 提供。
# 不再给 Listener 绑定 namespace admin。

echo "==> 3. 提取 Karmada 控制面 CA 根证书..."
umask 077
TMP_DIR=$(mktemp -d)
trap 'rm -rf "${TMP_DIR}"' EXIT

kubectl --kubeconfig="${KUBECONFIG_KARMADA}" config view --minify --flatten --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}' | base64 -d > "${TMP_DIR}/ca.crt"

echo "==> 4. 为 ServiceAccount 请求 Token（实际有效期由 API Server 决定）..."
TOKEN_DURATION="${TOKEN_DURATION:-24h}"
kubectl --kubeconfig="${KUBECONFIG_KARMADA}" -n arc-systems create token "${SA_NAME}" --duration="${TOKEN_DURATION}" > "${TMP_DIR}/token"

echo "==> 5. 在 arc-systems 中创建/更新 karmada-token Secret..."
kubectl --kubeconfig="${KUBECONFIG_KARMADA}" -n arc-systems create secret generic karmada-token \
  --from-file=ca.crt="${TMP_DIR}/ca.crt" \
  --from-file=token="${TMP_DIR}/token" \
  --dry-run=client -o yaml | kubectl --kubeconfig="${KUBECONFIG_KARMADA}" apply -f -

echo "==> [SUCCESS] karmada-token Secret 初始化完成！"
kubectl --kubeconfig="${KUBECONFIG_KARMADA}" -n arc-systems get secret karmada-token
