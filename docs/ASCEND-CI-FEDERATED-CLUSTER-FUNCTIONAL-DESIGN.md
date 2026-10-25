# 昇腾CI 联邦集群功能设计文档

> 文档标识：ASCEND-CI-FEDERATED-CLUSTER-FUNCTIONAL-DESIGN
> 状态：统一设计正文（基于现有资料整理；新增决定与待选参数显式标记）
> 目录依据：用户提供的功能域设计模板：第 1 章保留已有内容；第 2 章概述；第 3 章总体方案；第 4 章领域规格；第 5 章功能实现
> **设计证明依据：** 官方 ARC/GitHub Actions、Karmada、Kubernetes 技术文档及清晰的项目设计决策。**文档完成不要求 PoC、Kind、真实 Runner 或硬件实验**。

**阅读原则**：**U** = 上游文档明确支持的机制；**D** = 昇腾CI针对组件组合定义的集成设计决策；**P** = 实施/厂商选型待定的参数。当前仓库 Manifest 是参考实现，不等于上游产品承诺；“设计完成”也不等于“已部署运行”。

# 1 功能域背景与目的

## 1.1 目标与职责

将原先项目独占的多个执行集群纳入 Karmada，统一管理 GitHub Actions Runner 的创建、分发与回收。

| 职责 | 实现主体 | 边界 |
|---|---|---|
| 任务匹配与 Runner 生命周期 | ARC Listener、Controller、Scale Set | GitHub Actions 提供队列；ARC 调整 Runner 数量 |
| 资源对象传播与成员选择 | Karmada Policy、Scheduler、Binding / Execution Controller | 选择成员集群；不承担每个 Pod 的节点级调度 |
| 资源实际执行 | 成员 Kubernetes、kube-scheduler、kubelet | 创建 Runner Pod；可在 NPU 节点执行有设备请求的工作负载 |
| 状态与凭证适配 | ResourceInterpreterCustomization、Listener Token / RBAC、propagateDeps | 保留 ARC 所需的 Pod 状态和 JIT Secret |
| 设计契约与治理 | 本子系统接口适配、策略、身份与状态约束 | 由本项目负责架构决策；不代替上游内部实现 |

## 1.2 系统边界

**边界内：** ARC Controller、Listener、Runner Scale Set；Karmada 控制面；纳管的 Kubernetes 成员集群、Runner Pod 和目标 NPU 节点资源。

**边界外：** GitHub Actions 服务、业务项目、镜像仓库及现有企业级监控系统。

系统边界表示统一联邦执行的运行范围，组件可依据部署条件运行在不同 Kubernetes / 宿主环境。图示选择了“Controller 在独立管理环境、Listener 定向成员、Runner 由联邦分发”的**参考部署拓扑**，并非上游唯一合法部署形态。跨控制面访问需要独立的凭据与网络设计。

## 1.3 子系统目标

![资源无法互用、资源利用不均衡与任务排队过长：联邦执行前后对比](images/federation-subsystem-goal-before-after.svg)

**现状：** 集群按项目固定分配。不同集群不能共享空闲资源。部分集群资源空闲，部分集群任务排队。热点项目等待时间较长。

**目标：** ARC 统一管理 Runner 需求。Karmada 将 Runner 分发至满足条件的成员集群。系统支持项目间共享合格成员的执行容量，以减少资源错配和可避免的排队等待。

**设计边界：** 该图为问题/目标示意，利用率及排队收益没有定量证明；本设计不承诺指定改善百分比。联邦策略负责成员选择，成员实际容量、资源兼容性和 NPU 驱动属于部署输入。

## 1.4 整体架构

![ARC + Karmada 联邦调度执行子系统整体架构](images/overall-subsystem-architecture.svg)

本子系统包含 ARC、Karmada、成员 Kubernetes 集群及 Runner 执行环境。图中灰色外框表示子系统边界。边界外的系统通过以下接口交互。

| 外部子系统 | 方向 | 交互内容 |
|---|---|---|
| GitHub Actions | 输入 | 工作流任务、`runs-on` 标签、Runner 需求 |
| GitHub Actions | 输出 | Runner 注册、Job 执行状态和结果 |
| 配置与部署系统 | 输入 | Scale Set、PropagationPolicy、RBAC、组件版本 |
| 镜像仓库 | 输入 | Runner 镜像、Action 镜像 |
| 监控与审计系统 | 输出 | 运行日志、Kubernetes 事件、验证记录 |

**设计范围：** 图中以一个集中 ARC、一个 Karmada 控制面和两个成员作为可扩展的逻辑示例，不限定 Kind 或具体地区。NPU 设备插件是目标环境依赖，按选型纳入成员集群；不依赖 `karmada-search`。

## 1.5 关键约束

- Runner 工作流使用 `runs-on: karmada-runner`。标签对应一个已注册 Scale Set。
- Scale Set 版本标签与选定的官方 ARC Controller 版本保持一致；仓库当前 0.15.0 为配置示例。
- Listener 运行在成员集群，通过独立 Token、CA 和 DNS 配置访问 Karmada API。
- Runner Pod 的 JIT Secret 必须传播至同一执行成员；传播过程允许短暂异步等待。
- Karmada Pod 状态解释器保留 `containerStatuses[].name` 和 `conditions`。
- `Divided / Weighted` 针对单个资源对象执行。多个独立 Pod 不保证逐轮均匀分布。
- 成员 Kubernetes 负责节点调度。NPU 任务需额外核实节点标签、设备插件、资源请求和工作负载运行环境。


# 2 功能域概述

## 2.1 定位、使用者和边界

本功能域向 GitHub Actions 工作流提供统一的计算资源入口，将原先固定按项目使用的集群汇聚成受控联邦候选集。用户仅选择 Runner 服务标签，平台将 Job 需求转换为短生命周期 Runner，通过 Karmada 选择成员并在成员 Kubernetes 内执行。它不是替代 GitHub Job Queue、重写 ARC 生命周期控制器或重建 Karmada 调度器。

**业务参与者**：CI 开发者（提交 Job）、CI 平台工程师（定义 Scale Set 与策略）、资源池管理员（纳管成员与 NPU）、安全管理员（权限/凭证）、运维人员（升级/故障/审计）。外部系统包括 GitHub Actions、OCI 镜像仓库、已有身份/密钥系统、GitOps/部署面和观测系统。

## 2.2 功能清单与用例

| 功能 | 关键问题 | 对应 Story | 负责设计内容 |
|---|---|---|---|
| F-01 统一 Runner 服务入口 | 项目与执行集群解耦 | US-01 | runs-on / Scale Set 关联与无匹配行为 |
| F-02 Runner 弹性供给与回收 | 统一供给、避免各集群管理孤岛 | US-02、US-07 | ARC 生命周期与 Karmada 对象清理的协作 |
| F-03 联邦调度与依赖分发 | 在合格成员执行并携带 JIT | US-03、US-04 | Policy、placement、propagateDeps |
| F-04 Listener 跨控制面访问 | 在成员运行的 Listener 安全访问统一 API | US-05 | Endpoint/TLS/Token/RBAC |
| F-05 Runner 状态反馈 | 避免错误的未就绪判断 | US-06 | Pod.status / 状态解释与聚合 |
| F-06 NPU 执行资源适配 | 在兼容设备节点执行 | US-08 | Device Plugin/扩展资源/节点筛选 |

## 2.3 需求分析与追踪（IR → UC → SR → 功能）

> IR/UC/SR 为根据现有 US-01～US-08 **整理出的建议需求编号**，不是已批准的外部需求编号。后续有正式需求系统 ID 时应映射或替换，不能把这些编号视为原始来源。

| IR | 客户需求意图 | 用例 UC | 导出的 SR | 功能 |
|---|---|---|---|---|
| IR-01 | 统一 CI Runner 服务入口 | UC-01 标签提交、UC-02 无匹配 Runner | SR-01-01、SR-01-02 | F-01 |
| IR-02 | 集中供给并回收 Runner | UC-03 按需创建、UC-04 作业完成回收 | SR-02-01、SR-02-02 | F-02 |
| IR-03 | 跨成员使用异构计算资源 | UC-05 候选成员调度、UC-06 JIT 依赖晚到、UC-07 NPU 放置 | SR-03-01、SR-03-02、SR-06-01 | F-03 / F-06 |
| IR-04 | 跨控制面身份和状态正确性 | UC-08 Listener 鉴权、UC-09 Pod 状态回传 | SR-04-01、SR-05-01 | F-04 / F-05 |

**用例视图（概要）：**

![昇腾CI 联邦集群功能域概要用例视图——业务角色与主要用例关系](images/federated-cluster-use-case-overview.svg)

> 图中连线仅表示参与者与主要业务用例的关联，不代表任务执行时序。Runner 弹性供给、JIT Secret 传播、Pod 状态聚合等属于支撑业务用例的内部实现功能，详见第 5 章。

## 2.4 系统功能需求 SR

| SR | 开发应实现的可观察行为 | 实现单元 | 用例 | 来源类型 |
|---|---|---|---|---|
| SR-01-01 | 统一 Runner 标签到 Scale Set 映射，业务工作流无需指定成员 | F-01 | UC-01 | U:ARC-1,ARC-2 |
| SR-01-02 | 无匹配 Scale Set 或认证异常时保持可观察错误或排队状态，不静默错误路由 | F-01 | UC-02 | D |
| SR-02-01 | 按任务需求通过 ARC 官方 CR 管理临时 Runner 实例和 Job 注册 | F-02 | UC-03 | U:ARC-1 |
| SR-02-02 | 执行结束后依据上游 Owner/Finalizer 回收临时 Runner、JIT 及联邦副本 | F-02 | UC-04 | D:ARC+Karmada |
| SR-03-01 | 将 Runner Pod 分发至符合 ClusterAffinity/资源约束的一个成员，不保证独立 Pod 逐轮均衡 | F-03 | UC-05 | U:KAR-1 |
| SR-03-02 | 将 Runner 引用的 JIT Secret 与 Pod 下发同一成员，并容忍异步晚到 | F-03 | UC-06 | U:KAR-2；D:依赖链 |
| SR-04-01 | Listener 使用控制面 endpoint、CA、受限身份操作 ERS；区分认证/授权/网络异常 | F-04 | UC-08 | U:K8S-1,K8S-2；D |
| SR-05-01 | 对单目标 Runner Pod 聚合完整 status，保留 containerStatuses 名称和 Ready Conditions | F-05 | UC-09 | U:KAR-3；D |
| SR-06-01 | NPU Runner 通过 Kubernetes Device Plugin 的扩展资源与节点约束分配到兼容成员 | F-06 | UC-07 | U:K8S-3；P:型号/资源名 |

**功能变更判断**：U 类优先复用官方机制；D 类是本项目的配置、身份、资源、状态适配；P 类需在实施配置/设备选型时落实。接口和异常规格集中在第 4 章定义；第 5 章对每个功能只描述该功能必须编写/声明的对象、调用与运行路径。

