# ARC + Karmada PoC 实现契约

> 基线：2026-10-15 已验证的 ARC 0.15.0 / Karmada v1.19.0 / Kubernetes v1.37.0。目标是行为等价的双成员 Kind PoC，**不是生产就绪保证**。对应 [Issue #1](https://github.com/pkking/arc-karmada-test/issues/1)。

## 1. 文档分工与规范

[架构设计](KARMADA-ARC-SUBSYSTEM-DESIGN.md)定义目的、职责与 Story；本文件定义**可实现的上游集成契约**；[验收契约](ACCEPTANCE-CONTRACT.md)规定测试断言和证据；[README](../README.md)保存操作命令。不得重新实现 ARC/Karmada 内部 Controller、调度器、OwnerReference 或 Finalizer 机制；代码生成可以不同，但必须满足本契约。配置现状不代表最佳实践；任何安全、传播及调度行为变更必须记录设计差异并证明回归。

## 2. 版本与可配置输入

| 参数 | PoC 基线 / 必需行为 |
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

镜像 tag 不等同于不可变 digest；实测 RepoDigest 和环境版本以[验证记录](arc-0.15.0-validation.md)为准，重建需记录实际镜像摘要。

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

当前空闲 Scale Set/Listener/基础 Secret 可常驻，不算泄漏；**本轮**临时 Pod/ER/JIT/关联 Binding/Work 已核对清理，**历史 ERS Finalizer 仍有残留**，不得强行清空未知 Finalizer 伪造结果。

## 4. 精确传播策略契约

依据 [`manifests/02-propagation-policies.yaml`](../manifests/02-propagation-policies.yaml)：

| Policy（Namespace） | 必须匹配的对象 | 目标与语义 |
|---|---|---|
| `arc-secret-propagation`（`arc-runners`） | **精确名字** Secret `arc-github-secret` + SA `karmada-runner-gha-rs-no-permission` | member1/member2；`conflictResolution: Overwrite` 仅接管这两类同名资源 |
| `arc-listener-propagation`（`arc-systems`） | Pod/SA 需同时匹配 `actions.github.com/scale-set-name: karmada-runner`、`actions.github.com/scale-set-namespace: arc-runners`；另匹配 Secret `karmada-token` | 仅 member1；`propagateDeps: true` 跟踪 Listener 动态配置 Secret |
| `arc-runner-pod-propagation`（`arc-runners`） | 当前选择**命名空间内全部 Pod**，**没有** labelSelector | member1/member2；`propagateDeps: true`；`Divided / Weighted`；各独立 Pod 分别放置，**不保证每轮均匀覆盖成员** |

不能把 ARC CRD/CR、Karmada 内部 Controller 或宿主 Docker Controller 当作 Runner Pod 传播；但在 `arc-runners` 新建的其他 Pod 可能因现有广域 selector 被传播。主 GitHub Secret 是否确需到成员是专项待核查点；先确定上游真实消费者，再提安全收敛变更与回归证据。

## 5. 接口、安全与跨控制面身份

| 主体 | 目标 | 控制面身份与限制 |
|---|---|---|
| 宿主 ARC Controller | Karmada API | 使用只读挂载的 Karmada kubeconfig；PoC 实际为管理员级权限，**尚未最小化** |
| member1 ARC Listener | Karmada API | `hostAliases`、`KUBERNETES_SERVICE_HOST/PORT`、Karmada CA 与 Token；不是成员本地 ServiceAccount Token |
| Runner Pod | GitHub Actions | 临时 JIT 注册；Runner SA 禁用 API Token 自动挂载 |
| Karmada | 成员 Kubernetes API | 由 Karmada 上游成员加入凭证提供授权 |

Listener 精确 RBAC 见 [`03-rbac.yaml`](../manifests/03-rbac.yaml)：`arc-runners` Role 对 `ephemeralrunnersets` 仅 `resourceNames: [karmada-runner]` 可 `patch`；对 `ephemeralrunners` 可 `get/patch`；对 `ephemeralrunners/status` 可 `patch`。RoleBinding 指向 `arc-systems/karmada-listener-sa`；**不得换成 namespace-admin**。

`setup-karmada-token.sh` 默认申请 `--duration=24h` 并保存 CA+Token 到 `arc-systems/karmada-token`。实际有效期由 API Server 控制；没有自动检测、轮换或无感知重载保证。手工轮换后需验证 Listener 实际加载新凭证并恢复 API 操作。x509/SAN/CA、401（认证）、403（授权）应分开定位。

## 6. 部署与 Ready 契约

命令以 [README 第五部分](../README.md#五-逐步复现操作指南) 为准，不把这些命令复制为架构设计。

1. 准备 Docker、Kind、kubectl、karmadactl、gh、仓库权限、镜像和不入库的凭证；固定版本。
2. 创建 `karmada-host`、member1、member2；启动 Karmada v1.19.0 并加入两个 Ready/Push 成员。**干净环境全流程未重新 E2E 验证**。
3. 安装官方 ARC 0.15.0 CRD；创建 Namespace、`arc-github-secret`；应用 Lua 状态解释器与精确 Listener RBAC。
4. 签发 `karmada-token`，核对 Token 期限、Listener 可达 endpoint、TLS SAN/CA；应用三个传播 Policy 并检查 selector、冲突对象及 `propagateDeps`。
5. 根据当前环境替换 `githubConfigUrl`、控制面 IP/Port、镜像和集群参数；创建 RunnerSet；宿主侧运行官方 `scripts/run-arc-controller.sh`。
6. **Ready**：两个成员 Ready；Listener **恰好一个**、0.15.0、Ready 且能建立 GitHub Session/访问 Karmada API。`minRunners=0` 时不要求空闲 Runner。
7. **E2E**：真实三 Job 成功和状态/凭证/清理检查按[验收契约](ACCEPTANCE-CONTRACT.md)执行。就绪不等于端到端已经验证。

声明式 apply 不等于整个安装、Token 替换、Controller 重启和动态资源恢复均幂等。失败时保存脱敏事件、对象 UID/RV 和日志，修复出错阶段再执行；不要无审阅强制删除 CRD/CR/Finalizer，也不要在 GitHub dispatch 超时且状态未知时盲目重派发。

## 7. 异步、故障和恢复契约

| 场景 | 观测 | 恢复责任 / 已验证边界 |
|---|---|---|
| JIT Secret 晚到 | `FailedMount` / Secret 暂缺，之后可 Ready | Karmada 异步同步、kubelet 重试；已观察到短暂失败后恢复，不是原子事务 |
| 成员不可达 | Cluster Ready、Work/Binding 异常、API 失败 | Karmada 上游调谐；**运行中 Job 迁移、重新执行和自动容灾未经验证** |
| Pod Pending / ErrImagePull | Pod Event、调度失败、镜像拉取错误 | Kubernetes 原生重试，运维排查；不得私自重复创建会导致重复 Job 的 Runner |
| Runner 注册失败 | GitHub queued、Runner 日志、Listener 错误 | ARC 管理生命周期；不得复用过期 JIT 凭证；故障演练待做 |
| 状态滞后/缺失 | 控制面无 `containerStatuses[].name` 或 Pod Ready | Lua 保留完整 status；**当前取 `statusItems` 第一个非空 status，只适用于单目标 Runner Pod**，不承诺多副本/迁移语义 |
| Listener 身份失效 | x509、401、403、无法更新 ERS | 核对网络/SAN/CA/Token/Role，由运维手工恢复；无自动轮换保证 |
| 本轮对象删除失败 | Pod/ER/JIT/Binding/Work UID 残留，Finalizer | ARC/Karmada 原生调谐；测试仅记录证据，未知历史 Finalizer 须独立分析 |

`verify-arc.py --duration 180 --interval 3` 是**测试采样参数，不是系统恢复 SLA**；故障重试次数/生产 deadline、告警阈值和 HA 尚待设计。

## 8. 待决策问题和实现质量门槛

- **D-01 主凭证传播**：当前 `arc-github-secret` 同步两成员；先证明消费者依赖、再设计最小分发和安全回归。
- **D-02 Runner Pod 选择器**：当前匹配全部 `arc-runners` Pod；收窄前证明上游动态 Pod 的稳定标签和负向匹配行为。
- **D-03 状态聚合**：首个非空 status 仅用于当前单目标；多副本/迁移前须重新设计。
- **D-04 生产化**：Controller 最小权限、Token 自动轮换、HA、故障切换、GitOps 与升级回滚尚未交付。
- **D-05 调度收益**：两成员具备执行能力 ≠ 统计均衡或排队改善；需要独立负载与窗口实验。
- **D-06 干净环境重建**：README 有路径，但须真实重建并保留脱敏证据，不得冒充通过。
- **D-07 US-08 NPU**：资源名称/粒度、Device Plugin、节点标签及真实 NPU E2E 未验证。

所有变更必须标记 Story、受影响契约/Manifest、验收断言、脱敏证据和未验证事项；不得提交 PAT、Token、Secret Data、Kubeconfig 或私钥。
