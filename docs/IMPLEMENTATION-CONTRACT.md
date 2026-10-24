# ARC + Karmada 集成接口与资源设计契约

> 用途：从 ARC、Karmada、Kubernetes 的公开 API 资料推导本项目集成方案的资源、接口、身份与异常设计。**不以 PoC、真实 Job 或集群重建为本文档的完成前提**。本仓库的 ARC 0.15.0/Karmada 1.19.0/Kind 配置是实现示例，不是上游官方组合保证。对应 [Issue #1](https://github.com/pkking/arc-karmada-test/issues/1)。

## 1. 文档分工与规范

[架构设计](KARMADA-ARC-SUBSYSTEM-DESIGN.md)定义系统目标、职责与 Story；[官方组件依据矩阵](UPSTREAM-SOURCE-MATRIX.md)是本设计的**证据来源**；本文件定义需要本系统承担的**接口、资源、身份和状态约束**；[设计评审准则](ACCEPTANCE-CONTRACT.md)检查其完整性。[README](../README.md) 与[工程测试计划](ENGINEERING-TEST-PLAN.md)是独立实施材料。不得重复设计 ARC/Karmada 的内部控制器、调度器或 Finalizer 机制。凡跨控制面的组合行为，必须标为本系统集成决策，而非上游已经担保的兼容性。

## 2. 版本与可配置输入

| 参数 | 参考实现示例 / 设计约束（部署时参数化） |
|---|---|
| ARC | `ghcr.io/actions/gha-runner-scale-set-controller:0.15.0` 官方镜像；四份官方 0.15.0 CRD；RunnerSet `app.kubernetes.io/version: "0.15.0"` |
| Runner | `ghcr.io/actions/actions-runner:2.337.0`；`minRunners: 0`、`maxRunners: 5` |
| Karmada | `v1.19.0`，Push 模式，member1/member2 Ready |
| Kubernetes | Kind 三集群 `karmada-host`、`member1`、`member2`，`v1.37.0`；节点 digest 见[README](../README.md) |
| GitHub | `githubConfigUrl: https://github.com/pkking/arc-karmada-test`（需按目标仓库替换）；Scale Set `karmada-runner` / `runs-on: karmada-runner` |
| Karmada kubeconfig | `$HOME/karmada-data/karmada-apiserver.config` 为当前主机路径，须实际指向可达的控制面 |
| Listener API | DNS `karmada-apiserver`，PoC IP `172.22.0.4`，端口 `32443`；**IP/端口为环境参数，绝非通用常量**，必须核实可达性、SAN 和 CA |
| 凭证 | 控制面 `arc-runners/arc-github-secret`；`arc-systems/karmada-token`（CA+Token），必须环境外部生成、不入库 |
| Token | `setup-karmada-token.sh` 默认请求 24h；实际有效期以 API Server 为准；**无自动轮换** |

镜像 tag 不等同于不可变 digest；具体部署选择版本时应遵守官方组件兼容性与供应链规范。[历史版本与运行记录](arc-0.15.0-validation.md)可作为实施参考，**不属于设计证明**。ARC [官方 Scale Set 文档](https://docs.github.com/en/actions/concepts/runners/actions-runner-controller)与 [Karmada Policy API](https://karmada.io/docs/next/userguide/scheduling/propagation-policy/)是能力依据。

## 3. 核心资源、Owner 与生命周期

`arc-systems` 管理 Listener；`arc-runners` 管理 Runner Scale Set 与临时 Runner。Namespace 来自 `manifests/00-namespaces.yaml`。

| 资源 | 权威生产者 | 控制面/成员分工及删除语义 |
|---|---|---|
| `AutoscalingRunnerSet/karmada-runner` | `04-autoscalingrunnerset.yaml` + ARC | 常驻控制面 `arc-runners`，`runnerScaleSetName`、`githubConfigSecret`、版本标签必须一致 |
| AutoscalingListener / Listener Pod | ARC 动态管理 | Listener Pod 在 `arc-systems`，定向传播到 member1；不得硬编码随机 hash 或强改父控制器生成的 CR |
| EphemeralRunnerSet、EphemeralRunner | ARC 动态管理 | 控制面 `arc-runners`，随 Runner 需求创建/回收，**不是**直接传播成员的工作负载 |
| Runner Pod | ARC 生成 | 控制面 `arc-runners` → member1 **或** member2；`serviceAccountName: karmada-runner-gha-rs-no-permission`、`automountServiceAccountToken: false` |
| JIT Secret | ARC 生成 | 与引用它的 Runner Pod 同成员；异步传播可短暂 FailedMount；Job 后检查清理 |
| `arc-github-secret` | 平台/环境提供 | 当前基础 Policy 显式同步 member1/member2，**必要性待核实**；不能擅自删除 |
| Runner SA | `03-rbac.yaml` | `arc-runners/karmada-runner-gha-rs-no-permission` → 两成员；无 Kubernetes API Role |
| Listener SA | `03-rbac.yaml` | 固定控制面身份 `arc-systems/karmada-listener-sa`，用于签发 Listener Karmada Token |
| `karmada-token` | `setup-karmada-token.sh` | `arc-systems` 控制面创建、定向下发 member1；Token 生命周期由运维负责 |
| 动态 Listener config Secret/SA | ARC | 按 scale-set 标签或 Pod 引用跟随 Listener 下发，不能依赖生成名称 |
| ResourceBinding / Work | Karmada 上游 | 依据选定成员自动产生/清理；按实际 UID/对象关联收集证据，不硬编码生成名称 |
| `ResourceInterpreterCustomization/pod-status-interpreter` | `01-karmada-interpreter.yaml` | 控制面扩展 `v1/Pod`，提供 ARC 需要的完整 Pod.status |

Scale Set/Listener/基础 Secret 属于常驻控制面资源；临时 Runner/Pod/JIT Secret 与关联 Binding/Work 应按各自上游生命周期回收。Finalizer 要保留资源所有权和安全恢复语义，不能以强制删除替代设计。[ARC 生命周期说明](https://docs.github.com/en/actions/concepts/runners/actions-runner-controller)是上游依据，跨 Karmada 的异步清理由本项目定义。

## 4. 精确传播策略契约

依据 [`manifests/02-propagation-policies.yaml`](../manifests/02-propagation-policies.yaml)：

| Policy（Namespace） | 必须匹配的对象 | 目标与语义 |
|---|---|---|
| `arc-secret-propagation`（`arc-runners`） | **精确名字** Secret `arc-github-secret` + SA `karmada-runner-gha-rs-no-permission` | member1/member2；`conflictResolution: Overwrite` 仅接管这两类同名资源 |
| `arc-listener-propagation`（`arc-systems`） | Pod/SA 需同时匹配 `actions.github.com/scale-set-name: karmada-runner`、`actions.github.com/scale-set-namespace: arc-runners`；另匹配 Secret `karmada-token` | 仅 member1；`propagateDeps: true` 跟踪 Listener 动态配置 Secret |
| `arc-runner-pod-propagation`（`arc-runners`） | 当前选择**命名空间内全部 Pod**，**没有** labelSelector | member1/member2；`propagateDeps: true`；`Divided / Weighted`；各独立 Pod 分别放置，**不保证每轮均匀覆盖成员** |

不能把 ARC CRD/CR、Karmada 内部控制器或宿主 Controller 当作 Runner Pod 传播；当前示例的广域 selector 可能意外传播其他 Pod。**目标设计原则**：匹配 ARC 受管 Runner 的稳定标签，主 GitHub 凭证优先保留在 ARC 管理域，只下发执行所需的 JIT/运行依赖；具体 label 稳定性和引用关系须依据选定 ARC 版本资料审查。实际 Manifest 的收敛与运行兼容性属于实施任务，不阻塞设计文档闭环。参考 [Karmada 官方资源选择器](https://karmada.io/docs/next/userguide/scheduling/propagation-policy/)和 [Kubernetes RBAC 最小权限](https://kubernetes.io/docs/concepts/security/rbac-good-practices/)。

## 5. 接口、安全与跨控制面身份

| 主体 | 目标 | 控制面身份与限制 |
|---|---|---|
| ARC Controller 管理身份 | Karmada API | 参考示例为管理 kubeconfig；**目标设计应提供按最小权限授权的独立身份**，不可视管理员凭证为推荐配置 |
| member1 ARC Listener | Karmada API | `hostAliases`、`KUBERNETES_SERVICE_HOST/PORT`、Karmada CA 与 Token；不是成员本地 ServiceAccount Token |
| Runner Pod | GitHub Actions | 临时 JIT 注册；Runner SA 禁用 API Token 自动挂载 |
| Karmada | 成员 Kubernetes API | 由 Karmada 上游成员加入凭证提供授权 |

Listener 精确 RBAC 见 [`03-rbac.yaml`](../manifests/03-rbac.yaml)：`arc-runners` Role 对 `ephemeralrunnersets` 仅 `resourceNames: [karmada-runner]` 可 `patch`；对 `ephemeralrunners` 可 `get/patch`；对 `ephemeralrunners/status` 可 `patch`。RoleBinding 指向 `arc-systems/karmada-listener-sa`；**不得换成 namespace-admin**。

`setup-karmada-token.sh` 默认申请 `--duration=24h` 并保存 CA+Token 到 `arc-systems/karmada-token`。实际有效期由 API Server 控制；没有自动检测、轮换或无感知重载保证。手工轮换后需验证 Listener 实际加载新凭证并恢复 API 操作。x509/SAN/CA、401（认证）、403（授权）应分开定位。

## 6. 部署依赖与配置顺序（实现指导，非设计验收）

本节只是将接口依赖转换为建议的初始化顺序；实际命令见[README](../README.md)。完成本设计只需明确配置输入和依赖顺序，**无需运行任何命令或提供 Ready、Job、Kind、PoC 证据**。

1. 准备 Docker、Kind、kubectl、karmadactl、gh、仓库权限、镜像和不入库的凭证；固定版本。
2. 为部署规划 Karmada 控制面及候选成员网络、证书和访问路径；`karmada-host`、member1/member2 是示例拓扑，集群数量与版本由部署基线确定。
3. 安装官方 ARC 0.15.0 CRD；创建 Namespace、`arc-github-secret`；应用 Lua 状态解释器与精确 Listener RBAC。
4. 签发 `karmada-token`，核对 Token 期限、Listener 可达 endpoint、TLS SAN/CA；应用三个传播 Policy 并检查 selector、冲突对象及 `propagateDeps`。
5. 根据当前环境替换 `githubConfigUrl`、控制面 IP/Port、镜像和集群参数；创建 RunnerSet；宿主侧运行官方 `scripts/run-arc-controller.sh`。
6. **Ready**：两个成员 Ready；Listener **恰好一个**、0.15.0、Ready 且能建立 GitHub Session/访问 Karmada API。`minRunners=0` 时不要求空闲 Runner。
7. **工程实施阶段**可按[独立测试计划](ENGINEERING-TEST-PLAN.md)进行真实 Job、分成员执行、故障与清理测试；这些测试**不能成为本设计文档完成的必要条件**。

声明式 apply 不等于整个安装、Token 替换、Controller 重启和动态资源恢复均幂等。失败时保存脱敏事件、对象 UID/RV 和日志，修复出错阶段再执行；不要无审阅强制删除 CRD/CR/Finalizer，也不要在 GitHub dispatch 超时且状态未知时盲目重派发。

## 7. 异步、故障和恢复契约

| 场景 | 可观测状态 | 设计规定的恢复责任 / 约束 |
|---|---|---|
| JIT Secret 晚到 | `FailedMount` / Secret 暂缺，之后可 Ready | Karmada 按上游依赖传播规则调谐、成员 kubelet 按 Pod 依赖处理；**方案不要求 Secret/Pod 跨资源原子到达** |
| 成员不可达 | Cluster Ready、Work/Binding 异常、API 失败 | Karmada 上游调谐；上游文档不承诺运行中 Job 无损迁移；不得将其作为设计默认保证 |
| Pod Pending / ErrImagePull | Pod Event、调度失败、镜像拉取错误 | Kubernetes 原生重试，运维排查；不得私自重复创建会导致重复 Job 的 Runner |
| Runner 注册失败 | GitHub queued、Runner 日志、Listener 错误 | 由 ARC 生命周期与 GitHub Runner API 管理；不得复用过期 JIT 凭证，重试/去重需明确责任 |
| 状态滞后/缺失 | 控制面无 `containerStatuses[].name` 或 Pod Ready | Lua 保留完整 status；**当前取 `statusItems` 第一个非空 status，只适用于单目标 Runner Pod**，不承诺多副本/迁移语义 |
| Listener 身份失效 | x509、401、403、无法更新 ERS | 核对网络/SAN/CA/Token/Role，由运维手工恢复；无自动轮换保证 |
| 本轮对象删除失败 | Pod/ER/JIT/Binding/Work UID 残留，Finalizer | ARC/Karmada 原生调谐；测试仅记录证据，未知历史 Finalizer 须独立分析 |

重试次数、故障恢复目标、告警窗口和生产 HA 是可配置的**业务/部署设计参数**，不能将仓库测试脚本的采样时间当作 SLA。设计已明确责任边界，不需要先运行测试才能形成契约。

## 8. 待决策问题和实现质量门槛

- **D-01 主凭证传播**：当前 `arc-github-secret` 同步两成员；先证明消费者依赖、再设计最小分发和安全回归。
- **D-02 Runner Pod 选择器**：当前匹配全部 `arc-runners` Pod；收窄前证明上游动态 Pod 的稳定标签和负向匹配行为。
- **D-03 状态聚合**：首个非空 status 仅用于当前单目标；多副本/迁移前须重新设计。
- **D-04 生产化**：Controller 最小权限、Token 自动轮换、HA、故障切换、GitOps 与升级回滚尚未交付。
- **D-05 调度收益**：两成员具备执行能力 ≠ 统计均衡或排队改善；需要独立负载与窗口实验。
- **D-06 实施环境选择**：控制面、成员 API 网络、证书、镜像 digest 和存储持久化由部署方案确认；干净环境重建属后续工程验证，不阻塞设计。
- **D-07 US-08 NPU**：按 Kubernetes Device Plugin 的扩展资源约定设计接口；具体资源名、分配粒度、驱动和硬件型号作为选型输入，真实 NPU E2E 为实施工作。

设计变更必须标记 Story、上游文档依据、集成决策及接口/DFX 影响；**不能要求 PoC 或运行证据才能关闭设计评审**。实施阶段另行安排测试、脱敏记录及安全审计；不得提交 PAT、Token、Secret Data、Kubeconfig 或私钥。