## 2.5 非目标、约束及使用体验

- 不提供自研 Job Queue；GitHub 仍负责 Job 匹配与结果。
- 不把 Weighted/Divided 自动解释成跨多个独立 Pod 的统计公平或逐轮均衡保证。
- 当前不自行开发 ARC、Karmada 和 Kubernetes 内部控制器；本系统聚焦对象配置与必要适配。
- 业务方通过 GitHub 原生 `runs-on` 选择服务，不要求在业务工作流中写成员集群 IP/名称。
- NPU 执行依赖选定厂商 Device Plugin/Runtime/资源名；功能设计可以完整，设备型号属于 P 类输入。


# 3 功能域总体方案

## 3.1 总体原则、设计模式与边界

1. **职责分离**：GitHub 负责队列，ARC 负责 Runner 期望量与短生命周期，Karmada 负责成员选择和对象传播，成员 Kubernetes 负责节点调度与设备资源分配。
2. **控制面与执行面分离**：Runner 管理请求汇入统一 API；Listener 和 Runner 可以部署于执行域，但其权限、连接地址和证书与目标 API 一致。
3. **最小权限与最小传播**：GitHub 主凭证以管理域为中心，JIT 凭证按具体引用传播至执行成员；Runner 默认不获取 Kubernetes 管控 API Token。
4. **最终一致与无重放假设**：跨组件 Pod/Secret/Binding 传播与清理都是异步流程；没有上游证明不得承诺原子传播、无损迁移或 Job exactly-once 重试。
5. **上游优先**：默认复用 ARC、Karmada、Kubernetes 的正式 API/控制器；本功能域仅增加集成策略、必要权限、状态解释及配置，不重复设计上游模块。

## 3.2 备选方案与最终选型

| 选型 | 结构 | 优点 | 主要代价与适用边界 |
|---|---|---|---|
| A：每成员独立 ARC | 每个集群有独立 Scale Set/Controller | 实施与故障域较独立 | 统一入口、跨资源池共享需要额外协调层；与“集中 Runner 生命周期”目标不完全一致 |
| B：ARC + Liqo | ARC 结合跨集群资源/网络抽象 | 某些网络和工作负载场景可复用 Liqo 能力 | 跨集群网络、资源抽象和运维依赖需独立设计；当前仓库不是该方案实现 |
| **C：ARC + Karmada（选择）** | ARC 管理期望量，Karmada 分发 Runner Pod | 复用 Karmada Policy、成员集群与执行面调度边界 | ARC 的控制面访问、状态回传、JIT 依赖、资源回收需要本项目显式约束 |

**方案选择依据（D）**：功能域主要是 Kubernetes 对象级跨集群分发，且不要求 Pod 间跨集群直连网络，因此以现有 Karmada 联邦能力承担成员选择和依赖传播。上述评价属于**本功能域的架构取舍**，不代表上游对 ARC+Karmada 组合做了兼容性认证。

## 3.3 端到端实现思路

```mermaid
flowchart LR
  GH[GitHub Job] --> L[ARC Listener]
  L --> ARC[ARC Controller / ERS]
  ARC --> API[Karmada API]
  API --> POLICY[成员选择与依赖传播]
  POLICY --> M1[成员 Kubernetes 1]
  POLICY --> M2[成员 Kubernetes 2]
  M1 --> GH
  M2 --> GH
  M1 -.Pod status.-> API
  M2 -.Pod status.-> API
```

需求方向为 GitHub → Listener → ARC → Karmada → 成员 Runner。反馈方向为 Runner → GitHub 和成员 Pod.status → Karmada → ARC。状态/依赖传播是两个上游控制器体系之间的**集成契约**，参见第 4 章。

## 3.4 功能域系统元素及逻辑架构

### 3.4.1 逻辑组件架构

![联邦调度执行系统逻辑架构](images/federation-layered-architecture.svg)

本子系统要解决的核心问题是：**项目与执行集群绑定，导致集群间资源难以互用、负载不均衡及任务排队**。架构采用 **ARC 集中管理 Runner + Karmada 联邦分发 + 成员 Kubernetes 原生执行** 的协作模式；以下重点说明各构件对整体问题的贡献，而非展开上游组件的内部实现。

| 架构构件 | 主要解决的问题 / 架构职责 | 与其他构件的协作关系 |
|---|---|---|
| ARC Listener | **统一任务入口**：让开发者使用同一个 Runner 标签提交任务，不必为不同成员集群选择专用标签。 | 对接 GitHub Actions 的任务需求，与 ARC Controller 协同完成按需 Runner 供给。 |
| ARC Controller | **集中管理执行实例的生命周期**：使 Runner 的创建、扩缩与回收不再由每个执行集群各自管理。 | 承接 Listener 的需求变化，将 Runner 交给 Karmada 跨集群分发，并根据执行状态管理生命周期。 |
| Karmada（复用的联邦子系统） | **打通相互隔离的执行资源池**：将多个成员集群纳入同一分发范围，使一个项目的任务可以使用其他合格成员的执行资源。 | 接收 ARC 产生的 Runner 对象，将其分发至成员 Kubernetes，并把执行状态返回给 ARC；不取代成员的节点级调度。 |
| 成员 Kubernetes 与 Runner Pod | **提供实际、相互隔离的任务执行能力**：让集中管理的 Runner 在选中的成员集群使用当地计算资源执行 GitHub Job。 | 接收 Karmada 下发的 Runner 工作负载，使用成员原生调度与网络执行任务，并向控制面反馈状态。 |
| ARC–Karmada 集成适配（本系统设计） | **消除两套上游系统之间的语义与环境差异**：保障跨控制面访问、临时注册凭证可用、Runner 就绪状态可被正确识别。 | 在 ARC、Karmada 与成员 Kubernetes 之间补齐通信鉴权、依赖传播和状态聚合约束，使上述管理、分发、执行链路闭环。 |

**范围与边界：** GitHub Actions 是外部任务来源；ARC、Karmada 与 Kubernetes 是复用组件。`karmada-search` 不在本方案逻辑图中；NPU 设备插件属于成员环境依赖，不应误画为自研核心模块。跨成员执行是设计能力，不据此承诺均匀分配或固定排队收益。

部署关系见 3.6；资源、状态与接口契约见 3.5、4.4～4.6 和第 5 章。



### 3.4.2 与周边系统元素的关系

| 周边元素 | 职责关系 | 进入/输出的实体 | 约束 |
|---|---|---|---|
| GitHub Actions | 外部任务入口与运行结果接收方 | Job 需求、Runner 注册、运行状态 | 入口标签、GitHub 身份与 JIT |
| 镜像仓库 | 提供 Controller/Runner/Action 镜像 | OCI Image | 可信源、平台架构、版本/digest |
| 密钥系统 | 供给和轮换 GitHub 主凭证、控制面身份 | Credential/CA/Token | 不在仓库明文保存、最小授权 |
| 部署与配置管理 | 安装官方组件及本项目 CRD/Policy | Chart/Manifest/配置参数 | 声明式、回滚与版本兼容 |
| 观测/审计系统 | 关联一次 Job 与执行实例 | Job/ER/Pod/Binding/Work 标识、事件与日志 | 不收集 Secret Data |

## 3.5 领域数据模型和归属

```mermaid
erDiagram
  GITHUB_JOB }o--|| RUNNER_SCALE_SET : "按标签匹配"
  RUNNER_SCALE_SET ||--o| LISTENER : "协作会话"
  RUNNER_SCALE_SET ||--o{ EPHEMERAL_RUNNER_SET : "ARC 管理"
  EPHEMERAL_RUNNER_SET ||--o{ EPHEMERAL_RUNNER : "临时实例"
  EPHEMERAL_RUNNER ||--o| RUNNER_POD : "生成执行实例"
  RUNNER_POD }o--|| MEMBER_CLUSTER : "单目标成员放置"
  RUNNER_POD ||--o{ RUNTIME_SECRET : "运行依赖"
  PROPAGATION_POLICY ||--o{ RESOURCE_BINDING : "控制传播匹配"
  RESOURCE_BINDING ||--o{ WORK : "分发至成员"
  WORK }o--|| MEMBER_CLUSTER : "定向交付"
```

图为**领域概念关系**，不等于原生 Kubernetes OwnerReference；动态资源真实归属由所选 ARC/Karmada 版本定义。以下表格是实现和销毁规格：

| 领域对象 | 权威生产者 | 主要关联/消费者 | 寿命分类 | 处置与销毁要求 |
|---|---|---|---|---|
| GitHub Job | GitHub Actions | 任务来源；不是 Kubernetes API 对象 | 单次/外部保留 | 任务队列、运行状态 |
| AutoscalingRunnerSet | 平台声明；ARC 管理 | Runner 服务及 GitHub 认证入口 | 长期 | ScaleSetName、GitHubConfigSecret、RunnerTemplate |
| AutoscalingListener / Listener Pod | ARC 生成 | 侦听任务需求并调整 ERS | 随 Scale Set | Token、CA、管理控制面 API |
| EphemeralRunnerSet / EphemeralRunner | ARC 生成 | 需求副本/单 Runner 实例生命周期 | 临时 | OwnerReference、状态、Finalizer |
| Runner Pod | ARC 生成 / Karmada 传播 | 成员的实际 Job 执行对象 | 临时 | SA、镜像、JIT Secret 引用、status |
| JIT Secret | ARC 动态生成 | 一次性 Runner 注册凭证 | 临时敏感 | 仅传目标执行成员；作业后回收 |
| 主 GitHub Secret | 平台密钥系统提供 | ARC 管理链路授权 | 受控敏感 | 目标设计不向无消费者成员扩散 |
| PropagationPolicy | 平台声明 / Karmada 消费 | 成员选择、匹配范围、依赖传播 | 长期配置 | ResourceSelectors、Placement、propagateDeps |
| ResourceBinding / Work | Karmada 生成 | 联邦调度与成员期望资源载体 | 随 Pod/策略 | 控制面到执行面的对象关系 |
| Pod.status | 成员 Kubernetes 产生 | 供 ARC 判断 Runner 状态 | 随 Pod | containerStatuses、conditions、Phase |
| MemberCluster / NPU 资源 | Karmada + 成员 K8s / Device Plugin | 集群资格、节点扩展资源 | 长期运行时 | Ready、labels、allocatable、扩展资源 |

**数据分级与销毁（D）**：GitHub PAT/App 凭证、Listener Token、JIT Secret 属敏感数据，禁止在日志、Git、工单附件输出值；运行时对象删除由 ARC/Karmada 所有权规则驱动，审计只保留非敏感 ID/状态。Job 日志、镜像与第三方系统持久数据的保留/销毁服从各自系统策略，**不因 Pod 删除自动消失**。证书/Token 轮换同时考虑控制面存储、成员副本和日志暴露面。销毁保留期/合规等级若需精确数值，由企业数据分类制度提供（P），不虚构默认周期。

