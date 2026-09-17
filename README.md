# ARC + Karmada 多集群 CI/CD 算力底座复现与配置指南

本项目是基于 **Actions Runner Controller (ARC)** 与 **Karmada 原生多集群联邦** 打造的生产级多集群 CI/CD 算力底座验证项目（Proof of Concept, PoC）。

本指南提供了完整的配置说明与端到端复现步骤，指导架构师与运维专家从零搭建并验证该架构。

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
  - [步骤 9：运行修复版 ARC Controller Manager](#步骤-9运行修复版-arc-controller-manager)
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
                      │ JIT Secret│ │ JIT Secret│  (瞬时凭证原子下发，零隧道依赖)
                      └───────────┘ └───────────┘
```

- **统一入口**：开发者在 GitHub Workflow 中仅需指定单一统一标签（如 `runs-on: karmada-runner`）。
- **单会话交互**：全局仅需运行一个 Listener Pod，避免任务争抢与 GitHub 轮询配额消耗。
- **动态算力下发**：Karmada 调度器接管 Runner Pod，按各集群资源水位自动分摊调度。
- **零网络隧道损耗**：Runner Pod 原生运行在子集群网络中，出网直接访问 GitHub，无跨集群容器网络损耗。

---

## 二、 核心机制深度解析

为了打通 ARC 与 Karmada 的联邦协同，本方案攻克并沉淀了四项关键工程机制：

### 机制 ①：Karmada Pod 状态无损聚合器
- **痛点根因**：Karmada 内置默认的 Pod 状态聚合器 `aggregatePodStatus` 仅复制了容器的 `Ready` 和 `State`，但**丢弃了容器名称（`containerStatuses[].name`）与 Conditions（如 `PodReady=True`）**。ARC 控制器在检查 Pod 时，因找不到名为 `runner` 的容器且缺少 Ready 状态，将 Runner 误判为超时未就绪并主动删除。
- **技术实现**：通过声明 Karmada `ResourceInterpreterCustomization`，注入 Lua 脚本无损回传成员集群 Pod 的完整 `status` 对象（见 [`manifests/01-karmada-interpreter.yaml`](manifests/01-karmada-interpreter.yaml)）。

### 机制 ②：Listener 跨集群跨平面通信与凭据适配
- **痛点根因**：Listener Pod 运行于成员集群，通过 `InClusterConfig()` 默认与本地集群 API 通信。若直接改写 `KUBERNETES_SERVICE_HOST` 为 Karmada IP，会遭遇两大错误：
  1. **TLS 校验失败**：Karmada API Server 证书的 SAN 仅包含 `karmada-apiserver`、`localhost` 等，直接使用 IP 访问报错 `x509: certificate is valid for ..., not <IP>`；
  2. **401 Unauthorized**：成员集群注入的 ServiceAccount Token 无法通过 Karmada 鉴权。
- **技术实现**：在 AutoscalingRunnerSet 的 `listenerTemplate` 中配置 `hostAliases` 解析 `karmada-apiserver`，并挂载预先生成的控制面 `karmada-token` Secret（含控制面 CA 与长效 Token，见 [`manifests/04-autoscalingrunnerset.yaml`](manifests/04-autoscalingrunnerset.yaml)）。

### 机制 ③：瞬时 JIT 凭证与 Runner Pod 自动绑定下发
- **痛点根因**：GitHub Actions 任务分发时，ARC 会在控制面动态生成一次性瞬时 JIT Token Secret。若 Runner Pod 与其引用的 Secret 分离调度到了不同集群，Runner 将无法获取 Token 而退出。
- **技术实现**：配置针对 `arc-runners` 命名空间中 Pod 的 `PropagationPolicy`，开启依赖自动追踪（`propagateDeps: true`）与分片调度策略（`Divided / Weighted`）。Karmada 会自动分析 Pod 环境变量中通过 `valueFrom.secretKeyRef` 引用的 JIT Secret，将其与 Pod 原子下发至同一成员集群（见 [`manifests/02-propagation-policies.yaml`](manifests/02-propagation-policies.yaml)）。

### 机制 ④：ARC 控制器 Secret 调谐死循环修复
- **痛点根因**：在官方发布版 `0.14.2` 中，`AutoscalingListenerReconciler` 在比较 `desiredSecret` 与现有 Secret 时，未剥离 Karmada 自动注入的 Labels 与 Annotations，误判 Secret 发生变更不断调用 `r.Patch` 更新 Secret，导致 Secret 的 `ResourceVersion` 递增并触发 `Listener config secret changed, restarting listener pod`，Listener 陷入无休止重启。
- **技术实现**：合并 ARC 上游 Master 分支修复补丁（PR `#4492` 与 `#4516`），在编译控制器时显式注入当前 Helm/CRD 匹配的版本号 `-ldflags "-X github.com/actions/actions-runner-controller/build.Version=0.14.2"`（见 [`scripts/run-arc-manager.sh`](scripts/run-arc-manager.sh)）。

---

## 三、 环境依赖与前置条件

| 依赖组件 | 建议版本 | 说明 |
| :--- | :--- | :--- |
| **Docker** | 24.0+ | 用于承载 Kind 容器化 Kubernetes 节点 |
| **Kind** | v0.20.0+ | 用于快速构建本地多集群拓扑 |
| **Kubectl** | v1.28.0+ | Kubernetes 命令行客户端 |
| **Karmada** | v1.10.0+ / v1.11.0+ | 多集群联邦管理控制面 |
| **Go** | 1.22+ | 用于从源码构建修复版 ARC Controller（若已有编译好的二进制则可省略） |
| **GitHub 访问凭证** | PAT (Repo 权限) | 具有目标仓库 `repo` 作用域的 Personal Access Token 或 GitHub App 密钥 |

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
│   ├── 03-rbac.yaml                     # Runner Pod 运行所需 RBAC 权限
│   └── 04-autoscalingrunnerset.yaml     # 核心机制 ②：AutoscalingRunnerSet 及 Listener 凭据注入
└── scripts/
    ├── setup-karmada-token.sh           # 一键提取控制面 CA 并生成 Listener 认证 Secret
    └── run-arc-controller.sh            # 核心机制 ④：编译并启动修复版 ARC Controller (同 run-arc-manager.sh)
```

---

## 五、 逐步复现操作指南

### 步骤 1：部署 Karmada 控制面与成员集群

如已存在 Karmada 环境，请跳过此步骤并确保 `KUBECONFIG` 指向 Karmada APIServer。

#### 1.1 使用 Kind 创建三个集群
```bash
# 创建 Karmada 宿主集群
kind create cluster --name karmada-host

# 创建两个成员执行集群
kind create cluster --name member1
kind create cluster --name member2
```

#### 1.2 在 karmada-host 上初始化 Karmada
```bash
karmadactl init --kubeconfig=$HOME/.kube/config --context=kind-karmada-host
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
member1   v1.30.0   Push   True    1h
member2   v1.30.0   Push   True    1h
```

---

### 步骤 2：安装 ARC 核心 CRD

在 **Karmada 控制面** 安装 ARC 运行时所需的 4 个核心 CRD：

```bash
KUBECONFIG_KARMADA="$HOME/karmada-data/karmada-apiserver.config"

kubectl --kubeconfig="${KUBECONFIG_KARMADA}" apply -f crds/
```

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

此步骤解决 **机制 ②** 中的凭据生成问题。运行提供的脚本，自动提取 Karmada CA 证书并生成长效 Token：

```bash
export KUBECONFIG_KARMADA="$HOME/karmada-data/karmada-apiserver.config"
bash scripts/setup-karmada-token.sh
```

该脚本会在 `arc-systems` 命名空间下创建包含 `ca.crt` 和 `token` 的 Secret：`karmada-token`。

---

### 步骤 6：配置 Karmada 联邦分发策略

配置分发策略，打通 **机制 ③**（依赖穿透）与基础 Secret 同步：

```bash
kubectl --kubeconfig="${KUBECONFIG_KARMADA}" apply -f manifests/02-propagation-policies.yaml
```

验证策略就绪：
```bash
kubectl --kubeconfig="${KUBECONFIG_KARMADA}" get propagationpolicy -A
```

---

### 步骤 7：应用 Runner RBAC 权限

在 `arc-runners` 命名空间中配置 Runner Pod 与 Manager 的 RBAC 授权：

```bash
kubectl --kubeconfig="${KUBECONFIG_KARMADA}" apply -f manifests/03-rbac.yaml
```

---

### 步骤 8：声明 AutoscalingRunnerSet

在应用清单前，请确认 `manifests/04-autoscalingrunnerset.yaml` 中的配置项：
1. `githubConfigUrl`: 替换为您的测试仓库地址（如 `https://github.com/pkking/arc-karmada-test`）；
2. `hostAliases[0].ip`: 替换为您当前环境中 Karmada 控制面的访问 IP（如 Kind 网桥 IP `172.22.0.4` 或宿主机 IP）；
3. 检查无误后，应用清单：

```bash
kubectl --kubeconfig="${KUBECONFIG_KARMADA}" apply -f manifests/04-autoscalingrunnerset.yaml
```

---

### 步骤 9：运行修复版 ARC Controller

此步骤落实 **机制 ④**，运行解决了 Secret 循环调谐 Bug 的控制器：

```bash
# 导出 Karmada 配置路径与 GitHub Token
export KUBECONFIG_KARMADA="$HOME/karmada-data/karmada-apiserver.config"
export GITHUB_TOKEN="ghp_yourPersonalAccessTokenHere"

# 运行 ARC Controller
bash scripts/run-arc-controller.sh
```

控制器启动成功后，会自动检测 Karmada 上的 `AutoscalingRunnerSet` 并拉起对应的 Listener Pod：
```
==> 启动 ARC Controller Manager (针对 Karmada 控制面)...
{"level":"info","ts":"...","logger":"controller-runtime.builder","msg":"Registering a mutating webhook"...}
{"level":"info","ts":"...","logger":"AutoscalingRunnerSet","msg":"Reconciling AutoscalingRunnerSet"...}
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

在 GitHub 仓库的 **Settings -> Actions -> Runners** 页面中，即可观察到状态为 **Idle** 的 Runner 监听池已成功就绪！

---

## 六、 运行验证与效果分析

### 1. 触发并发 Workflow
在测试仓库中触发 [`.github/workflows/test-arc.yml`](.github/workflows/test-arc.yml)（包含 3 个并发 Job，指定 `runs-on: karmada-runner`）：

```bash
gh workflow run test-arc.yml --repo pkking/arc-karmada-test
```

### 2. 观察控制面与成员集群状态

在任务执行期间，分别查看成员集群 `member1` 与 `member2` 的 Pod 分布：

```bash
# 查看 member1 上的 Runner Pod
kubectl --kubeconfig=$HOME/karmada-data/member1-internal.config -n arc-runners get pods

# 查看 member2 上的 Runner Pod
kubectl --kubeconfig=$HOME/karmada-data/member2-internal.config -n arc-runners get pods
```

**实际运行分摊表现：**
```
=== member1 集群节点 ===
NAME                                    READY   STATUS    RESTARTS   AGE
karmada-runner-4kx8b-runner-7d9cf       1/1     Running   0          18s
karmada-runner-4kx8b-runner-l89sf       1/1     Running   0          17s

=== member2 集群节点 ===
NAME                                    READY   STATUS    RESTARTS   AGE
karmada-runner-4kx8b-runner-w24vc       1/1     Running   0          18s
```
- **验证结论 1**：Karmada 调度器根据 `Divided` 权重算法，成功将 3 个并发 Runner Pod 动态分散调度至 `member1`（2个）与 `member2`（1个），避免了单集群负载倾斜。
- **验证结论 2**：`propagateDeps: true` 策略成功将每个 Runner 对应的一次性 JIT Secret 自动穿透投递至对应的子集群，无任何鉴权或镜像拉取失败。
- **验证结论 3**：任务执行完成后，ARC 控制器与 Karmada 联邦引擎协同完成垃圾回收，控制面与子集群的 Pod、Work 及临时 JIT Secret 均被**即时完整销毁（Zero Leaks）**。

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
- **根因**：ARC 0.14.2 版本的 Secret Reconciler 缺陷。Karmada 会在 Secret 上打入联邦管理标签/注解，触发 0.14.2 控制器的死循环 Patch。
- **解决办法**：使用上游 Master 分支合并了 PR `#4492` 的代码，并带有 `-ldflags "-X .../build.Version=0.14.2"` 进行编译（见 [`scripts/run-arc-manager.sh`](scripts/run-arc-manager.sh)）。

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

通过上述 Prompt 驱动，Agent 自动化完成了架构可行性论证、双平面架构设计、KinD 多集群环境搭建、三大原生协同断裂问题的定位与修复（Lua 状态聚合、Listener 跨面 TLS 与 Token 注入、JIT Secret 依赖穿透），以及 ARC 上游 Master 补丁合入与编译。