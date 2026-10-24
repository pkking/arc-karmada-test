# ARC + Karmada 多集群 CI/CD 算力底座复现与配置指南

本项目是基于 **Actions Runner Controller (ARC)** 与 **Karmada 原生多集群联邦** 的多集群 CI/CD 算力底座验证项目（Proof of Concept, PoC），不是生产就绪保证。

**当前版本：官方 ARC `0.15.0`，不再使用源码补丁或自编译控制器。** 2026-10-15 已在 Karmada `v1.19.0` / Kubernetes `v1.37.0` 的两个 Kind 成员集群上重新验证。实测证据与限制见 [`docs/arc-0.15.0-validation.md`](docs/arc-0.15.0-validation.md)。

本指南提供了完整的配置说明与端到端复现步骤，指导架构师与运维专家从零搭建并验证该架构。

系统边界、Before / After 对比、联邦架构、用户故事及实测证据见[《ARC + Karmada 联邦调度执行子系统设计与验证说明》](docs/KARMADA-ARC-SUBSYSTEM-DESIGN.md)。独立 Agent/开发者在修改 Manifest 或脚本前，还应阅读[实现契约](docs/IMPLEMENTATION-CONTRACT.md)、[验收契约与追踪矩阵](docs/ACCEPTANCE-CONTRACT.md)和[Agent 开发规范](AGENTS.md)。这些文件约束当前 PoC 行为，**不代表全新 Kind 环境重建或生产能力已通过验证**。

---

## 目录

