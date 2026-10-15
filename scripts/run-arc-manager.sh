#!/usr/bin/env bash
# Run the official ARC release against the Karmada API (Linux/Docker host networking).
# Authentication to GitHub comes from AutoscalingRunnerSet.spec.githubConfigSecret.
# No source checkout, patches, Go build or arc-manager-fixed binary is used.
set -euo pipefail

KUBECONFIG_KARMADA="${KUBECONFIG_KARMADA:-$HOME/karmada-data/karmada-apiserver.config}"
ARC_VERSION="${ARC_VERSION:-0.15.0}"
ARC_IMAGE="ghcr.io/actions/gha-runner-scale-set-controller:${ARC_VERSION}"
ARC_CONTAINER_NAME="${ARC_CONTAINER_NAME:-arc-karmada-controller}"

for tool in docker kubectl; do
    command -v "$tool" >/dev/null || { echo "[ERROR] Required command: $tool" >&2; exit 1; }
done
[[ -f "$KUBECONFIG_KARMADA" ]] || { echo "[ERROR] Kubeconfig not found: $KUBECONFIG_KARMADA" >&2; exit 1; }

# Flatten file-based certs into a private temporary kubeconfig before mounting it.
# Never print the raw kubeconfig or put GitHub credentials in command-line arguments.
umask 077
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT
kubectl --kubeconfig="$KUBECONFIG_KARMADA" config view --minify --flatten --raw > "$TMP_DIR/kubeconfig"

# The image's non-root user can read the bind-mounted file. On the host, the
# containing directory remains mode 0700, inaccessible to other local users.
chmod 644 "$TMP_DIR/kubeconfig"

echo "==> Starting official ARC ${ARC_VERSION} against Karmada"
echo "    Image:      ${ARC_IMAGE}"
echo "    Kubeconfig: ${KUBECONFIG_KARMADA}"
echo "    Container:  ${ARC_CONTAINER_NAME}"

docker run --rm --name="$ARC_CONTAINER_NAME" --network=host \
    --mount "type=bind,src=$TMP_DIR/kubeconfig,dst=/etc/arc/kubeconfig,readonly" \
    --env CONTROLLER_MANAGER_POD_NAMESPACE=arc-systems \
    --env "CONTROLLER_MANAGER_CONTAINER_IMAGE=$ARC_IMAGE" \
    --env 'NO_PROXY=*' --env 'no_proxy=*' \
    "$ARC_IMAGE" \
    --auto-scaling-runner-set-only \
    --kubeconfig=/etc/arc/kubeconfig \
    --metrics-addr=127.0.0.1:8090 \
    --health-probe-bind-address=127.0.0.1:8091 \
    --listener-metrics-addr=127.0.0.1:8092