## 3.6 部署拓扑与周边服务

![ARC 与 Karmada 联邦调度执行系统部署架构](images/arc-karmada-deployment-architecture.svg)

| 部署域 | 构件 | 运行接口 | 设计约束 |
|---|---|---|---|
| ARC 管理域 | 官方 ARC Controller | GitHub API、统一 Karmada API | 可以是管理 Kubernetes/容器部署；不要求在每个成员单独部署 |
| Karmada 控制面 | API、Policy/Scheduler、状态解释扩展 | 多成员 Kubernetes API | API、证书、网络、成员资格、可用性由部署管理提供 |
| Listener 成员域 | Listener Pod、独立控制面身份 | GitHub Actions、Karmada HTTPS API | 示例固定在 member1；目标成员是可配置的 Placement |
| Runner 执行域 | Runner Pod、JIT Secret、Runner SA、可选 NPU 资源 | GitHub Actions、成员 kubelet/Runtime | 只允许在满足资源、镜像和安全约束的成员运行 |
| 平台外 | 镜像仓库、身份/密钥服务、运维/观测 | 拉取、认证、日志/指标 | 不要求跨成员 Pod 网络直连 |

**配置输入清单（P）**：`runnerScaleSetName` / `runs-on`、GitHub URL、Runner 镜像/digest、控制面 API DNS/IP/port/SAN、CA、Token audience/TTL、目标成员集合、调度权重、Namespace、NPU 资源名、节点标签、业务 SLO。当前 Manifest 的 `karmada-runner`、`member1/member2`、`172.22.0.4:32443` 是示例值，后两者不应硬编码到通用部署工具。

### 3.6.1 安装与声明依赖次序

1. 提供 Karmada API、可达的成员 K8s API 和所需 ARC 版本 CRD；目标成员达到可参与传播的状态。
2. 准备命名空间、控制面身份/证书、Runner SA 与 GitHub 管理凭证。
3. 安装状态解释器、角色/绑定和传播策略，再声明 AutoscalingRunnerSet；保证 Listener 访问的是正确控制面。
4. 安装官方 ARC Controller 并由其调谐动态对象。组件顺序、重试和升级回滚在部署计划中实现，不在功能设计中复刻具体 Shell 命令。

## 3.7 上游官方资料及推导边界