- [一、 架构总览与核心优势](#一-架构总览与核心优势)
- [二、 核心机制深度解析](#二-核心机制深度解析)
- [三、 环境依赖与前置条件](#三-环境依赖与前置条件)
- [四、 目录结构说明](#四-目录结构说明)
- [五、 逐步复现操作指南](#五-逐步复现操作指南)
  - [步骤 1：部署 Karmada 控制面与成员集群](#步骤-1部署-karmada-控制面与成员集群)
  - [步骤 2：安装 ARC 核心 CRD](#步骤-2安装-arc-核心-crd)
  - [步骤 3：创建命名空间与基础鉴权 Secret](#步骤-3创建命名空间与基础鉴权-secret)
  - [步骤 4：应用 Karmada Pod 状态无损聚合器](#步骤-4应用-karmada-pod-状态无损聚合器)
  - [步骤 5：初始化 Listener 跨集群认证凭据](#步骤-5初始化-listener-跨集群认证凭据)
  - [步骤 6：配置 Karmada 联邦分发策略](#步骤-6配置-karmada-联邦分发策略)
  - [步骤 7：应用 Runner RBAC 权限](#步骤-7应用-runner-rbac-权限)
  - [步骤 8：声明 AutoscalingRunnerSet](#步骤-8声明-autoscalingrunnerset)
  - [步骤 9：运行官方 ARC Controller](#步骤-9运行官方-arc-controller)
- [六、 运行验证与效果分析](#六-运行验证与效果分析)
- [七、 常见故障排查手册 (FAQ)](#七-常见故障排查手册-faq)
- [八、 AI 协作复现信息与初始 Prompt](#八-ai-协作复现信息与初始-prompt)

---

## 一、 架构总览与核心优势

### 1. 传统独立部署与官方 HA 方案的局限
- **维护管理成本高**：在十多个集群中独立平铺部署一套 ARC 时，每个集群必须定义专有 Runner Label（如 `runs-on: cluster-bj-01`、`runs-on: cluster-sh-02`），开发者需在 Workflow 中手工指定目标集群，导致集群间资源严重倾斜（忙闲不均）。
- **官方多集群方案限制**：ARC 官方提供的多集群/高可用方案缺乏跨集群全局容量感知调度能力，且强依赖硬件规格严格同构。
- **并发长轮询 API 限流**：每个集群独立运行一套 Autoscaling Listener，多集群并发对 GitHub 进行长轮询（Long Polling），极易触碰 GitHub API 限流配额。
- **虚拟节点（如 Liqo）方案的痛点**：基于 VXLAN/WireGuard 跨集群网络隧道损耗大，虚拟节点掩盖了底层物理硬件状态，故障排查困难。

### 2. 双平面解耦架构设计
本项目采用**“管控控制面集中调谐 + 算力执行面轻量承载”**的双平面解耦方案：

```
                         [ GitHub Actions ]
                                 │
                      (统一 runs-on: karmada-runner)
                                 │
                      ┌───────────▼───────────┐
                      │   Single Listener     │  (运行于成员集群，单一长连接会话)
                      └───────────┬───────────┘
                                  │ (Patch EphemeralRunnerSet)
                      ┌───────────▼───────────┐
                      │ Karmada Control Plane │
                      │ - ARC Controller (集中)│  (直接面向 Karmada 控制面调谐)
                      │  - AutoscalingRunnerSet│
                      │  - EphemeralRunner    │
                      │  - Karmada Scheduler  │  (动态调度与依赖穿透)
                      └──────┬─────────┬──────┘
                             │         │
        [Divided / Weighted] │         │ [Divided / Weighted]
                             ▼         ▼
                      ┌───────────┐ ┌───────────┐
                      │  member1  │ │  member2  │
                      │ Runner Pod│ │ Runner Pod│  (原生网络空间运行，直连 GitHub)
                      │ JIT Secret│ │ JIT Secret│  (瞬时凭证依赖下发，零隧道依赖)
                      └───────────┘ └───────────┘
```

- **统一入口**：开发者在 GitHub Workflow 中仅需指定单一统一标签（如 `runs-on: karmada-runner`）。
- **单会话交互**：全局仅需运行一个 Listener Pod，避免任务争抢与 GitHub 轮询配额消耗。
- **动态算力下发**：Karmada 调度器接管 Runner Pod，根据 placement 与集群状态选择执行集群；多个独立 Pod 的 `Divided / Weighted` 策略不保证每次都均匀分布。
- **零网络隧道损耗**：Runner Pod 原生运行在子集群网络中，出网直接访问 GitHub，无跨集群容器网络损耗。

---

## 二、 核心机制深度解析

本方案保留三项 Karmada 适配机制，第四项历史 ARC 源码修复已由官方 release 替代：

### 机制 ①：Karmada Pod 状态无损聚合器
- **痛点根因**：Karmada 内置默认的 Pod 状态聚合器 `aggregatePodStatus` 仅复制了容器的 `Ready` 和 `State`，但**丢弃了容器名称（`containerStatuses[].name`）与 Conditions（如 `PodReady=True`）**。ARC 控制器在检查 Pod 时，因找不到名为 `runner` 的容器且缺少 Ready 状态，将 Runner 误判为超时未就绪并主动删除。
- **技术实现**：通过声明 Karmada `ResourceInterpreterCustomization`，注入 Lua 脚本无损回传成员集群 Pod 的完整 `status` 对象（见 [`manifests/01-karmada-interpreter.yaml`](manifests/01-karmada-interpreter.yaml)）。

### 机制 ②：Listener 跨集群跨平面通信与凭据适配
- **痛点根因**：Listener Pod 运行于成员集群，通过 `InClusterConfig()` 默认与本地集群 API 通信。若直接改写 `KUBERNETES_SERVICE_HOST` 为 Karmada IP，会遭遇两大错误：
  1. **TLS 校验失败**：Karmada API Server 证书的 SAN 仅包含 `karmada-apiserver`、`localhost` 等，直接使用 IP 访问报错 `x509: certificate is valid for ..., not <IP>`；
  2. **401 Unauthorized**：成员集群注入的 ServiceAccount Token 无法通过 Karmada 鉴权。
- **技术实现**：在 AutoscalingRunnerSet 的 `listenerTemplate` 中配置 `hostAliases` 解析 `karmada-apiserver`，并挂载预先生成的控制面 `karmada-token` Secret（含控制面 CA 与需定期轮换的 Token，见 [`manifests/04-autoscalingrunnerset.yaml`](manifests/04-autoscalingrunnerset.yaml)）。

### 机制 ③：瞬时 JIT 凭证与 Runner Pod 自动绑定下发
- **痛点根因**：GitHub Actions 任务分发时，ARC 会在控制面动态生成一次性瞬时 JIT Token Secret。若 Runner Pod 与其引用的 Secret 分离调度到了不同集群，Runner 将无法获取 Token 而退出。
- **技术实现**：配置针对 `arc-runners` 命名空间中 Pod 的 `PropagationPolicy`，开启依赖自动追踪（`propagateDeps: true`）与分片调度策略（`Divided / Weighted`）。Karmada 会自动分析 Pod 环境变量中通过 `valueFrom.secretKeyRef` 引用的 JIT Secret，按 Pod 的 placement 将依赖下发到对应成员集群（不是跨资源原子事务，Pod 启动时可能短暂等待 Secret）（见 [`manifests/02-propagation-policies.yaml`](manifests/02-propagation-policies.yaml)）。

### 机制 ④：使用官方 ARC 0.15.0，移除历史源码补丁
- **历史问题**：旧方案中，Secret 上的 Karmada 元数据触发控制器反复 Patch 和 Listener 重建。
- **当前实现**：官方 `gha-runner-scale-set-0.15.0` 已包含相关上游变更，按现存与期望 metadata 合并后比较，并单独比较 Secret Data。直接运行官方镜像，不再 cherry-pick、构建 `arc-manager-fixed` 或伪装 `build.Version`。
- **版本一致性**：Controller/Listener 使用 `0.15.0`，本仓库四个 CRD 来自同一 release；手写 AutoscalingRunnerSet 必须带 `app.kubernetes.io/version: "0.15.0"`，缺失或不兼容会被 ARC 删除。参见[静态核对](docs/arc-0.15.0-assessment.md)与[运行验证](docs/arc-0.15.0-validation.md)。

---

## 三、 环境依赖与前置条件

以下是 **2026-10-15 实际核对并运行验证的版本**，不是最低版本要求，也不表示其他版本已通过兼容性验证。部署组件需显式固定版本，避免依赖 `latest` 或本机工具的默认值。

### 部署组件

| 组件 | 当前版本 / 镜像 | 说明 |
| :--- | :--- | :--- |
| **ARC Controller / Listener** | `ghcr.io/actions/gha-runner-scale-set-controller:0.15.0` | 官方 release，无源码补丁；两者使用同一镜像 |
| **ARC CRD / RunnerSet 标签** | `0.15.0` | 四个 CRD 来自 `gha-runner-scale-set-0.15.0`；`app.kubernetes.io/version` 同步为 `0.15.0` |
| **GitHub Actions Runner** | `ghcr.io/actions/actions-runner:2.337.0` | 与 ARC 独立版本，不使用 `latest` |
| **Karmada** | `v1.19.0` | aggregated-apiserver、controller-manager、scheduler、webhook 均为 `docker.io/karmada/<组件名>:v1.19.0` |
| **Kind 宿主 / 成员 Kubernetes** | `kindest/node:v1.37.0` | 三个 Kind 集群均使用该版本；镜像 digest 见下方创建命令 |
| **Karmada 控制面 Kubernetes 组件** | `registry.k8s.io/kube-apiserver:v1.37.0`、`registry.k8s.io/kube-controller-manager:v1.37.0` | 与宿主 / 成员集群版本一致 |
| **Karmada etcd** | `registry.k8s.io/etcd:3.7.0-0` | 本次环境的实际运行镜像 |

### 本机操作工具

| 工具 | 实测版本 | 说明 |
| :--- | :--- | :--- |
| **Docker Client / Server** | `29.8.2` / `29.8.2` | 本机 daemon 为 Docker Desktop；Controller 使用 Docker host 网络；镜像导出使用 `--platform` |
| **Kind CLI** | `v0.33.0` | 与节点 Kubernetes 镜像版本分开固定 |
| **kubectl** | `v1.36.1` | 当前客户端；服务端为 `v1.37.0`，不要把旧 `v1.28.0+` 当作本环境已验证范围 |
| **Python** | `3.13.16` | 执行运行验证和离线护栏测试，无第三方 Python 依赖 |
| **GitHub CLI** | `2.102.0` | 触发及检查真实 GitHub Actions workflow |
| **karmadactl** | 本机报告 `v0.0.0-master`，commit/build 信息 unknown | 仅如实记录当前工具，不能作为可复现发布版本；从零搭建建议使用与服务端匹配的 `v1.19.0` release，并显式固定下方镜像和 CRD。更换 CLI 后需另行验证初始化步骤 |

无需安装 Go，也无需克隆 ARC 源码。GitHub 认证需 PAT（目标仓库权限）或 GitHub App 密钥；凭据保存在 Kubernetes Secret，不提交到 Git。

---

## 四、 目录结构说明

```
arc-karmada-test/
├── .github/
│   └── workflows/
│       └── test-arc.yml                  # 验证用 Workflow (支持多任务并发触发)
├── README.md                             # 本复现指南
├── crds/                                 # ARC 4 个核心 CRD 定义
│   ├── actions.github.com_autoscalinglisteners.yaml
│   ├── actions.github.com_autoscalingrunnersets.yaml
│   ├── actions.github.com_ephemeralrunners.yaml
│   └── actions.github.com_ephemeralrunnersets.yaml
├── manifests/
│   ├── 00-namespaces.yaml               # 业务与系统命名空间 (arc-systems, arc-runners)
│   ├── 01-karmada-interpreter.yaml      # 核心机制 ①：Karmada Pod 状态无损聚合器
│   ├── 02-propagation-policies.yaml     # 核心机制 ③：分发策略 (Secret, Listener, Runner Pod+JIT)
│   ├── 03-rbac.yaml                     # 无权限 Runner SA + Listener 控制面最小权限
│   └── 04-autoscalingrunnerset.yaml     # 核心机制 ②：AutoscalingRunnerSet 及 Listener 凭据注入
├── docs/                                # 版本评估、运行验证及脱敏证据
└── scripts/
    ├── setup-karmada-token.sh           # 控制面 CA 与 Listener 认证 Secret（需轮换）
    ├── run-arc-manager.sh               # Docker 运行官方 ARC 0.15.0，无补丁
    ├── run-arc-controller.sh            # 兼容入口，符号链接至 run-arc-manager.sh
    └── verify-arc.py                    # Listener 稳定性、真实 workflow、分发与清理回归
```

---

## 五、 逐步复现操作指南

### 步骤 1：部署 Karmada 控制面与成员集群

如已存在 Karmada 环境，请跳过此步骤并确保 `KUBECONFIG` 指向 Karmada APIServer。

#### 1.1 使用 Kind 创建三个集群
```bash
# 显式固定本次实测节点镜像，不依赖 Kind CLI 的默认 Kubernetes 版本
KIND_NODE_IMAGE="kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5"

# 创建 Karmada 宿主集群和两个成员执行集群
kind create cluster --name karmada-host --image="${KIND_NODE_IMAGE}"
kind create cluster --name member1 --image="${KIND_NODE_IMAGE}"
kind create cluster --name member2 --image="${KIND_NODE_IMAGE}"
```

#### 1.2 在 karmada-host 上初始化 Karmada

本机开发版 `karmadactl` 的默认镜像/CRD 指向 `v0.0.0-master`，不能直接使用无版本参数的 `init`。以下显式对齐现有环境的组件版本；本次回归复用了已有集群，**没有重建集群验证完整初始化流程**，使用其他 CLI 版本时先核对 `karmadactl init --help`：

```bash
KARMADA_VERSION="v1.19.0"
KUBERNETES_VERSION="v1.37.0"

karmadactl init --kubeconfig="$HOME/.kube/config" --context=kind-karmada-host \
  --crds="https://github.com/karmada-io/karmada/releases/download/${KARMADA_VERSION}/crds.tar.gz" \
  --karmada-aggregated-apiserver-image="docker.io/karmada/karmada-aggregated-apiserver:${KARMADA_VERSION}" \
  --karmada-controller-manager-image="docker.io/karmada/karmada-controller-manager:${KARMADA_VERSION}" \
  --karmada-scheduler-image="docker.io/karmada/karmada-scheduler:${KARMADA_VERSION}" \
  --karmada-webhook-image="docker.io/karmada/karmada-webhook:${KARMADA_VERSION}" \
  --karmada-apiserver-image="registry.k8s.io/kube-apiserver:${KUBERNETES_VERSION}" \
  --karmada-kube-controller-manager-image="registry.k8s.io/kube-controller-manager:${KUBERNETES_VERSION}" \
  --kube-image-tag="${KUBERNETES_VERSION}" \
  --etcd-image="registry.k8s.io/etcd:3.7.0-0"
```
安装完成后，Karmada 控制面的 Kubeconfig 默认生成在 `$HOME/.kube/karmada.config` 或自定义路径（如 `$HOME/karmada-data/karmada-apiserver.config`）。

#### 1.3 将成员集群加入 Karmada
```bash
# 获取 member1 和 member2 的内部 kubeconfig，并加入联邦
karmadactl --kubeconfig=$HOME/karmada-data/karmada-apiserver.config join member1 \
  --cluster-kubeconfig=$HOME/karmada-data/member1-internal.config

karmadactl --kubeconfig=$HOME/karmada-data/karmada-apiserver.config join member2 \
  --cluster-kubeconfig=$HOME/karmada-data/member2-internal.config

# 验证成员集群状态
kubectl --kubeconfig=$HOME/karmada-data/karmada-apiserver.config get clusters
```
预期输出：
```
NAME      VERSION   MODE   READY   AGE
member1   v1.37.0   Push   True    ...
member2   v1.37.0   Push   True    ...
```

---

### 步骤 2：安装 ARC 核心 CRD

在 **Karmada 控制面** 安装 ARC 运行时所需的 4 个核心 CRD：

```bash
KUBECONFIG_KARMADA="$HOME/karmada-data/karmada-apiserver.config"

kubectl --kubeconfig="${KUBECONFIG_KARMADA}" apply --server-side -f crds/
```

升级已有环境前先备份 CRD/ARC CR、确认无运行中任务并停掉旧 Controller。若 server-side apply 提示旧字段所有权冲突，审阅差异后可**仅对这四个 CRD**使用 `--force-conflicts --field-manager=arc-release-upgrade`；不要盲目覆盖其他资源。Helm 升级也不应被假定会自动升级已有 CRD。

验证 CRD 安装状态：
```bash
kubectl --kubeconfig="${KUBECONFIG_KARMADA}" get crd | grep actions.github.com
```

---

### 步骤 3：创建命名空间与基础鉴权 Secret

#### 3.1 创建命名空间
```bash
kubectl --kubeconfig="${KUBECONFIG_KARMADA}" apply -f manifests/00-namespaces.yaml
```

#### 3.2 创建 GitHub 访问凭据 Secret
在 `arc-runners` 命名空间下创建与 GitHub 交互的 Secret（包含 PAT 或 GitHub App 私钥）：
```bash
# 使用个人访问令牌 (PAT)
GITHUB_TOKEN="ghp_yourPersonalAccessTokenHere"

kubectl --kubeconfig="${KUBECONFIG_KARMADA}" -n arc-runners create secret generic arc-github-secret \
  --from-literal=github_token="${GITHUB_TOKEN}" \
  --dry-run=client -o yaml | kubectl --kubeconfig="${KUBECONFIG_KARMADA}" apply -f -
```

---

### 步骤 4：应用 Karmada Pod 状态无损聚合器

此步骤解决 **机制 ①**，防止由于状态丢失导致 Runner 被系统误删除：

```bash
kubectl --kubeconfig="${KUBECONFIG_KARMADA}" apply -f manifests/01-karmada-interpreter.yaml
```

验证自定义解释器已生效：
```bash
kubectl --kubeconfig="${KUBECONFIG_KARMADA}" get resourceinterpretercustomization pod-status-interpreter
```

---

### 步骤 5：初始化 Listener 跨集群认证凭据

此步骤解决 **机制 ②** 中的凭据生成问题。使用固定控制面身份 `arc-systems/karmada-listener-sa`；先应用最小权限 RBAC，再提取 CA 并请求 Token：

```bash
export KUBECONFIG_KARMADA="$HOME/karmada-data/karmada-apiserver.config"
kubectl --kubeconfig="${KUBECONFIG_KARMADA}" apply -f manifests/03-rbac.yaml
bash scripts/setup-karmada-token.sh
```

该脚本会在 `arc-systems` 创建包含 `ca.crt` 和 `token` 的 `karmada-token` Secret。默认请求 `24h`，可用 `TOKEN_DURATION` 调整，实际到期时间由 API Server 决定。**到期前重新运行脚本轮换**；本 PoC 尚未实现自动轮换。不要使用绑定到旧 Listener 自动生成 SA 的 Token。

升级旧环境时，移除旧 `arc-runners/karmada-listener-sa-admin` RoleBinding（如果存在）；新的清单不再需要旧 `karmada-runner-gha-rs-manager` Role/RoleBinding。Controller 使用 kubeconfig 权限，本 PoC 为管理员；这不是生产最小权限部署。

---

### 步骤 6：配置 Karmada 联邦分发策略

配置分发策略，打通 **机制 ③**（依赖穿透）与基础 Secret 同步：

```bash
kubectl --kubeconfig="${KUBECONFIG_KARMADA}" apply -f manifests/02-propagation-policies.yaml
```

Listener Pod/SA 按稳定的 scale-set labels 匹配，引用的 config Secret 和 Token 自动追踪，不再写死 Listener hash。基础策略同时分发无权限 Runner SA，并仅对显式命名的两个基础资源使用 `conflictResolution: Overwrite` 接管旧副本；请确认成员集群中同名资源可以被本方案管理。

验证策略就绪：
```bash
kubectl --kubeconfig="${KUBECONFIG_KARMADA}" get propagationpolicy -A
```

---

### 步骤 7：应用 Runner RBAC 权限

配置无权限 Runner SA，以及 Listener 控制面身份的最小权限（对应官方 0.15.0 `rulesForListenerRole`：Patch 指定 EphemeralRunnerSet、Get/Patch EphemeralRunner、Patch 其 status）。不再绑定 namespace admin：

```bash
kubectl --kubeconfig="${KUBECONFIG_KARMADA}" apply -f manifests/03-rbac.yaml
```

---

### 步骤 8：声明 AutoscalingRunnerSet

在应用清单前，请确认 `manifests/04-autoscalingrunnerset.yaml` 中的配置项：
1. `githubConfigUrl`: 替换为您的测试仓库地址（如 `https://github.com/pkking/arc-karmada-test`）；
2. `hostAliases[0].ip`: 替换为您当前环境中 Karmada 控制面的访问 IP（如 Kind 网桥 IP `172.22.0.4` 或宿主机 IP）；
3. `app.kubernetes.io/version` 必须与 Controller 一致（当前 `0.15.0`）；Runner 镜像固定为 `2.337.0`；
4. 检查无误后，应用清单：

```bash
kubectl --kubeconfig="${KUBECONFIG_KARMADA}" apply -f manifests/04-autoscalingrunnerset.yaml
```

---

### 步骤 9：运行官方 ARC Controller

使用官方镜像 `ghcr.io/actions/gha-runner-scale-set-controller:0.15.0`，直接面向 Karmada 调谐。脚本通过只读挂载的 kubeconfig 访问控制面，GitHub 认证来自 `spec.githubConfigSecret`，不需要给启动脚本传 PAT。

```bash
export KUBECONFIG_KARMADA="$HOME/karmada-data/karmada-apiserver.config"
# 先停掉旧 Controller，避免新旧版本同时调谐。
bash scripts/run-arc-controller.sh
```

脚本默认容器名 `arc-karmada-controller`，前台运行。另一个终端可用 `docker logs arc-karmada-controller` 检查日志，`docker stop arc-karmada-controller` 停止。`ARC_VERSION` 可覆盖默认版本，但必须同时核对 CRD 与 RunnerSet 标签；不要只改镜像版本。

**Kind 拉镜像限制**：若成员节点的 containerd 代理失效，先在宿主机 `docker pull` 官方 Controller/Runner 镜像，再预加载到两个成员节点。多架构镜像的 `kind load docker-image` 若报缺失 digest，可用 `docker image save --platform linux/amd64` 后通过 `docker exec -i <member>-control-plane ctr -n=k8s.io images import --platform linux/amd64 --digests -` 导入（需支持 `--platform` 的 Docker，架构按实际修改）。这只是预加载官方镜像，不是重新构建。

控制器启动成功后，会自动检测 Karmada 上的 `AutoscalingRunnerSet` 并拉起对应的 Listener Pod：
```
==> Starting official ARC 0.15.0 against Karmada
    Image:      ghcr.io/actions/gha-runner-scale-set-controller:0.15.0
...
AutoscalingRunnerSet  Autoscaling runner set is up to date and ready  {"version":"0.15.0",...}
```

此时在控制面查看 Listener 状态：
```bash
kubectl --kubeconfig="${KUBECONFIG_KARMADA}" -n arc-systems get pods
```
预期输出：
```
NAME                               READY   STATUS    RESTARTS   AGE
karmada-runner-fdfdcc69-listener   1/1     Running   0          2m
```

Listener Ready 且已成功建立 GitHub session 后，监听池就绪。`minRunners: 0` 时没有常驻 Runner；实际任务成功执行才是端到端验收依据。

---

## 六、 运行验证与效果分析

### 自动化回归：功能与单轮成员覆盖分开验收

先确保 Listener Ready、成员集群上下文 `kind-member1` / `kind-member2` 可访问，且 `gh` 已认证。本仓库将验收拆为 [T0 静态 / T1 功能 / T2 定向成员 / T3 统计均衡](docs/ACCEPTANCE-CONTRACT.md)，避免将 **Job 成功** 与 **同一轮任务覆盖两个成员** 混为一谈。

```bash
# T0：离线守护测试
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
bash -n scripts/*.sh

# T1：三个 GitHub Job 功能成功、JIT/状态和本轮清理；
# 保存 clusterCoveragePassed，但不因为单轮 3:0 分布而认定功能失败
python3 scripts/verify-arc.py --dispatch --coverage-mode report-only --duration 180 \
  --output /tmp/arc-015-validation/t1-functional.json

# 兼容原来的单轮两个成员均需观测到 Runner 的综合回归；
# 默认即 require，显式写出便于审计
python3 scripts/verify-arc.py --dispatch --coverage-mode require --duration 180 \
  --output /tmp/arc-015-validation/coverage-experiment.json
```

采样器会短暂修改并恢复 Listener config Secret 和 ARC 自动生成的 Role 的测试 annotation，随后检查 Listener UID、Secret ResourceVersion、Runner/JIT、控制面 Pod 状态和本轮清理。JSON 不包含 Secret Data/Token，但仍须核对脱敏。**如果 GitHub Job 成功而单轮全部落在 member1，T1 可以通过，require 模式的成员覆盖实验应失败**，两项结果都保留。

T2 不依赖随机调度：经审阅后暂时将 Runner Policy 的 placement **分别定向** member1/member2，每次用 `--required-contexts kind-member1` 或 `kind-member2`、`--coverage-mode require` 验证该成员。测试完成后必须恢复 `clusterNames: [member1, member2]` 并保留恢复后的证据。T3 资源均衡及排队性能须另设统计实验，不由该脚本证明。

可用 `--repo`、`--kubeconfig`、`--contexts`、`--duration` 调整环境；`180` 秒为采样窗口而非生产 SLA。不要在有无关并发任务的共享池上直接运行清理断言。GitHub dispatch 超时后先发现已有 run，不可盲目再次派发。原型部署后另需真实验证，**未运行不能标记为已通过**。

### 1. 手动触发并发 Workflow
在测试仓库中触发 [`.github/workflows/test-arc.yml`](.github/workflows/test-arc.yml)（包含 3 个并发 Job，指定 `runs-on: karmada-runner`）：

```bash
gh workflow run test-arc.yml --repo pkking/arc-karmada-test
```

### 2. 观察控制面与成员集群状态

在任务执行期间，分别查看成员集群 `member1` 与 `member2` 的 Pod 分布：

```bash
# 查看 member1 上的 Runner Pod
kubectl --context=kind-member1 -n arc-runners get pods

# 查看 member2 上的 Runner Pod
kubectl --context=kind-member2 -n arc-runners get pods
```

**本次 ARC 0.15.0 实测分摊（首轮）：**
```
=== member1 集群节点 ===
NAME                                    READY   STATUS    RESTARTS   AGE
karmada-runner-runner-k8bcd           1/1     Running   0          ...
karmada-runner-runner-kc2fs           1/1     Running   0          ...

=== member2 集群节点 ===
NAME                                    READY   STATUS    RESTARTS   AGE
karmada-runner-runner-twx87           1/1     Running   0          ...
```
- **验证结论 1**：官方 ARC 0.15.0 成功执行三个并发 job；本轮在 `member1` 观察到 2 个 Runner、`member2` 观察到 1 个，不表示未来每轮必定按 2:1 分配。
- **验证结论 2**：每个就绪 Runner 的 JIT Secret 在对应成员集群存在，控制面回传了名为 `runner` 的容器状态；环境中的镜像拉取代理问题通过预加载官方镜像解决。
- **验证结论 3**：在观测窗口结束时，本轮 Runner Pod、EphemeralRunner 及观察到的 JIT Secret 已清理；没有宣称所有历史资源、Work 或故障路径都“即时 Zero Leaks”。详见运行报告的已验证范围与历史残留说明。

---

## 七、 常见故障排查手册 (FAQ)

### Q1: Listener Pod 报错 `x509: certificate is valid for ..., not 172.22.0.4`
- **根因**：Karmada APIServer 证书的 SAN 列表中未登记 IP 地址，只包含域名（如 `karmada-apiserver`、`localhost`）。
- **解决办法**：在 [`manifests/04-autoscalingrunnerset.yaml`](manifests/04-autoscalingrunnerset.yaml) 中保留 `hostAliases` 配置，将 `karmada-apiserver` 映射到宿主机/容器网桥 IP，并将环境变量 `KUBERNETES_SERVICE_HOST` 设为 `karmada-apiserver`。

### Q2: Listener Pod 报错 `Unauthorized (401)`
- **根因**：Listener 默认使用的是所在成员集群本地签发的 ServiceAccount Token，Karmada 控制面无法识别。
- **解决办法**：运行 `scripts/setup-karmada-token.sh` 脚本，将 Karmada 控制面颁发的 Token 挂载并覆盖容器内的 `/var/run/secrets/kubernetes.io/serviceaccount` 目录。

### Q3: Runner Pod 启动后很快被 Terminating 删除，ARC 日志报 `runner pod not ready`
- **根因**：Karmada 原生状态聚合丢弃了容器名与 Ready Condition。
- **解决办法**：确保已成功应用 [`manifests/01-karmada-interpreter.yaml`](manifests/01-karmada-interpreter.yaml) 自定义 Lua 聚合器，且 `ResourceInterpreterCustomization` 处于就绪状态。

### Q4: Listener Pod 陷入无限删除与重启死循环，日志显示 `Listener config secret changed`
- **历史表现**：旧 PoC 中带 `0.14.2` 版本标识的控制器曾因 Secret 上的 Karmada 元数据反复 Patch。不能把 `#4492` 单独描述为忽略这些元数据的修复；最终采用的是官方 0.15.0 的合并比较实现，详见[版本核对](docs/arc-0.15.0-assessment.md)。
- **解决办法**：使用官方 ARC `0.15.0` 与匹配 CRD/版本标签（见 [`scripts/run-arc-manager.sh`](scripts/run-arc-manager.sh)），不要再合并补丁或伪装 build.Version。轮换 config Secret 本身的内容/metadata 可能引起一次正常替换；验收关注的是稳定后仍反复 Patch/重建，而不是要求所有配置变化都永不重启。

### Q5: Runner Pod 报错找不到 Secret 或鉴权失败退出
- **根因**：Karmada 未开启依赖自动穿透，导致 Pod 被调度到了子集群，但其绑定的 JIT Secret 遗留在控制面。
- **解决办法**：检查 `manifests/02-propagation-policies.yaml` 中的 `arc-runner-pod-propagation`，必须显式声明 `propagateDeps: true`。

---

## 八、 AI 协作复现信息与初始 Prompt

本项目全流程采用 AI 智能体（Agentic AI）以结对编程与自动化运维排障模式协作完成。相关开发工具、模型及最初驱动 Prompt 归档如下，完整复现演进记录可参考 [AI_REPRODUCTION.md](AI_REPRODUCTION.md)。

### 1. 开发工具与模型配置
- **开发协作工具**：**Antigravity CLI (`agy`)**
- **底层驱动模型**：**Gemini 3.8 flash**
- **协作模式**：多工具自主调用、代码检索与补丁编写、实时集群排障与配置编排

### 2. 最初的核心驱动 Prompt
> **最初需求与可行性论证 Prompt**：
> ```text
> 我期望arc这个项目可以对接使用karmada 以便能使用多个集群的资源，之前我们使用过liqo，这是将某个集群模拟成node，不太符合要求，更期望使用karmada，先分析下这个方案是否可行，以及是否可以快速poc
> ```

> **核心业务场景与设计目标 Prompt**：
> ```text
> 我期望的测试场景：一个统一的runner label，用户不感知底层有多个集群，github也不知道底层有多个集群，只需要把任务发给一个listener，这个listener把任务投递给karmada，由karmada负责找有资源的集群，执行任务
> ```

通过上述 Prompt 驱动，Agent 自动化完成了架构可行性论证、双平面架构设计、KinD 多集群环境搭建、三大原生协同断裂问题的定位与修复（Lua 状态聚合、Listener 跨面 TLS 与 Token 注入、JIT Secret 依赖穿透），以及旧版本阶段的 ARC 源码排障。**历史补丁/编译流程已退役，当前复现使用官方 0.15.0 镜像。**