| ID | 上游组件资料 | 本设计引用的能力 |
|---|---|---|
| ARC-1 | [GitHub：Actions Runner Controller](https://docs.github.com/en/actions/concepts/runners/actions-runner-controller) | Runner Scale Set、Listener、EphemeralRunner、JIT、生命周期 |
| ARC-2 | [GitHub：部署 Runner Scale Sets](https://docs.github.com/en/actions/how-tos/manage-runners/use-actions-runner-controller/deploy-runner-scale-sets) | Scale Set 部署配置、GitHub 凭证、Runner 模板 |
| KAR-1 | [Karmada：PropagationPolicy](https://karmada.io/docs/next/userguide/scheduling/propagation-policy/) | 资源选择器、成员放置、分副本/加权策略 |
| KAR-2 | [Karmada：Propagate dependencies](https://karmada.io/docs/userguide/scheduling/propagate-dependencies/) | 对象引用的 Secret/ConfigMap 依赖传播 |
| KAR-3 | [Karmada：Customizing Resource Interpreter](https://karmada.io/docs/userguide/globalview/customizing-resource-interpreter/) | ResourceInterpreterCustomization、AggregateStatus |
| K8S-1 | [Kubernetes：Service Accounts](https://kubernetes.io/docs/concepts/security/service-accounts/) | TokenRequest、SA 身份与 Token 生命周期 |
| K8S-2 | [Kubernetes：RBAC Good Practices](https://kubernetes.io/docs/concepts/security/rbac-good-practices/) | 角色权限及最小权限原则 |
| K8S-3 | [Kubernetes：Device Plugins](https://kubernetes.io/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/) | Device Plugin 扩展资源与节点分配 |

**使用边界**：官方文档分别证明 ARC 与 Karmada 等组件的原生能力；**并不证明两者组合的端到端兼容性**。所有跨组件行为应在第 4～5 章以 D 类设计契约表达；性能百分比、Token 自动轮换、多副本 status 和无损故障切换不得当作已有保证。

# 4 功能域规格变更与设计

> 本章只定义跨多个功能都要遵循的**统一规格**；第 5 章描述 F-01～F-06 如何调用这些规格，不复制上游内部算法或全部 CRD YAML。规格标记：`U` 官方机制、`D` 本功能域选定约束、`P` 外部输入。接口示例以现有仓库 Manifest 为基础；如果目标设计与现状不同，必须通过“现状/目标”明确呈现。

## 4.1 功能域规格变更

| 规格 ID | 涉及功能 | 现有参考配置/行为 | 目标设计与变化类型 | 开发责任 |
|---|---|---|---|---|
| SP-01 Runner 服务命名 | F-01、F-02 | `karmada-runner` 单 Scale Set；`minRunners: 0`, `maxRunners: 5` | D：对外保持稳定标签和同名契约；数值抽取为 P 类部署参数 | 平台配置 |
| SP-02 候选成员策略 | F-03、F-06 | member1/member2、`Divided / Weighted` | D：对每个 Runner Pod 在合格成员中确定目标，不承诺独立 Pod 逐轮平均 | Policy/调度适配 |
| SP-03 Runner Pod 选择范围 | F-03 | `arc-runners` 下全部 Pod 均被 Policy 选择 | **需修改**：目标只匹配 ARC 受管 Runner Pod；选择器须以选定版本的真实标签契约确定 | Policy 维护者 |
| SP-04 GitHub 主凭证传播 | F-02、F-04 | `arc-github-secret` 显式下发全部成员 | **需收敛**：控制面管理凭证不应无必要复制到成员；只传播被执行 Pod 引用的临时凭证 | 身份与 Secret 管理 |
| SP-05 Listener API 身份 | F-04 | 控制面 Token+CA 挂载，PoC 手工签发、硬编码 endpoint | **需改进**：可配置的 HTTPS endpoint、最小授权、Token 生命周期/轮换机制和错误分类 | 身份适配/部署管理 |
| SP-06 Runner Pod 状态 | F-02、F-05 | Lua 取首个非空 `status` | D：定义单目标 Pod、来源一致性、容器名和 Ready 条件的保留/缺失处理 | 状态解释适配 |
| SP-07 临时对象生命周期 | F-02、F-03 | ARC 与 Karmada 分别异步回收；历史 Finalizer 残留须独立处理 | D：单次任务资源可关联、可判定终态；禁用强清未知 Finalizer | 生命周期/运维 |
| SP-08 NPU 资源规格 | F-03、F-06 | NPU Runner 资源名/插件未在统一规范固定 | P/D：插件发布资源名、Runtime、驱动/节点匹配、资源请求约束 | 异构资源管理 |
| SP-09 安全/可观测性 | 所有功能 | 基于日志/Pod 与 Work 的局部诊断 | D：统一敏感数据边界、Job/Runner/Pod/Work 关联键和运维事件 | 平台运维 |

**变更口径**：SP-03、SP-04、SP-05 的“需修改”描述的是正式功能设计目标；仓库中的 PoC Manifest 仍为原有参考配置，**不能据此推定目标规格已经在代码中实现**。工程落地时应在对应 PR 中标明差异，而设计文档闭环不需要 PoC 证明。

## 4.2 SP-01 Runner 服务与任务入口规格设计

### 4.2.1 实现思路

采用 GitHub 官方 Runner Scale Set 机制：用户通过 `runs-on` 选择服务级标签，配置端在 `AutoscalingRunnerSet.spec.runnerScaleSetName` 声明对应 Runner 服务。Runner 的创建/回收由官方 ARC 管理（U）；本功能域维护“标签 → Scale Set → Runner 模板/成员候选池”的映射（D）。不为不同成员集群创建用户可见的专用标签（D）；若后续因机型/隔离级别存在不兼容资源池，可按资源类型额外定义多个 Scale Set（P）。

### 4.2.2 实现设计

| 规范字段 | 类型/位置 | 约束 |
|---|---|---|
| 服务标识 | GitHub workflow `runs-on`、`AutoscalingRunnerSet.spec.runnerScaleSetName` | 同一服务的字符串必须一致；示例 `karmada-runner`（P） |
| GitHub 目标 | `spec.githubConfigUrl` | 指向获得授权的 Repo/Org；不能让 Runner 去其他租户注册 |
| 主凭证 | `spec.githubConfigSecret` | 仅控制面可信管理域可读；凭证轮换独立管理 |
| 弹性区间 | `spec.minRunners/maxRunners` | `0 <= min <= max`；上限和队列目标由容量/配额输入确定（P） |
| Runner 镜像与 Pod 模板 | `spec.template` | 版本、架构、SA、资源请求、运行环境及安全上下文由服务规格决定 |
| 版本约束 | ARC CRD/Controller/Listener/RunnerSet label | 应选择互相兼容的版本；当前 `0.15.0` 仅为示例 |

异常：未匹配标签由 GitHub 排队/匹配机制呈现；没有权限或配额达到上限，不允许变更为错误成员标签绕过隔离。

## 4.3 SP-02/SP-03 联邦调度与依赖传播规格设计

### 4.3.1 实现思路

选择 Karmada PropagationPolicy 的成员候选集与资源匹配机制（U），而非自研任务分派控制器。**一个临时 Runner Pod 的目标是一个可执行成员**（D）；成员 Kubernetes 原生调度该成员内的节点。采用引用依赖传播 `propagateDeps`（U）交付 JIT Secret/SA，接受“Pod 先到、依赖后到”的异步窗口（D）。

### 4.3.2 实现设计

| 规格/字段 | 设计规则 | 备注 |
|---|---|---|
| `PropagationPolicy.metadata.namespace` | 对象位于待选 Runner Pod 的 Namespace；策略作用域不能意外包含其他租户 | 示例 `arc-runners` |
| `resourceSelectors` | **目标**仅匹配 ARC 产生的 Runner Pod；以所选 ARC 版本 Pod metadata.labels 为准；要求包含正向与负向匹配约束 | 当前 Manifest 匹配全 Namespace Pod，是差异项 SP-03 |
| `placement.clusterAffinity` | 目标成员必须已纳管、Ready、满足业务隔离和 Runner 类型/架构/设备资源要求 | 示例 member1/member2 |
| `replicaScheduling` | 按 Karmada 当前版本支持的语义配置 `Divided / Weighted`；对彼此独立的单 Pod 无逐轮平均保证 | 指标目标需 P 类 SLO |
| `propagateDeps` | 被 Pod 引用的 JIT Secret/ServiceAccount 须与目标成员一致 | 不能推断原子下发顺序 |
| 主凭证 | 不因 Runner Pod 放置而复制 `githubConfigSecret` 到任意执行成员；如有其他实际消费者须单独授权设计 | SP-04 |
| 执行态 | Pending/FailedMount/ErrImagePull 等状态由成员 K8s 管理；控制面观察状态，不直接重试 GitHub Job | 避免重复执行 |

**失败与边界**：无合格成员 → Runner 保持不可执行并给出可观测调度原因；成员故障 → 运行中 Job 是否重试服从 GitHub/ARC 上游语义和业务幂等约束，不能承诺自动迁移；JIT Secret 晚到 → 保留成员 K8s 挂载重试语义；策略冲突 → 遵守官方 PropagationPolicy 冲突处理机制、避免覆盖非本系统所有资源。

## 4.4 SP-04/SP-05 身份、凭证与跨控制面访问规格设计

### 4.4.1 实现思路

将身份分成管理面 ARC Controller、执行成员 ARC Listener、临时 Runner 三个信任域：Controller 访问 Karmada API 并持有 GitHub 管理凭证；Listener 持**独立、最小授权**的 Karmada API Token/CA；Runner 仅持短期 JIT 注册配置，默认不获得 Kubernetes API Token（D）。参照 Kubernetes ServiceAccount/TokenRequest/RBAC（U）设计生命周期，平台密钥系统承担签发、轮换和审计（P）。

### 4.4.2 实现设计

| 主体 → 目标 | 身份/认证 | 最小权限与安全边界 |
|---|---|---|
| Controller → Karmada API | 独立 Kubeconfig / SA 凭证，CA/TLS | 仅获 ARC 对象/Runner Pod/关联配置的必需 CRUD/Watch；管理员 kubeconfig 不是目标设计 |
| Listener → Karmada API | 独立控制面 SA Token，可信 CA，SAN 匹配的 HTTPS Endpoint | 对指定 ERS 的 `patch`，必要 ER 的 `get/patch` 与 `status patch`；当前 [RBAC Manifest](../manifests/03-rbac.yaml) 作为配置示例 |
| Runner Pod → GitHub Actions | ARC 分配的 JIT 临时凭证 | 不用 GitHub 主 PAT/App token；`automountServiceAccountToken: false`；最小网络出口 |
| Karmada → 成员 API | 联邦成员准入与集群身份 | 只承担成员资源传播、状态读取等官方联邦责任；成员凭证归属单独管理 |

**认证输入（P）**：API DNS/IP/Port、serverName/SAN、CA trust bundle、Token audience、expiration、轮换窗口、密钥管理系统和审计事件。凡 Token/CA 发生轮换，必须定义 Listener 重新加载或重建的行为及可观测就绪条件；不能把 `Secret` 内容更新等同自动无中断轮换。

**故障分类**：DNS/TCP 连通失败、TLS x509、401 未认证、403 越权、Token 已到期分别标识；恢复首先由凭证/网络所有者处理，不允许默默提升角色权限至 cluster-admin。日志只记录错误类别、身份名/资源路径及关联 ID，不打印凭证值。

## 4.5 SP-06/SP-07 状态与生命周期规格设计

### 4.5.1 实现思路

ARC 根据 Runner 对象及 Pod 状态感知临时执行实例；Karmada 负责将成员状态汇总回控制面（U）。本系统通过 ResourceInterpreterCustomization 定义单目标 Pod 的状态保留规则（D），不直接修改 ARC 内部就绪判断。生命周期与删除分别遵守 ARC 和 Karmada 的 owner/finalizer 模型（U）。

### 4.5.2 实现设计

**运行状态及转换语义：**

| 状态/条件 | 触发和观测 | 设计动作 / 边界 |
|---|---|---|
| Requested | Listener 对 ERS 需求做更新 | ARC 生成 Runner 相关对象；Karmada 不负责 GitHub Job 排队 |
| Scheduled | Karmada 选择成员并生成 Binding/Work | 一个 Runner Pod 确定目标成员；不得把无选定成员当作 Ready |
| Pending | 成员正在调度、镜像/Secret 未就绪 | 保留失败原因及重试责任；不创建重复 Job |
| Ready | 成员 Pod.status 满足 ARC 所需容器名和 Ready Conditions | AggregateStatus 保留字段、同步控制面 |
| Executing | Runner 完成 JIT 注册并执行 GitHub Job | GitHub 为任务结果权威源；不将 Pod Ready 等价于 Job 成功 |
| Completed / Failed | GitHub Job/Runner 提交终态 | ARC 控制器触发临时资源生命周期；错误终态有可观察原因 |
| Deleting | ARC/Karmada 异步清理相关资源 | 按对象归属与 Finalizer 调谐，不擅自强清 |

**Pod 状态解释约束**：目标仅对一个选定执行成员的 Pod.status 进行无损拷贝；必须保留 `containerStatuses[].name`、`conditions`、相位、容器状态等上游 ARC 消费的关键字段；没有 status 时保持未就绪而非伪造 Ready。当前 Lua“第一个非空 status”的实现依赖**单目标、无冲突输入**；如果支持迁移/副本，必须在本功能域重新设计冲突解决算法。

**删除语义**：临时 Runner、Pod、JIT 及 Karmada Binding/Work 应按任务引用相关联；最终清理由各自控制器调谐，不承诺原子删除。异常 Finalizer 要分辨对象生产者与运维权限，不能以任意 patch 清空来宣称完成。

## 4.6 SP-08 NPU 资源规格设计

### 4.6.1 实现思路

复用 Kubernetes Device Plugin 扩展资源和原生节点调度（U）；本功能域定义“Runner 服务规格 → 目标成员候选集 → 设备节点资源请求”的映射（D）。不在联邦层重新实现 NPU 内部设备分配。需要明确 CPU/架构、OS、驱动/固件、Device Plugin、容器 Runtime 的兼容性输入（P）。

### 4.6.2 实现设计

| 输入参数（P） | 数据源 | 规则 |
|---|---|---|
| NPU 扩展资源名 | 目标厂商插件提供的 Kubernetes 资源名 | 必须与成员 Node allocatable 和 Pod resources.requests/limits 一致；不能假定通用 `npu` 字符串 |
| 设备份额与数量 | 设备插件的资源粒度和可共享机制 | 整数或厂商定义的资源份额；不超额请求；分配语义以厂商 API 为准 |
| 成员/节点标签 | 集群资源管理系统 | Placement 决定合格成员；成员 scheduler 决定节点；两层约束不可冲突 |
| 镜像/驱动/Runtime | 镜像仓库、节点基础设施 | CUDA/CANN 等设备栈参数按具体硬件选型；不能由 Device Plugin 一项推断全部兼容 |
| Runner Scale Set | 业务服务分层或工作流标签 | 不兼容硬件需明确区分服务入口，避免错误匹配并静默切换资源类型 |

失败语义：无兼容设备 → 排队或 Pending 并可观察；插件未就绪或节点扩展资源消失 → 不以普通 CPU Runner 顶替有 NPU 约束的任务；设备分配失败由 Kubernetes/插件及设备运维分别定位。最终功能的实际厂商资源名称、硬件型号和性能约束属于 P 类选型，不阻塞设计内容形成。

## 4.7 SP-09 跨功能质量与可服务性规格

| 属性 | 全功能域统一要求 | 可配置输入/界限 |
|---|---|---|
| 安全 | Controller/Listener/Runner 身份隔离、最小 Secret 传播、TLS 可验证、审计脱敏 | Token 有效期、凭证系统、租户隔离策略（P） |
| 可靠性 | 区分上游调谐可恢复故障与需人工修复的凭证/网络错误；禁止无依据 Job 重试 | SLO/恢复目标由业务确定（P） |
| 可用性 | 不将“成员联邦可用”推导为 GitHub 服务可用；明确控制面、成员和网络故障域 | 目标部署拓扑、HA 等级由平台定义（P） |
| 性能与并发 | 记录 GitHub Job queue、Listener scale 请求、Runner Ready 等耗时分段；不能以单轮分布证明均衡 | 容量/排队时间/延迟指标阈值（P） |
| 可服务性 | 通过 Job Run、Scale Set、ER、Pod UID、Binding/Work、成员 Cluster 建立诊断关联 | 日志保留时间、审计级别遵循企业制度（P） |
| 升级与兼容 | ARC CRD、Controller/Listener、Runner 镜像、Karmada API 的版本关系显式记录 | 目标版本和变更/回滚窗口（P） |

**统一需求闭环**：SP-01～SP-09 的实现责任分配至第 5 章 F-01～F-06；异常与 FMEA 均采用“故障源 → 影响 → 检测 → 缓解/恢复 → 上游与本项目责任”模式。任何需要真实性能数值、设备型号或企业安全等级的字段均保留为 P 类输入，不以 PoC 作为文档完成条件。

# 5 功能实现设计

> 本章按**功能单元**组织，每个功能汇总：功能概述、技术取舍、用例运行时序、用户接口、实现接口及 FMEA/DFX。其共同规格只引用第 4 章的 SP 编号，不重复堆叠 YAML 细节。对已有 Kubernetes/ARC/Karmada CRD，以其 OpenAPI schema 和字段契约为准；**没有新增 REST 服务，因此不虚构 Swagger Endpoint**。如实际新增 REST API，必须另补 OpenAPI 3 规范。
>
> US 图源自此前主设计文档的 8 张运行图，在此按 F 功能聚合（F-02: US-02/07，F-03: US-03/04）。部分交互为本系统的集成设计推论，不是官方对组合系统端到端行为的保证。

## 5.1 F-01 统一 Runner 服务入口

### 5.1.1 功能概述

**关联需求**：SR-01-01、SR-01-02；**用例**：UC-01 通过统一标签提交工作流、UC-02 无匹配或无可用 Runner；**功能域规格**：SP-01。

对业务方屏蔽 member1/member2 和资源池拓扑，只暴露可授权的 GitHub Runner Scale Set 服务标签。输入为工作流 runs-on 与配置的 Scale Set，输出为已匹配任务需求或可见的排队/授权状态。

**边界/归属**：只维护本功能域的配置或适配；ARC/Karmada/Kubernetes 上游内部调谐逻辑不由本功能重写。

### 5.1.3 实现思路

选用 ARC 官方 Scale Set + GitHub 原生 runs-on（U）；由平台将服务标签、Runner 模板、目标 GitHub Repo/Org 和容量区间关联（D）。不开发自研 GitHub Job Queue；若资源型号/隔离不同，以多个显式服务区分（P）。

### 5.1.4 设计思路

开发者 → GitHub Job 队列 → Listener 会话 → EphemeralRunnerSet 期望量 → ARC Runner 供给；具体成员由 F-03 决定。任务未匹配 Scale Set 时保持 GitHub 的等待与失败可诊断状态，平台不得自动替换标签。

#### 5.1.4.1 UC-01 通过统一标签提交工作流——运行时序（US-01）

```mermaid
sequenceDiagram
    autonumber
    box rgb(232,243,255) 用户与任务入口
        actor Dev as 开发者
        participant GH as GitHub Actions
    end
    box rgb(255,240,222) ARC Runner 管理
        participant L as ARC Listener
        participant ERS as EphemeralRunnerSet
        participant C as ARC Controller
    end
    participant K as Karmada API

    Dev->>GH: 提交工作流 runs-on karmada-runner
    GH->>GH: Job 进入匹配的 Scale Set 队列
    L->>GH: 维持单一 Listener 会话并获取任务需求
    GH-->>L: 返回分配给 Scale Set 的 Job 需求
    L->>ERS: Patch 期望 Runner 副本数
    C->>ERS: Watch 并调谐期望与实际副本
    C->>K: 创建 EphemeralRunner 与 Runner Pod
    Note over Dev,K: 统一 Runner 标签屏蔽 member1 和 member2，后续由 Karmada 选择成员
    Note over GH,L: 此处代表一个 Scale Set 的 Listener 会话，不限制其他 Scale Set
```

**图的架构含义**：展示该用例跨组件的主要消息路径与对象更新。当前图是设计交互，不是实测调用栈；真实上游控制器内部仍以目标版本官方文档为准。

### 5.1.5 用户接口设计

用户接口是 Workflow YAML 中的 `jobs.<job>.runs-on`，不是自建网页 UI。示例：`runs-on: karmada-runner`；该字符串必须与 `AutoscalingRunnerSet.spec.runnerScaleSetName` 配置一致。若没有新增管理 UI，则“可视化页面设计”为 N/A，运维使用 GitHub/声明式配置。

### 5.1.6 实现接口设计

#### 5.1.6.1 接口设计

| 调用方 → 提供方 | 原生接口/对象 | 关键字段与输入输出 | 身份或前置条件 | 异常/幂等及诊断 | 依据/决策 |
|---|---|---|---|---|---|
| 外部 GitHub workflow → Scale Set | GitHub Actions runs-on | string 标签到服务名映射 | Repo/Org 权限和 Runner Service 名称一致 | 未匹配 → GitHub queued；不偷换成员标签 | U:ARC-1; D:SP-01 |
| 平台配置 → ARC Controller | actions.github.com/v1alpha1 AutoscalingRunnerSet（声明式） | metadata.name/ns, spec.runnerScaleSetName, githubConfigUrl, githubConfigSecret, minRunners, maxRunners, template | Karmada API CRD 权限；GitHub 主凭证保管在可信管理域 | 版本/权限/配额异常可观测；禁用隐式扩权 | U:ARC-2; D |

**开发交付边界**：只新增/修改本仓库负责的 RunnerSet 模板、PropagationPolicy、RBAC、身份适配、Lua/状态解释或资源参数；对官方 API 的精确字段类型、枚举和版本兼容限制，必须以所选版本的 CRD OpenAPI Schema 为准。上表不能替代完整 upstream schema。

### 5.1.9 DFX分析

**功能内 DFX**：并发由 `maxRunners` 和 GitHub/ARC 上游机制控制；服务容量和排队目标为 P 类输入。安全要求避免跨 Repo/Org 意外注册，并确保工作流不直接获得 GitHub 管理凭证。

**可靠性 FMEA（设计阶段定性分析；严重度/发生概率/探测度的数值及 RPN 未经业务基准审批，暂不虚构）**

| FMEA 编号 | 失效模式 | 对业务/系统的影响 | 发现与定位 | 预防/恢复措施 | 责任主体 |
|---|---|---|---|---|---|
| F01-01 | 标签拼写或命名漂移 | Job 长时间等待 | GitHub runs-on/Scale Set 清单比对 | 统一配置源与变更审查 | 平台维护者 |
| F01-02 | GitHub 管理凭证无效 | Scale Set 会话/Runner 注册受阻 | Listener/Controller 认证事件 | 刷新凭证与隔离错误，不重写 Job 标签 | 身份服务/ARC |

**可服务性与安全检查**：关联 GitHub Job Run → RunnerSet → ER/Pod UID → Karmada Binding/Work → 成员集群，采集必要状态与错误分类，不记录主 GitHub Token、JIT Secret Data、Kubeconfig 和私钥。若此功能提供可配置的 SLO，明确指标定义、度量位置、业务提供的阈值及失败处理，而不把一次 PoC 运行值当作产品规格。

## 5.2 F-02 Runner 弹性供给及临时资源回收

### 5.2.1 功能概述

**关联需求**：SR-02-01、SR-02-02；**用例**：UC-03 Job 需求驱动 Runner 创建、UC-04 Job 结束回收 Runner 与凭证；**功能域规格**：SP-01、SP-06、SP-07。

把临时 Runner 供给与回收集中在 ARC，成员只负责运行。输入是 Listener 的任务需求及 ARC CR；输出是单 Job 执行的临时 Runner 与最终释放的对象。

**边界/归属**：只维护本功能域的配置或适配；ARC/Karmada/Kubernetes 上游内部调谐逻辑不由本功能重写。

### 5.2.3 实现思路

采用 ARC 官方 Listener/Controller/EphemeralRunnerSet/EphemeralRunner 生命周期（U），Karmada 仅负责成员 Pod/Secret 分发及副本回收（D）。不自行维护 runnerID 与 Pod 名称的固定格式；采用动态 Owner/UID 关联。

### 5.2.4 设计思路

需求更新 → ERS 期望量 → EphemeralRunner / Pod / JIT Secret → 成员执行 → GitHub Job 状态 → ARC 临时资源回收 → Karmada Binding/Work 与成员对象异步清理。正常 Job 与失败 Job 都进入可诊断终态，但不能在缺乏 GitHub 幂等约束时擅自重派发。

#### 5.2.4.1 UC-03 Job 需求驱动 Runner 创建——运行时序（US-02）

```mermaid
sequenceDiagram
    autonumber
    box rgb(232,243,255) 外部任务入口
        participant GH as GitHub Actions
    end
    box rgb(255,240,222) ARC 生命周期
        participant L as ARC Listener
        participant ERS as EphemeralRunnerSet
        participant C as ARC Controller
    end
    box rgb(231,249,239) 联邦执行
        participant K as Karmada 控制面
        participant Pod as 成员 Runner Pod
    end

    GH-->>L: 下发已匹配 Scale Set 的任务需求
    L->>ERS: 调整期望临时 Runner 副本数
    C->>ERS: 监听 ERS 变更
    C->>K: 创建 EphemeralRunner 和 Runner Pod
    C->>K: 创建 JIT 注册凭证 Secret
    K->>Pod: 下发 Runner Pod 及依赖对象
    Pod->>GH: 使用 JIT 凭证注册临时 Runner
    GH-->>Pod: 分配并执行 Job
    Pod-->>GH: 回传 Job 状态及日志
    GH-->>L: 任务完成和需求变化
    L->>ERS: 更新期望 Runner 副本数
    C->>K: 删除已结束的临时 Runner 相关对象
    Note over C,Pod: 成员集群不需要常驻 ARC Controller，清理按 ARC 和 Karmada 的对象生命周期完成
    Note over C,K: ARC 和 Karmada 异步回收按各自所有权处理，残留 Finalizer 需独立治理
```

**图的架构含义**：展示该用例跨组件的主要消息路径与对象更新。当前图是设计交互，不是实测调用栈；真实上游控制器内部仍以目标版本官方文档为准。

#### 5.2.4.2 UC-04 Job 结束回收 Runner 与凭证——运行时序（US-07）

```mermaid
sequenceDiagram
    autonumber
    box rgb(232,243,255) 任务完成事件
        participant Pod as Runner Pod
        participant GH as GitHub Actions
        participant ARC as ARC Controller
    end
    box rgb(255,240,222) 联邦清理链路
        participant API as Karmada 控制面
        participant BW as ResourceBinding / Work
    end
    box rgb(231,249,239) 成员与验证
        participant Member as 成员资源
        participant Verify as 平台资源审计
    end

    Pod-->>GH: 上报 Job 执行结果与状态
    GH-->>ARC: 任务完成状态进入 ARC 生命周期
    ARC->>API: 清理本轮 EphemeralRunner / Runner Pod
    API->>BW: 调谐关联 Binding 与 Work 删除
    BW->>Member: 删除成员 Runner Pod 等传播对象
    ARC->>API: 清理关联一次性 JIT Secret
    API->>Member: 传播凭证对象删除
    Verify->>API: 按 UID 盘点本轮 ER / Pod / Secret / Binding / Work
    Verify->>Member: 盘点成员中的本轮 Pod 和 Secret
    Member-->>Verify: 返回残留对象清单
    API-->>Verify: 返回控制面残留对象清单
    Note over Verify: 审计当前任务关联资源的最终状态，历史异常对象另行治理
    Note over ARC,Member: 删除按各控制器异步调谐完成，不代表所有历史对象零残留
```

**图的架构含义**：展示该用例跨组件的主要消息路径与对象更新。当前图是设计交互，不是实测调用栈；真实上游控制器内部仍以目标版本官方文档为准。

### 5.2.5 用户接口设计

开发者只在 GitHub Actions 看到任务排队、执行与结果；用户无需创建 Pod、Secret 或调用 Kubernetes API。平台运维可通过对象/日志和 Run 标识关联一次任务。额外用户页面：N/A。

### 5.2.6 实现接口设计

#### 5.2.6.1 接口设计

| 调用方 → 提供方 | 原生接口/对象 | 关键字段与输入输出 | 身份或前置条件 | 异常/幂等及诊断 | 依据/决策 |
|---|---|---|---|---|---|
| Listener → ERS | Kubernetes API PATCH: actions.github.com/v1alpha1 EphemeralRunnerSet | 对指定 Scale Set 的期望副本调整；对象 UID/RV、资源名动态 | 独立 Listener SA，指定 ERS patch | 冲突更新遵循 resourceVersion；拒绝越权或重复写错 ERS | U:ARC-1; D:SP-01 |
| ARC Controller → Karmada API | EphemeralRunner、Runner Pod、临时 JIT Secret | Pod 模板含镜像、SA、JIT Secret 引用；临时 ER 动态生成 | Controller 最小 Karmada API 权限 | 资源未创建/镜像不可用保留事件与诊断 | U:ARC-1; D |
| ARC/Karmada → 成员 K8s | Pod 删除；ResourceBinding/Work 生命周期 | 关联 UID/owner、已选成员及临时 Secret | 分层的 Owner/Finalizer | 异步删除、Finalizer 阻塞独立处理，不强清 | U:ARC-1,KAR-1; D:SP-07 |

**开发交付边界**：只新增/修改本仓库负责的 RunnerSet 模板、PropagationPolicy、RBAC、身份适配、Lua/状态解释或资源参数；对官方 API 的精确字段类型、枚举和版本兼容限制，必须以所选版本的 CRD OpenAPI Schema 为准。上表不能替代完整 upstream schema。

### 5.2.9 DFX分析

**功能内 DFX**：并发上限由 Scale Set 配置控制；设计不承诺无损故障切换。JIT Secret 按短期敏感数据处置，不把 Secret.data 记录在审计或工作流日志中。

**可靠性 FMEA（设计阶段定性分析；严重度/发生概率/探测度的数值及 RPN 未经业务基准审批，暂不虚构）**

| FMEA 编号 | 失效模式 | 对业务/系统的影响 | 发现与定位 | 预防/恢复措施 | 责任主体 |
|---|---|---|---|---|---|
| F02-01 | 需求变化与 Controller 调谐延迟 | Job 排队增加 | ERS 期望与实际、Controller Event | 遵循 ARC 上游 reconcile；容量告警 | ARC/运维 |
| F02-02 | 注册后 Pod 异常或 Job 失败 | 执行失败/重复风险 | GitHub Run/ER/Pod 相关事件 | 遵守 ARC/GitHub 重试语义，禁止盲目重复 dispatch | GitHub/ARC |
| F02-03 | 临时对象 Finalizer 阻塞 | Pod/Secret 残留 | ER/Pod/Binding/Work 关联 UID 与删除状态 | 由对应对象 owner 调谐；异常人工处置 | ARC/Karmada/运维 |

**可服务性与安全检查**：关联 GitHub Job Run → RunnerSet → ER/Pod UID → Karmada Binding/Work → 成员集群，采集必要状态与错误分类，不记录主 GitHub Token、JIT Secret Data、Kubeconfig 和私钥。若此功能提供可配置的 SLO，明确指标定义、度量位置、业务提供的阈值及失败处理，而不把一次 PoC 运行值当作产品规格。

## 5.3 F-03 Runner 联邦成员调度与依赖传播

### 5.3.1 功能概述

**关联需求**：SR-03-01、SR-03-02；**用例**：UC-05 在合格成员放置 Runner、UC-06 JIT Secret 晚到后的依赖恢复、UC-10 成员不可用或无可调度资源；**功能域规格**：SP-02、SP-03、SP-04。

让 ARC 创建的单个 Runner Pod 可在候选成员运行，且 Runner 所需临时凭证随 Pod 同成员可用。输入是 Pod/Policy、成员资格及对象引用；输出为 Binding/Work、成员 Pod/Secret 和调度状态。

**边界/归属**：只维护本功能域的配置或适配；ARC/Karmada/Kubernetes 上游内部调谐逻辑不由本功能重写。

### 5.3.3 实现思路

以 Karmada PropagationPolicy/clusterAffinity/propagateDeps（U）实现对象级跨集群分发；目标 Policy 仅选择 ARC Runner Pod（D）。不新增跨成员网络隧道或自研联邦分配器。`Divided/Weighted` 属每资源调度策略，不代表独立 Pod 的逐轮均匀分布。

### 5.3.4 设计思路

Controller 创建 Runner Pod → Policy 匹配 Runner 受管标签 → Karmada 选择满足资源/隔离条件的成员 → Binding/Work 分发 Pod 与被引用 JIT Secret → 成员调度节点 → Runner Ready 与后续注册。缺少 Secret 容忍短暂 FailedMount；成员不可用时保持不可执行并报告原因，不擅自复制执行中的 Job。

#### 5.3.4.1 UC-05 在合格成员放置 Runner——运行时序（US-03）

```mermaid
sequenceDiagram
    autonumber
    box rgb(255,240,222) Runner 资源入口
        participant C as ARC Controller
        participant API as Karmada API
    end
    box rgb(232,243,255) Karmada 联邦编排
        participant P as PropagationPolicy
        participant D as Detector
        participant S as Karmada Scheduler
        participant B as Binding / Work
    end
    box rgb(231,249,239) 成员执行
        participant M1 as member1
        participant M2 as member2
    end

    C->>API: 创建 Runner Pod
    API->>P: 根据选择器匹配 arc-runner-pod-propagation
    Note over P,S: clusterAffinity 允许 member1 和 member2，Divided / Weighted 按各 Pod 分别决策
    D->>API: 观察待传播的 Runner Pod
    D->>B: 创建 ResourceBinding
    S->>B: 写入选定的成员集群 Placement
    B->>M1: 通过 Work 下发到 member1（被选中时）
    opt 另一个独立 Pod 被调度至 member2
        B->>M2: 通过 Work 下发到 member2（被选中时）
    end
    M1->>M1: 原生 kube-scheduler 选择节点并执行
    M2->>M2: 如收到 Pod 则在本集群执行
    Note over S,M2: 不保证多个独立 Pod 在每轮按固定比例均匀分布
    Note over C,API: Karmada 选择成员集群，成员 kube-scheduler 负责节点级调度
```

**图的架构含义**：展示该用例跨组件的主要消息路径与对象更新。当前图是设计交互，不是实测调用栈；真实上游控制器内部仍以目标版本官方文档为准。

#### 5.3.4.2 UC-06 JIT Secret 晚到后的依赖恢复——运行时序（US-04）

```mermaid
sequenceDiagram
    autonumber
    box rgb(255,240,222) ARC 对象创建
        participant C as ARC Controller
        participant API as Karmada API
    end
    box rgb(232,243,255) Karmada 依赖传播
        participant P as PropagationPolicy
        participant B as Binding / Work
    end
    box rgb(231,249,239) 目标成员集群
        participant Secret as JIT Secret
        participant Pod as Runner Pod
    end

    C->>API: 创建瞬时 JIT Secret
    C->>API: 创建引用 Secret 的 Runner Pod
    P->>API: 匹配 Runner Pod 并启用 propagateDeps
    API->>B: 为 Pod 选择目标成员并跟踪依赖
    B->>Secret: 向相同成员传播 JIT Secret
    B->>Pod: 向相同成员传播 Runner Pod
    alt Secret 尚未到达
        Pod->>Secret: 尝试读取引用的凭证
        Secret-->>Pod: 暂时不可用（可出现 FailedMount）
        Note over Secret,Pod: 异步依赖传播，不是原子事务
    else Secret 已准备好
        Pod->>Secret: 读取 JIT 凭证
        Secret-->>Pod: 返回注册凭证
    end
    Pod->>Pod: Runner 启动并向 GitHub 注册
    Note over API,Pod: 凭证与 Pod 共置于选定成员，任务结束后的回收见 US-07
```

**图的架构含义**：展示该用例跨组件的主要消息路径与对象更新。当前图是设计交互，不是实测调用栈；真实上游控制器内部仍以目标版本官方文档为准。

#### 5.3.4.3 UC-10 成员不可用——异常运行时序

```mermaid
sequenceDiagram
    autonumber
    participant ARC as ARC Controller
    participant API as Karmada API
    participant K as Karmada Policy/Scheduler
    participant Member as 成员 Kubernetes
    participant Ops as 平台运维
    ARC->>API: 声明待执行 Runner Pod
    K->>Member: 查询成员 Ready 和可调度资格
    alt 无满足条件的成员
      K-->>API: 无合格 placement / 等待资源
      API-->>Ops: 暴露状态与调度原因
      Note over ARC,Ops: 不隐式复制运行中 Job 或删除 GitHub Job
    else 成员在分发后故障
      Member--xK: 状态不可达或 Work 无法完成
      K-->>Ops: 报告联邦成员/对象异常
      Note over ARC,Ops: 故障恢复与 Job 重试分别由上游机制和业务幂等性约束
    end
```

**异常约束**：不得将成员失联等价于 GitHub Job 已失败或可安全重复执行；设计仅保证可诊断的异常状态和责任边界。

### 5.3.5 用户接口设计

业务方继续使用同一 runs-on 标签，无需选择成员。平台用户可以查看任务实际成员、资源不足原因和依赖状态；若没有新建管理界面，采用既有 GitHub/观测与 Kubernetes Event，专用 UI：N/A。

### 5.3.6 实现接口设计

#### 5.3.6.1 接口设计

| 调用方 → 提供方 | 原生接口/对象 | 关键字段与输入输出 | 身份或前置条件 | 异常/幂等及诊断 | 依据/决策 |
|---|---|---|---|---|---|
| ARC → Karmada API | v1/Pod、v1/Secret、v1/ServiceAccount | Runner Pod 标签、Namespace、SA、SecretKeyRef；JIT 动态 Secret | 控制面 CR 访问授权 | 资源丢失、JIT 未到可观测，不重试 GitHub 任务 | U:ARC-1; D |
| 平台 → Karmada Scheduler/Controller | policy.karmada.io/v1alpha1 PropagationPolicy | resourceSelectors、clusterAffinity、propagateDeps、replicaScheduling | policy 管理角色，命名空间隔离 | 无匹配/候选集为空 → 留置与调度事件 | U:KAR-1,KAR-2; D:SP-02,SP-03 |
| Karmada → 成员 | ResourceBinding/Work（上游动态资源） | 成员目标、Pod/引用 Secret/SA、同步状态 | 联邦成员访问身份 | 成员不可达/冲突遵循 Karmada 官方 reconcile | U:KAR-1,KAR-2; D |

**开发交付边界**：只新增/修改本仓库负责的 RunnerSet 模板、PropagationPolicy、RBAC、身份适配、Lua/状态解释或资源参数；对官方 API 的精确字段类型、枚举和版本兼容限制，必须以所选版本的 CRD OpenAPI Schema 为准。上表不能替代完整 upstream schema。

### 5.3.9 DFX分析

**功能内 DFX**：安全隔离禁止广域 Pod 和主 GitHub Secret 传播；性能指标分离“成员选择耗时”“依赖就绪时间”“Runner 注册时间”，不虚构全局公平。

**可靠性 FMEA（设计阶段定性分析；严重度/发生概率/探测度的数值及 RPN 未经业务基准审批，暂不虚构）**

| FMEA 编号 | 失效模式 | 对业务/系统的影响 | 发现与定位 | 预防/恢复措施 | 责任主体 |
|---|---|---|---|---|---|
| F03-01 | Pod 选择器过宽 | 误传播非 Runner Pod / 租户边界被扩大 | Policy selectors 与实际 Pod 标签审查 | 目标选择器限定受管标签、配置审查 | 策略管理员 |
| F03-02 | JIT Secret 晚于 Pod 到达 | Pod FailedMount / 启动推迟 | 成员 Event + Secret 引用检查 | 依赖传播/成员 kubelet 重试，不强制重派发 | Karmada/Kubernetes |
| F03-03 | 目标成员离线或设备容量不足 | Runner Pending / 排队 | Cluster Ready、Work/Binding、Pod Event | 成员筛选、故障诊断，避免重复执行 | Karmada/运维 |

**可服务性与安全检查**：关联 GitHub Job Run → RunnerSet → ER/Pod UID → Karmada Binding/Work → 成员集群，采集必要状态与错误分类，不记录主 GitHub Token、JIT Secret Data、Kubeconfig 和私钥。若此功能提供可配置的 SLO，明确指标定义、度量位置、业务提供的阈值及失败处理，而不把一次 PoC 运行值当作产品规格。

## 5.4 F-04 Listener 跨控制面认证与访问

### 5.4.1 功能概述

**关联需求**：SR-04-01；**用例**：UC-08 Listener 访问统一 Karmada API、UC-11 Token 失效或证书不匹配；**功能域规格**：SP-04、SP-05。

让部署在执行成员的 Listener 正确访问 ARC 控制面对象，且不获得比所需更新更大的 Karmada 权限。输入为 API endpoint、CA/Token、RBAC；输出是经认证的 ERS 操作和鉴权诊断。

**边界/归属**：只维护本功能域的配置或适配；ARC/Karmada/Kubernetes 上游内部调谐逻辑不由本功能重写。

### 5.4.3 实现思路

采用 Kubernetes ServiceAccount/TokenRequest/RBAC（U）构建独立控制面身份；Listener 应按控制面 serverName/SAN 与 CA 校验 API Server（D）。当前 hostAliases/环境变量/Secret 挂载是仓库示例，目标部署要求 endpoint 参数化与凭证轮换策略。

### 5.4.4 设计思路

密钥/部署面提供控制面身份 → 注入 Listener → client 验证服务器证书 → Karmada 验证 Token 与 Role → Listener Watch/Patch ERS。任何 x509、401、403 应保留可区分的诊断，并由证书/Token/授权责任方修复。

#### 5.4.4.1 UC-08 Listener 访问统一 Karmada API——运行时序（US-05）

```mermaid
sequenceDiagram
    autonumber
    box rgb(231,249,239) member1 成员集群
        participant L as ARC Listener Pod
    end
    box rgb(255,240,222) 网络与凭证配置
        participant T as listenerTemplate
        participant N as hostAliases / ENV
        participant SA as karmada-token / CA
    end
    box rgb(232,243,255) 联邦控制面
        participant API as Karmada API Server
        participant ERS as EphemeralRunnerSet
    end

    T->>L: 注入 Listener Pod 模板及挂载配置
    N->>L: 设置 KUBERNETES_SERVICE_HOST / PORT
    N->>L: hostAliases 映射控制面域名
    SA->>L: 挂载 Karmada ServiceAccount Token 和 CA
    L->>API: HTTPS 请求 Karmada API
    L->>L: 校验服务端 TLS 证书链与 SAN
    API->>API: 认证 Token 并按 RBAC 授权
    alt 证书与权限均有效
        API-->>L: 建立可用的 API 访问
        L->>ERS: Watch / Patch 期望 Runner 副本数
        ERS-->>L: 返回对象状态
    else TLS 或 RBAC 不匹配
        API-->>L: x509 / Unauthorized / Forbidden
        Note over L,SA: 需修正域名、凭证或最小权限授权
    end
    Note over L,ERS: 跨控制面身份及配置 Secret 更新语义须在集成设计中明确
```

**图的架构含义**：展示该用例跨组件的主要消息路径与对象更新。当前图是设计交互，不是实测调用栈；真实上游控制器内部仍以目标版本官方文档为准。

### 5.4.5 用户接口设计

开发者对鉴权无直接操作。平台运维查看 Listener ready、认证错误类别和身份版本。安全管理员维护角色与凭证轮换；不要求专用 UI，使用已有密钥与监控系统。

### 5.4.6 实现接口设计

#### 5.4.6.1 接口设计

| 调用方 → 提供方 | 原生接口/对象 | 关键字段与输入输出 | 身份或前置条件 | 异常/幂等及诊断 | 依据/决策 |
|---|---|---|---|---|---|
| Listener → Karmada API | HTTPS Kubernetes API（EphemeralRunnerSet） | host/port/SNI、CA、bearer token、resourceNames=Scale Set | TokenRequest + 独立 SA | x509、401、403、超时独立分类 | U:K8S-1,K8S-2; D:SP-05 |
| 部署/密钥面 → Listener Pod | listenerTemplate/Secret 或受控投射卷 | CA、Token/有效期、KUBERNETES_SERVICE_HOST/PORT 或 kubeconfig | Pod 只读最小挂载，禁止明文日志 | 轮换后重新加载行为必须明确，不假定自动生效 | D |
| 安全管理员 → K8s RBAC | Role/RoleBinding | ephemeralrunnersets patch(限名)、ephemeralrunners get/patch、status patch | 跨 namespace subject 确认；禁止 cluster-admin | 角色漂移按差异检查纠正 | U:K8S-2; D |

**开发交付边界**：只新增/修改本仓库负责的 RunnerSet 模板、PropagationPolicy、RBAC、身份适配、Lua/状态解释或资源参数；对官方 API 的精确字段类型、枚举和版本兼容限制，必须以所选版本的 CRD OpenAPI Schema 为准。上表不能替代完整 upstream schema。

### 5.4.9 DFX分析

**功能内 DFX**：安全为核心：可验证 TLS、受控 Token 生命周期、独立 SA、控制面最小 RBAC、凭证不落日志。高可用 Listener 与轮换不中断属于 P 类平台运维目标，设计不能默认宣称已支持。

**可靠性 FMEA（设计阶段定性分析；严重度/发生概率/探测度的数值及 RPN 未经业务基准审批，暂不虚构）**

| FMEA 编号 | 失效模式 | 对业务/系统的影响 | 发现与定位 | 预防/恢复措施 | 责任主体 |
|---|---|---|---|---|---|
| F04-01 | DNS/CA/SAN 不匹配 | Listener 无法建立 Karmada API 会话 | x509 / endpoint 连通性诊断 | 统一证书域名和服务发现，拒绝关闭 TLS 校验 | 部署/PKI |
| F04-02 | Token 失效或未被正确加载 | ERS 无法更新 | 401 / Token 到期/身份版本 | 平台重签与受控重载；不直接扩权 | 安全/运维 |
| F04-03 | RBAC 漂移 | 403/需求无法更新或越权 | Role/Binding 对照契约审计 | 最小权限修复；保持 Role 目标名约束 | 安全/平台 |

**可服务性与安全检查**：关联 GitHub Job Run → RunnerSet → ER/Pod UID → Karmada Binding/Work → 成员集群，采集必要状态与错误分类，不记录主 GitHub Token、JIT Secret Data、Kubeconfig 和私钥。若此功能提供可配置的 SLO，明确指标定义、度量位置、业务提供的阈值及失败处理，而不把一次 PoC 运行值当作产品规格。

## 5.5 F-05 Runner Pod 状态反馈与一致性

### 5.5.1 功能概述

**关联需求**：SR-05-01；**用例**：UC-09 Runner Ready 状态反馈、UC-12 状态缺失、冲突或滞后；**功能域规格**：SP-06、SP-07。

使 ARC 能够读取有容器名称、状态和 Ready Condition 的控制面 Runner Pod 状态，避免因联邦层丢失字段导致误判。输入成员 Pod.status，输出控制面完整聚合状态。

**边界/归属**：只维护本功能域的配置或适配；ARC/Karmada/Kubernetes 上游内部调谐逻辑不由本功能重写。

### 5.5.3 实现思路

使用 Karmada 的 ResourceInterpreterCustomization/AggregateStatus（U）对 Pod 状态做显式解释（D），不修改 Karmada/ARC 官方源码；当前只定义**一个 Runner Pod 对应一个选定执行成员**的语义。

### 5.5.4 设计思路

成员 kubelet 更新 status → Karmada 收集目标 Pod 状态 → 自定义解释器保留完整 status → 控制面 Pod → ARC 读取 Ready/容器状态。无来源或状态滞后时应维持不可确认状态，不能人工制造 Ready。

#### 5.5.4.1 UC-09 Runner Ready 状态反馈——运行时序（US-06）

```mermaid
sequenceDiagram
    autonumber
    box rgb(231,249,239) 成员集群运行状态
        participant Pod as Runner Pod
        participant Kubelet as kubelet
    end
    box rgb(255,240,222) Karmada 状态聚合
        participant EC as Execution Controller
        participant RI as Lua ResourceInterpreter
        participant CP as 控制面 Pod
    end
    box rgb(232,243,255) ARC 控制面判定
        participant ARC as ARC Controller
    end

    Pod->>Kubelet: 容器启动并进入 Running / Ready
    Kubelet->>EC: 上报成员 Pod.status
    Note over Kubelet,EC: 状态包含 containerStatuses 的容器名称、state 和 conditions
    EC->>RI: 聚合成员状态（statusItems）
    RI->>RI: 执行 AggregateStatus
    RI->>CP: 写回完整的 desiredObj.status
    CP-->>ARC: 暴露容器名称和 Pod Ready Condition
    ARC->>CP: 读取聚合后的 Pod 状态
    CP-->>ARC: 返回完整的 Runner 状态
    ARC->>ARC: 正确识别就绪，避免缺字段导致超时误删
    Note over RI,ARC: 通过 ResourceInterpreterCustomization 声明 Lua 规则，无需修改 Karmada 源码
```

**图的架构含义**：展示该用例跨组件的主要消息路径与对象更新。当前图是设计交互，不是实测调用栈；真实上游控制器内部仍以目标版本官方文档为准。

### 5.5.5 用户接口设计

用户在 GitHub 查看 Job 状态；运维可按 Pod 名称/UID、成员和 Ready 条件排查。用户操作 UI：N/A。

### 5.5.6 实现接口设计

#### 5.5.6.1 接口设计

| 调用方 → 提供方 | 原生接口/对象 | 关键字段与输入输出 | 身份或前置条件 | 异常/幂等及诊断 | 依据/决策 |
|---|---|---|---|---|---|
| 成员 kubelet → Karmada | v1 Pod.status | phase、conditions、containerStatuses[name,state,ready]、启动/结束原因 | 成员资源读取身份 | 状态缺失/滞后须可检测，不凭空生成字段 | U:Kubernetes, KAR-3 |
| Karmada Interpreter → 控制面 Pod | config.karmada.io/v1alpha1 ResourceInterpreterCustomization（AggregateStatus） | target apiVersion=v1 kind=Pod；statusItems → desiredObj.status | 本系统维护 Lua 规则，不改上游源码 | 空/多个 status 来源需按单目标前提处理 | U:KAR-3; D:SP-06 |
| ARC → Pod.status | Kubernetes Pod get/watch | runner 容器名、Ready Conditions、状态/退出信息 | Controller 读取所需对象权限 | 缺字段保持未就绪并给出日志/事件 | U:ARC-1; D |

**开发交付边界**：只新增/修改本仓库负责的 RunnerSet 模板、PropagationPolicy、RBAC、身份适配、Lua/状态解释或资源参数；对官方 API 的精确字段类型、枚举和版本兼容限制，必须以所选版本的 CRD OpenAPI Schema 为准。上表不能替代完整 upstream schema。

### 5.5.9 DFX分析

**功能内 DFX**：状态聚合运行不得泄露 Secret Data；目标只保留必要 Pod.status 内容。性能与时效性需要以业务 SLO 定义上限，不把测试脚本的 180s 采样窗口当作生产恢复时限。

**可靠性 FMEA（设计阶段定性分析；严重度/发生概率/探测度的数值及 RPN 未经业务基准审批，暂不虚构）**

| FMEA 编号 | 失效模式 | 对业务/系统的影响 | 发现与定位 | 预防/恢复措施 | 责任主体 |
|---|---|---|---|---|---|
| F05-01 | status 解释器丢失容器名/Ready | ARC 误判未就绪 | 控制面与目标成员 Pod.status 字段比较 | 无损保留关键字段；单目标语义审查 | 状态适配 |
| F05-02 | 多个状态来源互相冲突 | 错误聚合或跨成员状态串扰 | Binding 目标与 status 来源不一致 | 拒绝无定义的选首个策略；新场景另定义合并规则 | 状态适配/Karmada |
| F05-03 | 状态上报延迟 | 临时 Runner 过早超时或诊断滞后 | Pod UID/RV、条件时间戳与日志 | 保持异步语义并设计告警，不捏造 SLO | Karmada/ARC |

**可服务性与安全检查**：关联 GitHub Job Run → RunnerSet → ER/Pod UID → Karmada Binding/Work → 成员集群，采集必要状态与错误分类，不记录主 GitHub Token、JIT Secret Data、Kubeconfig 和私钥。若此功能提供可配置的 SLO，明确指标定义、度量位置、业务提供的阈值及失败处理，而不把一次 PoC 运行值当作产品规格。

## 5.6 F-06 异构 NPU Runner 执行资源适配

### 5.6.1 功能概述

**关联需求**：SR-06-01；**用例**：UC-07 请求 NPU Runner 并在兼容节点执行、UC-13 NPU 资源不足或设备插件不可用；**功能域规格**：SP-02、SP-08。

通过服务级 Runner 规格和成员 Kubernetes 扩展资源，使需要 NPU 的 CI Job 仅进入与硬件/软件栈兼容的资源池。输入 NPU Runner 标签、资源名/数量/驱动约束，输出可调度的 NPU Runner Pod 或可观察的资源不足状态。

**边界/归属**：只维护本功能域的配置或适配；ARC/Karmada/Kubernetes 上游内部调谐逻辑不由本功能重写。

### 5.6.3 实现思路

成员 K8s Device Plugin 提供扩展资源（U），Karmada 依据成员资源与放置约束分发 Runner Pod（U），服务模板声明对应扩展资源和镜像/驱动约束（D）。不得编造统一 NPU 资源名或声称任何设备/Runtime 都兼容（P）。

### 5.6.4 设计思路

业务选择 NPU Runner 标签 → ARC 创建带扩展资源请求的 Runner Pod → Karmada 过滤合格成员 → 成员 kube-scheduler 选择符合资源的节点 → Device Plugin/kubelet 分配设备 → Runner 执行并上报 Job 结果。资源不足则保持等待/不可调度并暴露原因。

#### 5.6.4.1 UC-07 请求 NPU Runner 并在兼容节点执行——运行时序（US-08）

```mermaid
sequenceDiagram
    autonumber
    box rgb(232,243,255) 任务与 Runner 模板
        participant GH as GitHub Actions
        participant ARC as ARC Controller
    end
    box rgb(255,240,222) 联邦和成员调度
        participant K as Karmada Scheduler
        participant KS as 成员 kube-scheduler
    end
    box rgb(231,249,239) NPU 执行环境
        participant DP as NPU Device Plugin
        participant Pod as NPU Runner Pod
    end

    Note over GH,Pod: 设计路径；设备插件与扩展资源名称由目标硬件选型
    GH->>ARC: 提交 NPU Runner 标签对应的 Job 需求
    ARC->>K: 创建声明 NPU 资源请求的 Runner Pod
    Note over ARC,K: 需要先明确 Runner Pod 的 NPU 资源规格及集群兼容条件
    K->>KS: 将 Runner Pod 分发到候选成员集群
    KS->>KS: 依据资源请求、节点标签和可用节点进行调度
    KS->>DP: 使用节点上的设备资源分配能力
    DP-->>Pod: 提供已分配的 NPU 设备资源
    KS->>Pod: 在选定节点启动 Pod
    Pod->>Pod: 执行 NPU 计算或测试
    Pod-->>GH: 上报 Job 结果，退出后释放资源
    Note over K,Pod: 真实 NPU 资源名、设备插件及兼容性由目标硬件选型决定
```

**图的架构含义**：展示该用例跨组件的主要消息路径与对象更新。当前图是设计交互，不是实测调用栈；真实上游控制器内部仍以目标版本官方文档为准。

#### 5.6.4.2 UC-13 NPU 资源不足——异常运行时序

```mermaid
sequenceDiagram
    autonumber
    participant ARC as ARC RunnerSet
    participant K as Karmada
    participant Cluster as 成员 Kubernetes
    participant Plugin as Device Plugin
    participant Ops as 资源池管理员
    ARC->>K: 提交带扩展资源请求的 Runner Pod
    K->>Cluster: 选择候选成员
    Cluster->>Plugin: 检查节点 allocatable 与设备能力
    alt 设备资源不存在或不足
      Plugin-->>Cluster: 无合格设备
      Cluster-->>Ops: Pod Pending / FailedScheduling 可见
      Note over K,Ops: 不降级运行到无 NPU 的节点
    else 设备插件与资源就绪
      Cluster->>Plugin: 请求设备分配
      Plugin-->>Cluster: 分配设备
      Cluster-->>ARC: Runner Pod 状态回传
    end
```

### 5.6.5 用户接口设计

用户通过独立 NPU Runner 服务标签选择设备能力，不需要直接填成员集群名称；需要在服务规格文档中说明设备类型、镜像/驱动约束。没有独立 UI 时使用 GitHub workflow YAML 与平台服务目录。

### 5.6.6 实现接口设计

#### 5.6.6.1 接口设计

| 调用方 → 提供方 | 原生接口/对象 | 关键字段与输入输出 | 身份或前置条件 | 异常/幂等及诊断 | 依据/决策 |
|---|---|---|---|---|---|
| 用户 Workflow → NPU Scale Set | GitHub Actions runs-on | 服务标签表示清晰设备规格，不能与通用 CPU Runner 混用 | Repo/Org 授权 | 无匹配服务 → queued/错误明确 | U:ARC-1; D |
| Runner Pod → 成员 Kubernetes | v1 Pod.spec.containers[].resources | requests/limits[厂商扩展资源名]、nodeSelector/affinity/tolerations | Pod SA 最小权限 | 资源不足 Pending，不静默丢弃 NPU 要求 | U:K8S-3; D:SP-08 |
| Karmada → 成员候选集 | PropagationPolicy placement | 选择兼容 NPU 成员、集群标签与资源容量条件 | 平台策略管理权限 | 成员标签/资源不满足 → 不错误放置 | U:KAR-1; D |

**开发交付边界**：只新增/修改本仓库负责的 RunnerSet 模板、PropagationPolicy、RBAC、身份适配、Lua/状态解释或资源参数；对官方 API 的精确字段类型、枚举和版本兼容限制，必须以所选版本的 CRD OpenAPI Schema 为准。上表不能替代完整 upstream schema。

### 5.6.9 DFX分析

**功能内 DFX**：资源配额、并发、设备隔离与多租户权限必须由目标 NPU 产品和集群容量制度补充（P）；性能不可用一次执行结果推导，应以定义的 workloads/metrics 评价。

**可靠性 FMEA（设计阶段定性分析；严重度/发生概率/探测度的数值及 RPN 未经业务基准审批，暂不虚构）**

| FMEA 编号 | 失效模式 | 对业务/系统的影响 | 发现与定位 | 预防/恢复措施 | 责任主体 |
|---|---|---|---|---|---|
| F06-01 | 厂商资源名与插件实际发布不一致 | Runner 无法调度 | Node allocatable 与 Pod requests 对照 | 设备能力目录与服务模板同源维护 | 设备/平台 |
| F06-02 | 驱动/Runtime/固件版本不兼容 | Pod 启动失败或任务失败 | 节点事件、容器退出原因 | 版本兼容矩阵和分层镜像管理 | 设备/运维 |
| F06-03 | 设备已耗尽或成员不可达 | 排队/持续 Pending | Karmada 目标、K8s FailedScheduling | 容量告警与候选集过滤，不降级成 CPU | 调度/资源管理员 |

**可服务性与安全检查**：关联 GitHub Job Run → RunnerSet → ER/Pod UID → Karmada Binding/Work → 成员集群，采集必要状态与错误分类，不记录主 GitHub Token、JIT Secret Data、Kubeconfig 和私钥。若此功能提供可配置的 SLO，明确指标定义、度量位置、业务提供的阈值及失败处理，而不把一次 PoC 运行值当作产品规格。

## 5.7 功能追踪与开发接口清单

| SR | 功能 | 关联规格 | 运行视图 | 开发实现入口 |
|---|---|---|---|---|
| SR-01-01、SR-01-02 | F-01 | SP-01 | 5.1.4.1 | `04-autoscalingrunnerset.yaml`、GitHub Workflow |
| SR-02-01、SR-02-02 | F-02 | SP-01、SP-07 | 5.2.4.1 / 5.2.4.2 | 官方 ARC、RunnerSet、对象生命周期契约 |
| SR-03-01、SR-03-02 | F-03 | SP-02、SP-03、SP-04 | 5.3.4.1 / 5.3.4.2 / 5.3.4.3 | `02-propagation-policies.yaml`、关联资源 |
| SR-04-01 | F-04 | SP-04、SP-05 | 5.4.4.1 | `03-rbac.yaml`、`04-autoscalingrunnerset.yaml` Listener Template |
| SR-05-01 | F-05 | SP-06 | 5.5.4.1 | `01-karmada-interpreter.yaml` |
| SR-06-01 | F-06 | SP-02、SP-08 | 5.6.4.1 / 5.6.4.2 | Runner Pod 资源模板、成员设备插件和 Placement |

**统一资料来源及版本边界**：本文件 3.7 给出 ARC/GitHub Actions、Karmada、Kubernetes 官方文档索引；本仓库现有 Manifest 用于定位配置差异；任何仅有内部运行记录支持、而无上游接口规范支持的声明都应标明为集成设计假设。缺少 PoC 不构成功能设计缺口，但 **SR/接口/异常/DFX 未定义**属于设计缺口。

**实施前需选择的参数（P）**：GitHub 组织/仓库与权限、目标版本和镜像 digest、联邦控制面 API/DNS/CA、凭证发行及轮换、成员与资源标签、NPU 型号与扩展资源名、隔离与容量策略、性能/可用性 SLO。若有正式输入来源，则填写批准值；如果尚无，不得硬编码临时 Kind IP 或杜撰业务 SLA。
