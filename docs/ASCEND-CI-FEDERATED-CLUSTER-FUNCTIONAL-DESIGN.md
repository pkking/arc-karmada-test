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

## 2.2 本功能域的系统特性

本功能域只分析**联邦调度需要提供的能力**，以及 ARC 与 Karmada 集成后必须解决的问题。对 GitHub Actions、ARC、Karmada 和 Kubernetes 已提供的通用功能，本设计只说明使用关系，不将其全部重新定义为昇腾CI的需求。

| 特性 | 要解决的问题 | 系统对外呈现的能力 | 当前仓库的依据 | 实现状态边界 |
|---|---|---|---|---|
| **FT-01 统一 Runner 服务与资源共享** | CI 作业依赖指定执行集群，项目之间难以共享其他集群的空闲容量。 | 授权范围内的作业使用统一 Runner 服务名称，不必指定成员集群；Runner 按作业需求创建。 | `manifests/04-autoscalingrunnerset.yaml`；`.github/workflows/test-arc.yml` | **已配置统一入口**；Runner 创建由官方 ARC 实现。当前 GitHub 目标是一个仓库，不据此宣称多项目权限已经配置完成。 |
| **FT-02 平台可控的跨集群放置** | 多个独立 Scale Set 之间的作业分配由 GitHub 控制，平台不能设置成员集群的放置规则。 | 平台通过联邦策略控制 Runner Pod 可以在哪些成员执行，并能调整候选范围，而不修改工作流标签。 | `manifests/02-propagation-policies.yaml` | **已配置** member1/member2 候选集；按 NPU 型号、实时剩余容量或地域进行综合选择尚未实现。 |
| **FT-03 跨控制面的 Runner 正常执行** | Runner 跨集群运行时，Listener 身份、注册凭证和 Pod 状态可能与管理控制面不一致。 | Runner 能在被选成员获取启动条件、注册并执行；平台能获取可信的执行状态。 | `manifests/01-karmada-interpreter.yaml`、`02-propagation-policies.yaml`、`03-rbac.yaml`、`04-autoscalingrunnerset.yaml` | **已有对应配置**，能力由上游控制器与本仓库适配共同提供；Token 自动轮换及多来源状态冲突处理不在当前实现中。 |
| **FT-04 联邦临时资源的生命周期管理** | Runner 由集中管理面创建，却在成员执行，任务结束后需要处理两侧临时资源。 | Runner 数量随作业需求变化；作业结束后，其不再需要的临时资源能够按所属控制器的规则回收。 | `manifests/04-autoscalingrunnerset.yaml`、`scripts/verify-arc.py`、ARC/Karmada 官方生命周期 | **依赖上游能力**；仓库包含运行资源检查脚本，但没有单独的通用资源回收控制器。 |

**特性与实现功能的区别**：FT-01～FT-04 是系统特性，回答本方案解决什么问题；第 5 章的 F-01～F-05 是为实现这些特性而展开的技术功能。F-06（NPU 专项执行资源适配）属于设备选型后的扩展设计，**不属于当前核心需求**。保留 US-08 及 F-06 的技术资料，不能据此将 NPU 调度写为已实现能力。

## 2.3 需求来源与用例关系

本节从第 1.3 节的问题和第 3.2 节的架构选型出发提取需求。**代码用于核对现有能力，上游资料用于确认可复用机制**；二者不能代替正式业务方批准的原始需求。下列 IR、UC 和 SR 编号是本项目的设计追踪编号，不是外部需求系统已批准的编号。

| 需求意图 | 问题来源 | 主要用例及场景 | 对应的系统 SR | 特性及实现功能 |
|---|---|---|---|---|
| IR-01 跨成员共享 Runner 执行资源 | 项目与执行集群绑定，部分项目等待、部分集群空闲。 | UC-01 统一服务提交；UC-03 根据需求供给 Runner。 | SR-01-01、SR-02-01 | FT-01；F-01、F-02 |
| IR-02 控制 Runner 的执行成员 | GitHub 不提供多个独立 Scale Set 之间可配置的成员调度策略。 | UC-05 在合格候选成员中执行；UC-10 无可用成员。 | SR-03-01 | FT-02；F-03 |
| IR-03 保证跨控制面执行链路有效 | Listener、临时注册凭证与成员 Pod 状态需要跨集群正确传递。 | UC-06 启动依赖未就绪；UC-08 Listener 访问控制面；UC-09 状态反馈。 | SR-03-02、SR-04-01、SR-05-01 | FT-03；F-03、F-04、F-05 |
| IR-04 结束临时执行资源的联邦生命周期 | 管理面与成员集群分别持有动态对象。 | UC-04 作业结束回收；UC-12 状态或清理异常。 | SR-02-02 | FT-04；F-02、F-03、F-05 |

**用例视图（概要）：**

![昇腾CI 联邦集群功能域概要用例视图——业务角色与主要用例关系](images/federated-cluster-use-case-overview.svg)

> 图示的是业务角色可以使用的主要功能，不表示任务时序。其中“纳管 NPU 资源”等完整资源域场景不是本轮联邦调度核心 SR；它们保留为后续资源类型扩展。Runner Pod、JIT Secret 和状态聚合属于第 5 章的实现设计，不是用户直接操作的业务用例。

## 2.4 系统需求 SR

本节只规定 FT-01～FT-04 必须呈现的系统行为，以及**为使这些特性成立**需要满足的质量要求。每条功能 SR 都规定使用条件、对外可观察的结果和判断方法。ARC/Karmada 资源字段和控制器内部处理写在第 4、5 章，不在这里重复。

中文需求表达参考 [ASD-STE100](https://www.asd-ste100.org/about_STE.html) 的术语一致、句义明确、避免省略原则；该英语标准不构成中文文本的符合性认证。以下沿用现有 SR 编号，避免丢失已建立的详细设计关联。

### 2.4.1 与联邦调度直接相关的功能需求

| SR | 适用场景 | 系统应提供的行为 | 可观察的判定条件 | 来源与现状 |
|---|---|---|---|---|
| **SR-01-01** | CI 开发者通过已发布的 Runner 服务提交作业。 | 开发者应能使用同一个 Runner 服务名称提交作业，不需要选择成员集群。该服务应允许作业在其有权限使用的联邦资源池中执行。 | 工作流不包含成员集群信息；属于同一服务的作业能够在策略允许的成员集群执行。 | FT-01；已配置统一标签与单个 Scale Set。 |
| **SR-02-01** | 联邦 Runner 服务收到作业需求，且未超过服务容量上限。 | 系统应按作业需求提供临时 Runner。被创建的 Runner 应能够在平台选择的成员集群执行作业。 | Runner 数量能够根据任务需求变化；作业在所选成员运行，不要求每个成员独立部署 ARC 管理服务。 | FT-01、FT-04；本仓库声明容量范围，实际增减由官方 ARC 实现。 |
| **SR-03-01** | 同一 Runner 服务允许使用多个成员集群。 | 系统应依据平台配置的联邦放置策略选择执行成员。作业不得在策略禁止的成员集群执行；开发者不需要更改 Runner 服务名称来选择成员。 | 修改允许的成员范围，随后创建的 Runner 只在该范围内执行；不要求多个独立 Runner 逐轮平均分配。 | FT-02；当前策略已配置 member1/member2，复杂容量感知策略未实现。 |
| **SR-03-02** | Runner 已被分配到某个成员集群，但启动所需的临时注册信息尚未可用。 | 系统应确保 Runner 在选定成员具备所需注册信息后才进入可执行状态。如果注册信息不可用，系统不得将该 Runner 视为已经就绪。 | 正常情况下，Runner 可以在成员完成注册并执行作业；注册信息缺失时，Runner 保持未就绪或出现明确的启动错误。 | FT-03；当前策略使用 `propagateDeps`，具体传播和启动由上游组件实现。 |
| **SR-04-01** | ARC Listener 运行在成员集群，需要访问统一的管理控制面。 | 在有效连接和授权条件下，系统应允许 Listener 处理所属 Runner 服务的任务需求。连接或授权无效时，系统不得把处理失败误报为已成功处理。 | Listener 能在成员运行并更新对应 Runner 服务；访问失败时能够区分连接、认证与授权原因。 | FT-03；现有 Token、CA、RBAC 和 Listener 模板已配置，自动轮换未实现。 |
| **SR-05-01** | Runner 在成员集群中启动、进入就绪状态或发生执行异常。 | 系统应向管理控制面反馈 Runner 的实际就绪和运行状态，不得把尚未就绪的 Runner 判定为就绪。 | 管理控制面获取的状态与选定成员的 Pod 状态一致，并保留识别 Runner 所需的状态信息。 | FT-03；现有 Lua 解释器按单成员来源复制状态。 |
| **SR-02-02** | 由联邦 Runner 执行的作业已经结束。 | 系统应按临时 Runner 生命周期回收不再需要的执行资源。资源未能回收时，平台应保留可以识别该资源的状态或错误信息。 | 作业结束后，相关临时执行资源进入回收流程；异常残留能够通过对象记录定位。 | FT-04；资源回收依赖 ARC/Karmada，上游运行状态由仓库脚本检查。 |

**刻意不列为独立功能 SR 的内容**：没有匹配 Runner 服务时的 GitHub 原生行为（原 SR-01-02）仍属于 UC-02 的异常说明；NPU 专项资源调度（原 SR-06-01）需要实际设备规格与配置，作为第 5.6 节的扩展设计，不属于当前正式功能 SR。

### 2.4.2 与联邦调度直接相关的非功能需求

| SR | 属性 | 规定的系统要求 | 判定依据 | 与当前实现的关系 |
|---|---|---|---|---|
| **SR-NF-01** | 跨控制面访问安全 | 系统必须拒绝没有相应权限的 Listener 对联邦管理资源的操作。Listener 的有效访问不得依赖关闭 TLS 证书校验。 | 使用受限身份与无权限身份访问相同资源，检查 API 的授权和证书处理结果。 | 已有受限 RBAC 和 CA 配置；控制面 Controller 当前使用管理 kubeconfig，仍需单独收敛权限。 |
| **SR-NF-02** | 跨成员凭证隔离 | 平台不得仅因为成员执行 Runner 就向该成员提供长期 GitHub 管理凭证。临时执行凭证应仅供运行它的成员使用。 | 检查凭证的实际消费者和传播对象，确认非必要成员不获得管理凭证。 | **目标安全要求，当前未满足**：现有策略显式向两个成员传播 `arc-github-secret`。 |
| **SR-NF-03** | 成员故障边界 | 当成员集群不可用时，系统不能把该成员中尚未就绪的 Runner 作为可执行资源；运行中的 GitHub 作业不要求无损迁移。 | 核对不可用成员的 Runner 状态；不得因为成员故障而宣称运行中作业已在其他成员恢复。 | 目标可靠性边界；当前策略指定候选成员，尚无自动故障迁移实现依据。 |
| **SR-NF-04** | 跨集群问题定位 | 平台运维应能确定一次作业所对应的 Runner 和执行成员，并可查看资源传播、注册或状态反馈中能够获得的异常信息。 | 通过作业及资源记录定位执行成员，区分主要故障阶段。 | 仓库有运行检查脚本，但尚无统一的运维查询接口。 |
| **SR-NF-05** | 容量与排队性能 | 联邦 Runner 服务必须遵守已配置的 Runner 容量上限。平台应能分别观察作业排队与 Runner 启动耗时；未定义的性能改善比例不得作为已满足的需求。 | 核对 Runner 数量不超过配置上限，记录可获得的排队与启动阶段时间。 | 已配置 `minRunners: 0`、`maxRunners: 5`；量化时延与改善目标仍待业务提供。 |
| **SR-NF-06** | 资源使用与成本 | 无待执行任务且允许空闲 Runner 数量为零时，系统应能够回收临时 Runner，以减少空闲占用。执行成员无需分别部署完整的 ARC 管理控制器。 | 检查空闲 Runner 配置和回收路径；核对成员的部署职责。 | 配置和架构支持这一原则，实际节约金额或比例尚无正式指标。 |
| **SR-NF-08** | 放置策略可维护性 | 平台管理员应能够修改成员候选范围，而无需让 CI 开发者修改原有 Runner 服务名称。 | 修改和恢复联邦策略后，检查后续 Runner 的执行成员仍属于当前允许范围。 | 成员范围通过 Karmada Policy 配置；未提供自研策略管理 UI。 |

上述非功能 SR 均对应核心联邦能力。通用 Kubernetes 升级兼容、所有企业服务的统一可用性等级以及尚未纳入本方案的 NPU 设备专项要求，仍可在部署与 DFX 章节保留设计说明，**不另行定义为当前核心系统 SR**。

### 2.4.3 需要业务确定的指标

| 参数 | 计量对象 | 必须明确的业务输入 | 当前状态 |
|---|---|---|---|
| P-SR-01 | Runner 准备耗时 | 起止事件、统计窗口、目标分位数与时间阈值 | 未确定 |
| P-SR-02 | 作业排队时间 | 是否统计 GitHub 侧等待、样本范围、目标时间及统计分位数 | 未确定 |
| P-SR-03 | 成员不可用时的新任务可执行性 | 合格备用成员的条件、故障检测与恢复目标；不包含已运行 Job 的无损迁移 | 未确定 |
| P-SR-04 | 联邦资源池利用率与运行成本 | 业务负载、共享前基线、资源占用与成本计量方法、目标值 | 未确定 |

这些指标来自“减少项目资源隔离与可避免的任务等待”的目标。现有仓库没有提供能证明生产环境改善比例的资料，因此不能直接填写数值。**指标待确定不妨碍功能和责任边界的文档化，也不能把待定指标声明为已经量化验收。**

### 2.4.4 系统特性、需求与实现设计的对应关系

| 系统特性 | 正式功能 SR | 关联非功能 SR | 详细设计实现单元 |
|---|---|---|---|
| FT-01 统一 Runner 服务与资源共享 | SR-01-01、SR-02-01 | SR-NF-05、SR-NF-06 | F-01、F-02、F-03 |
| FT-02 平台可控的跨集群放置 | SR-03-01 | SR-NF-03、SR-NF-08 | F-03 |
| FT-03 跨控制面的 Runner 正常执行 | SR-03-02、SR-04-01、SR-05-01 | SR-NF-01、SR-NF-02、SR-NF-04 | F-03、F-04、F-05 |
| FT-04 联邦临时资源的生命周期管理 | SR-02-02 | SR-NF-04、SR-NF-06 | F-02、F-03、F-05 |

**需求来源边界**：本表以第 1.3 节的问题及第 3.2 节选定方案为依据。当前 Manifest 用于证明哪些集成配置已经存在；ARC/Karmada 的控制器语义来自上游。SR-NF-02 等尚未实现的目标约束必须在工程实施中单独处理，不能将需求写成已完成的实现，也不能将历史 PoC 结果作为上游正式规格。

## 2.5 不属于当前联邦调度核心需求的内容

- GitHub Actions 自身的 Job 匹配、排队、任务结果存储，不由本系统重新实现。原 SR-01-02 的异常场景在 F-01 中保留。
- 每个 ARC/Kubernetes 原生资源的创建方法及控制器内部算法属于第 4、5 章的实现设计，不构成独立的对外系统特性。
- NPU 设备插件、资源名称、型号兼容和设备分配属于后续资源类型扩展。原 SR-06-01 对应的 F-06、US-08 可保留为**扩展设计**，本轮不将其计入已实现功能或当前核心 SR。
- 平台未定义自动迁移正在执行的 GitHub Job，也未定义多个独立 Runner Pod 必须逐轮平均分配。
- 通用组件升级、企业级统一可用性等级、所有成本预算和监控平台产品选型属于更大范围的平台工程，不在本功能域新增独立 SR。
- 本轮只修订设计与需求来源，不修改运行 Manifest，不要求 PoC、Kind 或真实 GitHub Job 验证文档。


# 3 功能域总体方案

## 3.1 总体原则、设计模式与边界

1. **职责分离**：GitHub 负责队列，ARC 负责 Runner 期望量与短生命周期，Karmada 负责成员选择和对象传播，成员 Kubernetes 负责节点调度与设备资源分配。
2. **控制面与执行面分离**：Runner 管理请求汇入统一 API；Listener 和 Runner 可以部署于执行域，但其权限、连接地址和证书与目标 API 一致。
3. **最小权限与最小传播**：GitHub 主凭证以管理域为中心，JIT 凭证按具体引用传播至执行成员；Runner 默认不获取 Kubernetes 管控 API Token。
4. **最终一致与无重放假设**：跨组件 Pod/Secret/Binding 传播与清理都是异步流程；没有上游证明不得承诺原子传播、无损迁移或 Job exactly-once 重试。
5. **上游优先**：默认复用 ARC、Karmada、Kubernetes 的正式 API/控制器；本功能域仅增加集成策略、必要权限、状态解释及配置，不重复设计上游模块。

## 3.2 备选方案与最终选型

| 选型 | 方案结构 | 主要优点 | 主要限制 |
|---|---|---|---|
| A：每个成员集群独立部署 ARC | 每个成员集群部署自己的 ARC Controller 和 Runner Scale Set。多个集群可对外提供相同 Runner 服务名称，但同名 Scale Set 必须位于不同 Runner Group。 | 各集群可以独立管理 Runner 的创建和回收，控制器故障主要影响所在集群。 | **GitHub Actions 服务决定作业分配给哪个 Runner Scale Set，平台不能配置跨 Scale Set 的作业分配算法。** 当多个同名 Scale Set 同时在线时，GitHub 官方将这种分配方式描述为不可配置的 `assignment race`。因此，平台不能直接按成员集群的剩余容量、NPU 类型、地域或优先级制定统一的跨集群调度策略。 |
| B：ARC + Liqo | ARC 结合 Liqo 提供的跨集群资源和网络能力。 | 可以复用 Liqo 的跨集群网络及资源抽象能力。 | 必须独立设计跨集群网络、资源映射和控制面依赖。本仓库没有将该方案作为目标实现。 |
| **C：ARC + Karmada（选定）** | 平台通过一个统一 Runner Scale Set 接收 GitHub 的任务需求。ARC 管理临时 Runner，Karmada 根据 PropagationPolicy 将 Runner Pod 分发到符合条件的成员集群。 | **成员集群的选择由平台维护的联邦策略控制**。平台能够规定候选成员、集群属性和资源约束，不需要依赖 GitHub 在多个独立 Scale Set 之间选择执行集群。 | 平台必须设计跨控制面的身份、JIT Secret 传播、Pod 状态反馈及临时资源回收。Karmada 的策略也不自动保证多个独立 Runner Pod 按固定比例分配。 |

### 3.2.1 作业分配与成员集群调度的责任边界

为了避免把两个不同的调度阶段混为一谈，本设计作如下区分：

1. **GitHub Actions 负责作业与 Runner 服务匹配**。GitHub 根据工作流的 `runs-on`、Runner Group 和 Runner Scale Set 的注册信息决定向哪个 Scale Set 分配作业。采用方案 A 时，多个独立 Scale Set 之间的选择仍由 GitHub 服务完成，平台不能提供自定义的集群间作业分配算法。
2. **ARC 负责 Scale Set 内的 Runner 数量和生命周期**。ARC Listener 接收 GitHub 分配给当前 Scale Set 的作业消息及数量信息，并更新 `EphemeralRunnerSet` 的期望数量。ARC 的 `minRunners` 和 `maxRunners` 用于限制或预置该 Scale Set 的 Runner 数量，**不是跨多个 Scale Set 的集群调度策略**。
3. **Karmada 负责选定 Runner Pod 的执行成员**。采用方案 C 时，ARC 为统一 Runner Scale Set 创建 Runner Pod。Karmada 根据本系统配置的 `PropagationPolicy` 选择允许执行该 Pod 的成员集群。成员 Kubernetes 继续负责该集群内部的节点调度。平台能控制的是 **Runner Pod 的成员放置策略**，而不是 GitHub Actions 内部的 Job 分配算法。

**选型结论（D）**：本功能域需要根据成员集群的资源条件控制 Runner 的执行位置。因此选择方案 C，将跨集群放置决策放在平台可管理的 Karmada 策略中。方案 A 虽然可以在多个集群独立提供 Runner 服务，但当多个 Scale Set 都有资格接收同一种任务时，平台不能直接控制 GitHub 将具体作业分配给哪一个 Scale Set。该差异是本次选型的主要依据，而不仅是方案 A 缺少集中管理。

**上游依据**：

- [GitHub 官方：部署 Runner Scale Sets — High availability and automatic failover](https://docs.github.com/en/actions/how-tos/manage-runners/use-actions-runner-controller/deploy-runner-scale-sets#high-availability-and-automatic-failover)：说明多个独立集群可以在不同 Runner Group 使用同名 Scale Set；多个 Scale Set 同时在线时，分配可能由竞争决定，且不能配置作业分配算法。
- [GitHub 官方：ARC 工作原理](https://docs.github.com/en/actions/concepts/runners/actions-runner-controller)：说明 GitHub Actions 服务进行作业匹配，ARC Listener 接收 `Job Available` 消息后按需求调整 Runner 数量。
- [ARC 源码：`cmd/ghalistener/scaler/scaler.go`](https://github.com/actions/actions-runner-controller/blob/master/cmd/ghalistener/scaler/scaler.go)：`Scale` 处理收到的作业消息与 `TotalAssignedJobs`，后续更新当前 `EphemeralRunnerSet` 的目标 Runner 数量。这里没有跨独立 Scale Set 的成员选择算法。
- [Karmada 官方：PropagationPolicy](https://karmada.io/docs/next/userguide/scheduling/propagation-policy/)：说明通过传播策略选择成员集群。本方案根据这些机制制定跨集群 Runner Pod 放置规则，**不把 ARC 与 Karmada 的组合描述成上游已认证的整体方案**。

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

> 本章规定多个功能共同使用的资源、配置及安全规则。第 5 章说明每个功能如何使用这些规则，不重复描述 ARC、Karmada 和 Kubernetes 的内部实现。**U** 表示上游文档支持的能力；**D** 表示本功能域确定的集成规则；**P** 表示部署时需要提供的参数。“当前配置”指本仓库已有的 Manifest，“目标设计”指开发必须满足的要求。两者不一致时，开发人员必须明确记录并处理差异。

## 4.1 功能域规格变更

| 规格编号 | 当前配置 | 目标设计要求 | 负责方 |
|---|---|---|---|
| SP-01 Runner 服务命名 | 示例的 Scale Set 名称为 `karmada-runner`，`minRunners` 为 0，`maxRunners` 为 5。 | 平台必须保持 GitHub 工作流标签与 Runner 服务名称一致。Runner 数量的上下限由业务容量参数确定。 | 平台配置维护者 |
| SP-02 候选成员策略 | 现有策略包含 member1、member2，并使用 `Divided` 与 `Weighted`。 | 平台必须规定每个 Runner Pod 的合格成员范围。一个临时 Runner Pod 只在一个成员执行；该策略不保证多个 Pod 每轮平均分配。 | 联邦策略维护者 |
| SP-03 Runner Pod 选择范围 | 现有策略匹配 `arc-runners` 命名空间的全部 Pod。 | 平台必须只选择 ARC 管理的 Runner Pod。开发人员须按目标 ARC 版本的对象标签定义选择条件，并检查其他 Pod 不会被选中。 | 联邦策略维护者 |
| SP-04 GitHub 主凭证传播 | 现有策略将 `arc-github-secret` 同步到两个成员集群。 | 平台必须按实际凭证使用者确定传播范围。临时 Runner 不应因为需要跨集群运行而自动获得 GitHub 管理凭证。 | 身份管理者 |
| SP-05 Listener API 身份 | 示例通过单独的 Token 和 CA 证书访问固定 API 地址，Token 由脚本签发。 | 平台必须提供可配置的 API 地址、可信 CA、最小权限和凭证更新规则。必须区分网络、证书、身份认证和权限错误。 | 身份管理与部署维护者 |
| SP-06 Runner Pod 状态 | 当前 Lua 解释器选取第一个非空 `status`。 | 状态解释必须保留 ARC 所需的 Pod 字段。该规则只适用于一个 Runner Pod 在一个成员运行且状态来源没有冲突的情况。 | 状态适配维护者 |
| SP-07 临时资源生命周期 | ARC 和 Karmada 分别处理临时对象的删除。 | 平台必须能够关联同一任务的 Runner、Pod、JIT Secret 和联邦对象。删除失败时必须保留原因，不能任意清空未知 Finalizer。 | 控制器维护者和运维人员 |
| SP-08 NPU 资源规格 | 当前示例没有固定 NPU 资源名称及设备插件。 | 部署方必须从目标设备插件取得扩展资源名称与分配规则。平台据此配置成员筛选和 Runner Pod 资源请求。 | 设备与资源池维护者 |
| SP-09 安全与可观测性 | 现有资料通过 GitHub Run、Pod、Work 和日志进行局部诊断。 | 平台必须保存任务和联邦资源之间可追踪的标识，记录错误类型，禁止将凭证值写入日志。 | 平台运维与安全管理员 |

**当前配置与目标的差异**：SP-03、SP-04、SP-05 需要改变现有集成配置。文档明确的是开发目标，不表示这些变化已经实施。设计完成不需要 PoC，但实施人员必须逐项处理目标规格与现有 Manifest 的差异。

## 4.2 SP-01 Runner 服务与任务入口规格设计

### 4.2.1 实现思路

GitHub Actions 通过工作流的 `runs-on` 字段匹配 Runner 服务。平台在 `AutoscalingRunnerSet.spec.runnerScaleSetName` 中配置该服务的名称。ARC 负责创建和删除 Runner（U）。本功能域负责维护工作流标签、Runner 服务、Runner 模板和候选成员之间的对应关系（D）。成员集群名称不应出现在一般业务工作流中。若两个资源池的硬件或权限不能互换，平台应分别定义 Runner 服务。

### 4.2.2 实现设计

| 规范字段 | 类型/位置 | 约束 |
|---|---|---|
| 服务标识 | GitHub workflow `runs-on`、`AutoscalingRunnerSet.spec.runnerScaleSetName` | 同一个 Runner 服务在工作流 `runs-on` 与 `spec.runnerScaleSetName` 中必须使用相同名称。`karmada-runner` 是参考名称。 |
| GitHub 目标 | `spec.githubConfigUrl` | 平台管理员必须为 `spec.githubConfigUrl` 设置已授权的仓库或组织地址。Runner 不得注册到未授权的仓库或组织。 |
| 主凭证 | `spec.githubConfigSecret` | `spec.githubConfigSecret` 引用的凭证必须由可信管理域提供，并遵守 SP-04 的分发限制。 |
| 弹性区间 | `spec.minRunners/maxRunners` | `spec.minRunners` 与 `spec.maxRunners` 必须满足 `0 ≤ minRunners ≤ maxRunners`。具体数值由部署配置确定。 |
| Runner 镜像与 Pod 模板 | `spec.template` | `spec.template` 必须声明 Runner 镜像、资源请求、ServiceAccount 及必要的容器安全配置。 |
| 版本约束 | ARC CRD/Controller/Listener/RunnerSet label | 部署方必须核对 ARC Controller、CRD 和 Runner 资源的版本兼容性。现有 0.15.0 只是配置示例。 |

如果工作流标签没有对应的 Runner 服务，平台不得更改该标签或把作业发送给其他服务。GitHub Actions 根据自己的规则显示排队状态。当注册权限不足或 Runner 已达到容量上限时，平台必须保留对应的错误信息或容量状态，不得通过更换服务标签绕过隔离要求。

## 4.3 SP-02/SP-03 联邦调度与依赖传播规格设计

### 4.3.1 实现思路

Karmada 的 `PropagationPolicy` 规定哪些对象需要传播，以及允许向哪些成员集群传播（U）。本功能域使用该策略传播 Runner Pod，不另行实现 GitHub 作业调度器（D）。每个临时 Runner Pod 只在一个成员集群运行。成员 Kubernetes 负责选择运行节点。Karmada 使用 `propagateDeps` 处理 Pod 引用的 JIT Secret 等依赖对象。Pod 与 Secret 可以先后到达；成员 Kubernetes 不能在必需的 Secret 尚未可用时完成相应的容器启动。

### 4.3.2 实现设计

| 配置项 | 规定的行为 | 前置条件与异常处理 |
|---|---|---|
| `PropagationPolicy.metadata.namespace` | 策略必须位于待传播 Runner Pod 所属的命名空间。平台必须检查该范围不会包括其他租户的 Pod。 | 当前使用 `arc-runners`。 |
| `resourceSelectors` | 策略必须只选择目标 Runner 服务管理的 Pod，所用标签必须符合选定 ARC 版本的对象定义。 | 除检查目标 Pod 被选中，还必须检查其他 Pod 不被选中；当前全命名空间匹配属于待修改配置。 |
| `placement.clusterAffinity` | 平台必须指定可以接收 Runner Pod 的成员集群，并根据设备类型、节点架构及隔离条件确定候选集。 | 当前示例为 member1 和 member2，不能把它们当成通用常量。 |
| `replicaScheduling` | 使用 `Divided` 或 `Weighted` 时，必须遵循所选 Karmada 版本的接口语义。 | 多个独立的单 Pod 任务不保证逐轮平均分配。 |
| `propagateDeps` | Karmada 必须将 Runner Pod 引用的 JIT Secret 等依赖资源传播到该 Pod 所在的成员。 | 依赖到达顺序不保证是原子事务；Secret 缺失时保留 Pod 挂载或启动异常状态。 |
| 主凭证 | 平台不得仅因为传播 Runner Pod 就把 GitHub 管理凭证分发到所有成员。 | 只有明确存在成员侧消费者并已授权时，才允许额外分发相应凭证。 |
| 执行态 | 成员 Kubernetes 负责维护 Pod 的 `Pending`、`FailedMount`、`ErrImagePull` 等状态或事件。 | 平台必须显示或保存异常原因，不得因此重复提交同一个 GitHub 作业。 |

如果没有符合要求的成员集群，Runner Pod 必须保持未就绪或不可调度状态，并保留调度失败原因。成员失去连接时，平台必须区别尚未运行的 Runner Pod 和正在执行的 GitHub Job。Karmada 的资源重新分发不等于 GitHub Job 可以无损迁移或安全重复执行。传播策略发生资源冲突时，平台必须遵守 Karmada 的冲突处理机制，不得覆盖不属于本系统管理的资源。

## 4.4 SP-04/SP-05 身份、凭证与跨控制面访问规格设计

### 4.4.1 实现思路

ARC Controller、ARC Listener 和临时 Runner 必须使用各自需要的访问身份。Controller 负责管理 Runner 对象并使用 GitHub 管理凭证。Listener 使用独立身份访问 Karmada API，只获得处理其 Runner 服务所需的权限。临时 Runner 使用 ARC 创建的 JIT 配置注册到 GitHub，不应获取长期 GitHub 管理凭证。本功能域采用 Kubernetes ServiceAccount、TokenRequest 和 RBAC 的身份与授权机制（U）。令牌的签发、有效期、更新与审计由平台身份管理者负责（D）。

### 4.4.2 实现设计

| 访问主体和目标 | 认证方式 | 规定的权限与安全限制 |
|---|---|---|
| Controller → Karmada API | Controller 必须通过可信 CA 验证 Karmada API，并使用专门的 Kubernetes API 凭证。 | 平台必须按 ARC 管理资源所需的 API 操作授予权限，不得把管理员级 kubeconfig 作为生产目标权限。 |
| Listener → Karmada API | Listener 必须使用单独的 ServiceAccount 凭证和可验证的 HTTPS 地址。 | 权限应限制为指定 Runner 服务需要的 `EphemeralRunnerSet` 与 `EphemeralRunner` 操作；当前 [RBAC Manifest](../manifests/03-rbac.yaml) 是配置示例。 |
| Runner Pod → GitHub Actions | Runner 使用 ARC 创建的 JIT 注册配置与 GitHub 通信。 | 不得使用管理端的长期 GitHub 凭证；Runner 模板使用 `automountServiceAccountToken: false` 避免自动挂载 Kubernetes API 令牌。 |
| Karmada → 成员 API | Karmada 使用联邦成员注册时配置的访问身份。 | 成员 API 凭证应限制在传播资源和读取必要状态的操作范围内，由平台身份管理者维护。 |

**部署方必须提供的认证参数（P）**：API 地址与端口、服务器证书名称、可信 CA、令牌的使用范围和有效期、凭证颁发者以及更新方法。平台必须规定 Listener 如何加载更新后的令牌或证书。仅修改 Kubernetes Secret 不能证明 Listener 一定会自动使用新凭证。

**异常处理**：地址无法解析或 TCP 连接失败时，记录网络错误；服务器证书链或证书名称校验失败时，记录 TLS 错误；API 返回 HTTP 401 时，记录身份认证失败；API 返回 HTTP 403 时，记录权限不足。平台必须保留错误类型以供身份或网络管理员定位，不能关闭 TLS 校验，也不能通过临时授予 `cluster-admin` 绕过故障。日志不得包含令牌、私钥或 Secret 数据。

## 4.5 SP-06/SP-07 状态与生命周期规格设计

### 4.5.1 实现思路

成员 Kubernetes 负责生成 Runner Pod 的实际状态。Karmada 将该状态提供给统一控制面（U），ARC 根据其管理的 Runner 对象和 Pod 状态执行原有生命周期处理。本功能域通过 `ResourceInterpreterCustomization` 规定单目标 Runner Pod 的状态保留规则（D），不改变上游 ARC 的状态判断逻辑。动态对象仍由相应控制器根据 OwnerReference 与 Finalizer 管理。

### 4.5.2 实现设计

**下面的阶段名称用于描述设计流程，不是 Kubernetes Pod Phase 或 ARC 的 API 枚举值。**

| 逻辑阶段 | 进入条件 | 规定的行为 |
|---|---|---|
| 任务需求已接收（Requested） | Listener 接收到 Runner 服务的任务数量需求。 | ARC 按 Runner Scale Set 管理期望 Runner 数量。Karmada 不负责 GitHub 任务排队。 |
| 执行成员已选定（Scheduled） | Karmada 为 Runner Pod 选择一个成员集群。 | Karmada 分发资源；没有合格成员时不得把 Pod 视为已调度。 |
| Pod 尚未就绪（Pending） | 成员 Kubernetes 尚未完成调度、镜像获取或必需 Secret 挂载。 | 平台保留 Pod 事件和未就绪原因，不因此重复提交 GitHub Job。 |
| Pod 已就绪（Ready） | 成员 Pod 状态满足 ARC 需要的容器状态及 Ready 条件。 | Karmada 必须保留并同步这些字段。Pod Ready 不等于 GitHub Job 成功。 |
| 作业执行中（Executing） | Runner 已注册并开始执行 GitHub 分配的 Job。 | GitHub Actions 管理 Job 的执行结果；Pod 状态用于 Runner 运行诊断。 |
| 作业结束（Completed / Failed） | ARC 和 GitHub 按各自接口识别任务结束状态。 | ARC 开始相应的临时资源回收流程，平台保留失败原因。 |
| 资源删除中（Deleting） | ARC 或 Karmada 开始删除关联的临时对象。 | 各控制器处理其拥有的资源；删除失败时保留对象标识、Finalizer 和错误原因。 |

**Pod 状态解释要求**：对只在一个成员运行的 Runner Pod，状态解释器必须保留 `status.phase`、`status.conditions`、`status.containerStatuses[].name` 及 ARC 读取的容器运行字段。如果成员没有返回状态，不能自行生成 Ready 条件。当前 Lua 使用“第一个非空 `status`”的规则，只适用于单目标且没有状态冲突的场景。新增跨成员迁移或多副本场景前，必须另行定义状态来源选择及冲突解决规则。

**删除要求**：平台必须能够关联一次任务对应的临时 Runner、Runner Pod、JIT Secret 和 Karmada Binding/Work。各控制器分别完成删除，不要求所有对象同时消失。Finalizer 阻止删除时，运维人员必须先确认该 Finalizer 的所属控制器和未完成的清理操作，不能随意清空它。

## 4.6 SP-08 NPU 资源规格设计

### 4.6.1 实现思路

Kubernetes Device Plugin 负责向成员 Kubernetes 发布扩展设备资源（U）。本功能域必须依据目标 Runner 服务的设备需求，设置成员候选范围和 Runner Pod 的扩展资源请求（D）。Karmada 负责选择成员，成员 Kubernetes 负责选择节点；本功能域不重新实现 NPU 设备分配。具体硬件架构、操作系统、驱动、固件、设备插件及容器运行时必须依据目标 NPU 产品资料确定（P）。

### 4.6.2 实现设计

| 配置项 | 参数来源 | 规定的行为与限制 |
|---|---|---|
| NPU 扩展资源名 | 目标设备插件发布的 Kubernetes 资源定义。 | Runner Pod 必须使用插件实际发布的扩展资源名称。不得假定所有设备都使用 `npu`。 |
| 设备份额与数量 | 设备插件定义的资源单位与可共享规则。 | 平台必须按插件支持的分配单位设置请求数量。没有厂商资料时不得承诺共享或超额分配。 |
| 成员/节点标签 | 资源池配置、成员标签及节点标签。 | Karmada 应选合格成员，成员调度器再按资源请求和节点约束选节点。两层约束必须一致。 |
| 镜像/驱动/Runtime | 设备驱动、固件和容器运行时的兼容资料。 | 部署方必须核对 Runner 镜像与目标设备的兼容性；不能因为安装了设备插件就推定全部软件栈可用。 |
| Runner Scale Set | 服务目录及 GitHub 工作流标签。 | 硬件能力不能互换时，平台必须配置不同 Runner 服务，不能默默把 NPU Job 交给普通 CPU Runner。 |

没有符合条件的设备时，Runner Pod 必须保留不可调度状态及原因。插件未就绪或节点扩展资源消失时，不得删除 NPU 资源请求，也不得改用不具备 NPU 的节点执行任务。资源分配失败由成员 Kubernetes、设备插件和设备运维人员按各自职责定位。设备型号、资源名称和性能指标属于部署选型参数（P）。

## 4.7 SP-09 跨功能质量与可服务性规格

| 质量属性 | 规定的行为 | 需要确认的部署参数 |
|---|---|---|
| 安全 | Controller、Listener 和 Runner 必须使用彼此独立且满足最小权限原则的身份。平台只下发执行所需的 Secret，并校验 TLS 证书。 | Token 有效期、凭证系统和租户隔离规则。 |
| 可靠性 | 系统必须区分控制器可以重试的资源操作与需要管理员修复的证书、权限、网络错误。不得仅凭 Runner Pod 异常就重新提交同一 GitHub Job。 | 故障恢复目标、告警规则与人工处理责任。 |
| 可用性 | 平台必须明确 GitHub、Karmada 控制面、成员集群和网络连接之间的故障边界。一个成员可用不代表整个 GitHub Runner 服务可用。 | 目标部署拓扑、冗余级别和服务可用性目标。 |
| 性能与并发 | 平台应分别记录 Job 排队、Runner 数量调整、Pod 就绪和 Runner 注册的时间。不能用一次分配结果推定长期负载均衡。 | 服务容量、排队时间与每阶段延迟要求。 |
| 可服务性 | 平台必须能够使用 GitHub Run、Runner Scale Set、临时 Runner、Pod UID、Binding/Work 和成员集群定位同一次任务的处理状态。 | 日志保留期限、审计要求及观测平台。 |
| 升级与兼容 | 部署方必须记录 ARC CRD、Controller、Listener、Runner 镜像及 Karmada API 的版本，并按目标版本的接口检查兼容性。 | 组件版本、镜像摘要和升级回滚窗口。 |

第 5 章 F-01～F-06 必须遵守 SP-01～SP-09。每项 FMEA 应明确失效原因、影响、检测方式、恢复方法和责任方。如果业务尚未提供性能目标、设备型号或安全等级，文档必须标记为待确定参数，不得填写无依据的数值。

# 5 功能实现设计

> 本章按**功能单元**组织，每个功能汇总：功能概述、技术取舍、用例运行时序、用户接口、实现接口及 FMEA/DFX。其共同规格只引用第 4 章的 SP 编号，不重复堆叠 YAML 细节。对已有 Kubernetes/ARC/Karmada CRD，以其 OpenAPI schema 和字段契约为准；**没有新增 REST 服务，因此不虚构 Swagger Endpoint**。如实际新增 REST API，必须另补 OpenAPI 3 规范。
>
> 第 5 章按照功能组织 US-01～US-08 的运行时序图，并补充成员不可用、NPU 资源不足的异常场景。图中的跨组件交互是系统设计，不是实测调用日志；上游组件内部行为以选定版本的正式文档为准。
>
> FMEA 采用定性方法，逐项说明失效原因、业务影响、检测方法、处理要求和责任方。严重度、发生频率、检测难度及风险优先数的评价标准尚未由业务批准，因此不填写数值。

## 5.1 F-01 统一 Runner 服务入口

### 5.1.1 功能概述

**关联的正式系统需求**：SR-01-01；**用例**：UC-01 通过统一标签提交工作流、UC-02 无匹配或无可用 Runner（异常处理，不单列 SR）；**功能域规格**：SP-01。

F-01 为开发者提供稳定的 Runner 服务名称。开发者在 GitHub Actions 工作流的 `runs-on` 字段中填写该名称，不需要知道执行集群的名称或地址。平台负责配置 Runner 服务名称、GitHub 目标和 Runner 模板。

**职责边界**：本功能负责管理 Runner 服务名称与 GitHub 目标的对应关系。成员选择由 F-03 负责；Kubernetes 节点调度不属于本功能。

### 5.1.3 实现思路

平台必须在 `AutoscalingRunnerSet.spec.runnerScaleSetName` 中声明 Runner 服务名称，并使该名称与业务工作流的 `runs-on` 值一致。GitHub Actions 和 ARC 负责匹配任务、创建 Runner 及处理注册。本功能不开发独立的 GitHub 任务队列。如果两类 Runner 的硬件能力或授权范围不同，平台必须使用能够明确区分它们的服务名称。

### 5.1.4 设计思路

开发者提交工作流后，GitHub Actions 根据 `runs-on` 匹配 Runner 服务。ARC Listener 获取该服务的任务需求，ARC Controller 管理所需 Runner。F-03 决定 Runner Pod 的成员集群。如果没有匹配的服务，平台不得更改开发者提交的标签，也不得把任务发送给另一种 Runner 服务。

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



### 5.1.5 用户接口设计

用户接口是 GitHub Actions 工作流中的 `jobs.<job>.runs-on` 字段。平台发布的服务名称必须与 `AutoscalingRunnerSet.spec.runnerScaleSetName` 一致。例如，两个字段均使用 `karmada-runner`。本功能不提供额外的网页操作界面，运维人员使用现有 GitHub 和配置管理界面。

### 5.1.6 实现接口设计

#### 5.1.6.1 接口设计

| 交互双方 | 原生接口或资源 | 输入、操作与输出 | 权限与前置条件 | 异常处理与限制 | 依据及规格 |
|---|---|---|---|---|---|
| 工作流使用者与 GitHub Actions | GitHub Actions Workflow：`jobs.<job>.runs-on` | 开发者必须填写已发布的 Runner 服务名称。GitHub 根据该名称匹配 Runner。 | 开发者必须具有目标仓库工作流的提交权限。 | 名称未匹配时，GitHub 按其队列机制处理；平台不得改写标签。 | ARC-1；SP-01 |
| 平台配置与 ARC Controller | `AutoscalingRunnerSet`：`actions.github.com/v1alpha1` | 管理员必须配置服务名称、GitHub 目标、凭证引用、Runner 数量范围和 Pod 模板。 | Controller 必须具备所需的 Kubernetes API 与 GitHub 目标权限。 | API 拒绝创建或更新时，必须保留资源名称和错误原因；不能自行扩大权限。 | ARC-2；SP-01 |

**开发交付边界**：开发人员应维护 Runner Scale Set 模板与服务名称配置。不新增用于代替 GitHub Job Queue 的接口。 Kubernetes 原生对象使用目标版本的 Kubernetes OpenAPI 定义；ARC 和 Karmada 自定义资源使用相应版本的 CRD Schema。

### 5.1.9 DFX分析

**功能安全与性能约束**：Runner 的最大数量受 `maxRunners` 限制。平台必须防止 Runner 在未授权的 GitHub 仓库或组织中注册。具体并发容量和排队时间由业务方定义。

**可靠性故障分析（FMEA）**

| 编号 | 失效原因 | 业务或系统影响 | 检测方法 | 处理与恢复要求 | 负责方 |
|---|---|---|---|---|---|
| F01-01 | 工作流标签和 Runner 服务名称不一致。 | GitHub Job 不能匹配目标 Runner。 | 对照 `runs-on` 和 `runnerScaleSetName`，检查 GitHub Job 的排队信息。 | 平台管理员应修正不一致的服务配置，并检查修改影响。 | 平台维护者 |
| F01-02 | GitHub 管理凭证失效。 | Runner 无法正常向目标 GitHub 仓库注册。 | 检查 Controller 或 Listener 的认证错误和凭证引用。 | 身份管理者更新凭证；不得通过更改业务标签来规避认证失败。 | 身份管理员、ARC 运维 |

**运行诊断与敏感信息处理**：运维人员应先比较 `runs-on`、Runner Scale Set 名称和 GitHub 配置，再分析身份认证错误。审计不得记录 GitHub 管理凭证。

## 5.2 F-02 Runner 弹性供给及临时资源回收

### 5.2.1 功能概述

**关联需求**：SR-02-01、SR-02-02；**用例**：UC-03 Job 需求驱动 Runner 创建、UC-04 Job 结束回收 Runner 与凭证；**功能域规格**：SP-01、SP-06、SP-07。

F-02 根据 GitHub Actions 的任务需求管理临时 Runner。ARC 创建 Runner 及其 Pod 和临时注册配置。作业结束后，ARC 与 Karmada 分别回收各自管理的临时对象。平台需要能够通过对象标识将一次任务与对应资源关联。

**职责边界**：本功能负责 Runner 需求与临时资源生命周期之间的集成。Runner 注册协议及 ARC 内部控制器仍由上游维护。

### 5.2.3 实现思路

平台复用 ARC 的 `AutoscalingRunnerSet`、`EphemeralRunnerSet` 与 `EphemeralRunner` 生命周期。Karmada 负责传播 Runner Pod 和依赖对象，并在对象删除后处理成员资源。开发人员不得编写第二个与 ARC 争夺 Runner 所有权的控制器。应使用 Kubernetes 对象 UID 与原生所有权信息关联临时资源，不能依赖自动生成名称的格式。

### 5.2.4 设计思路

ARC Listener 根据任务需求更新 `EphemeralRunnerSet`，ARC Controller 创建临时 Runner 相关对象。成员 Kubernetes 启动 Runner Pod，Runner 向 GitHub 注册并执行作业。作业完成后，ARC 按原生生命周期回收 Runner，Karmada 异步处理传播对象。不能假定所有临时对象在同一时刻删除，也不能根据 Pod 失败就自行重复派发作业。

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



### 5.2.5 用户接口设计

开发者在 GitHub Actions 中查看作业的排队、执行与完成状态，不需要直接创建 Runner Pod 或 JIT Secret。运维人员使用 GitHub Run、临时 Runner、Pod 和 Work 的标识定位异常。本功能不新增专用网页界面。

### 5.2.6 实现接口设计

#### 5.2.6.1 接口设计

| 交互双方 | 原生接口或资源 | 输入、操作与输出 | 权限与前置条件 | 异常处理与限制 | 依据及规格 |
|---|---|---|---|---|---|
| ARC Listener 与 EphemeralRunnerSet | Kubernetes API：`EphemeralRunnerSet` 更新操作 | Listener 根据 GitHub 返回的任务需求更新指定 Runner 服务的期望数量。 | Listener 身份必须具有指定 Runner 服务所需的更新权限。 | API 返回资源版本冲突或拒绝访问时，必须保留失败原因，不得更新其他 Runner 服务。 | ARC-1；SP-01 |
| ARC Controller 与 Karmada API | `EphemeralRunner`、`Pod` 和 JIT Secret | ARC 创建临时 Runner 对象、Runner Pod 和注册所需的临时 Secret。 | Controller 必须有权管理所在命名空间的对应资源。 | 对象创建失败时记录资源类型和 API 错误，不得报告不存在的 Runner 已注册。 | ARC-1；SP-07 |
| ARC/Karmada 与成员 Kubernetes | Runner Pod、JIT Secret、`ResourceBinding`、`Work` | ARC 按官方生命周期删除临时 Runner，Karmada 调谐被传播对象的删除。 | 每个控制器只能依据其资源所有权处理相关对象。 | 删除未完成时保留对象 UID、Finalizer 和错误，不得清除未知 Finalizer。 | ARC-1、KAR-1；SP-07 |

**开发交付边界**：开发人员维护 Runner Scale Set 配置、资源关联和必要权限；动态 Runner 的创建与回收由 ARC 的官方控制器完成。 Kubernetes 原生对象使用目标版本的 Kubernetes OpenAPI 定义；ARC 和 Karmada 自定义资源使用相应版本的 CRD Schema。

### 5.2.9 DFX分析

**功能安全与性能约束**：平台不能超过 Runner 服务规定的容量上限。作业失败后，未经确认的重复执行可能产生副作用，因此不得仅因 Runner Pod 消失而重新提交作业。JIT Secret 的数据不得写入日志。

**可靠性故障分析（FMEA）**

| 编号 | 失效原因 | 业务或系统影响 | 检测方法 | 处理与恢复要求 | 负责方 |
|---|---|---|---|---|---|
| F02-01 | ARC 对任务需求的响应延迟。 | GitHub Job 需要等待 Runner。 | 比较 Runner 的期望数量与实际数量，并检查 ARC Controller 事件。 | ARC 运维人员应检查控制器状态和资源容量；平台按业务阈值发出告警。 | ARC 运维 |
| F02-02 | Runner 注册失败或 Job 执行中断。 | Job 不能完成，错误重试可能导致重复执行。 | 检查 GitHub Run、临时 Runner 状态和 Pod 事件。 | 应按 GitHub 与 ARC 的官方机制处理；没有明确去重依据时不得再次派发。 | GitHub、ARC 运维 |
| F02-03 | 临时资源的 Finalizer 阻止删除。 | 临时 Pod 或 Secret 仍占用资源。 | 按对象 UID 检查 ER、Pod、Binding、Work 和 Secret 的删除状态。 | 由对象所属控制器完成清理；需要人工操作时先确认资源所有者。 | ARC、Karmada 运维 |

**运行诊断与敏感信息处理**：使用 GitHub Run、临时 Runner、Runner Pod UID、JIT Secret 和 Binding/Work 追踪同一次任务。Finalizer 阻止删除时，必须先确认设置它的控制器。

## 5.3 F-03 Runner 联邦成员调度与依赖传播

### 5.3.1 功能概述

**关联需求**：SR-03-01、SR-03-02；**用例**：UC-05 在合格成员放置 Runner、UC-06 JIT Secret 晚到后的依赖恢复、UC-10 成员不可用或无可调度资源；**功能域规格**：SP-02、SP-03、SP-04。

F-03 将 ARC 创建的临时 Runner Pod 分发至符合条件的成员集群，并保证该 Pod 引用的临时注册凭证在执行成员可用。输入包括传播策略、成员状态和 Pod 的依赖关系；输出是目标成员中的 Runner Pod、依赖对象和相关状态。

**职责边界**：本功能负责 Runner Pod 选择条件、成员候选集及依赖传播规则。成员的节点级调度仍由 Kubernetes 负责。

### 5.3.3 实现思路

平台使用 Karmada 的 `PropagationPolicy` 定义 Runner Pod 匹配规则和候选成员，并使用 `propagateDeps` 传播所引用的 JIT Secret。匹配条件必须依据所选 ARC 版本生成的 Runner Pod 标签制定。Karmada 选择成员，成员 Kubernetes 选择节点。本功能不开发新的 GitHub Job 调度器。`Divided` 和 `Weighted` 不保证每轮独立 Runner Pod 平均分配。

### 5.3.4 设计思路

ARC Controller 创建 Runner Pod 后，Karmada 根据传播策略选择执行成员。Karmada 将 Pod 与其引用的依赖对象传播到该成员。成员 Kubernetes 负责挂载 Secret 和启动容器。JIT Secret 尚未到达时，Pod 可能处于挂载等待状态，此时不得将该 Runner 判定为可执行。成员不可用时必须保留调度失败原因，不能据此重复提交 GitHub Job。

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

开发者继续通过 Runner 服务名称提交任务，不需要自行选择成员集群。平台管理员可以从 Karmada 的 Cluster、Binding/Work 和成员 Pod 事件判断 Runner 的分发位置和未就绪原因。本功能不新增独立网页界面。

### 5.3.6 实现接口设计

#### 5.3.6.1 接口设计

| 交互双方 | 原生接口或资源 | 输入、操作与输出 | 权限与前置条件 | 异常处理与限制 | 依据及规格 |
|---|---|---|---|---|---|
| ARC Controller 与 Karmada API | Kubernetes `Pod`、`Secret`、`ServiceAccount` | ARC 创建带有服务标签、运行身份和 JIT Secret 引用的 Runner Pod。 | Controller 必须具有指定命名空间的所需对象管理权限。 | 对象不存在或 JIT Secret 尚未可用时，必须保留实际状态，不得重新提交 Job。 | ARC-1；SP-03 |
| 平台配置与 Karmada | `PropagationPolicy`：`policy.karmada.io/v1alpha1` | 平台必须设置资源匹配条件、成员候选范围和依赖传播规则。 | 策略维护者只能操作授权范围内的命名空间及成员。 | 策略不匹配或没有合格成员时，保留调度原因，不得默默选择其他租户成员。 | KAR-1、KAR-2；SP-02、SP-03 |
| Karmada 与成员 Kubernetes | `ResourceBinding`、`Work` 及成员对象 | Karmada 将选定 Runner Pod 和它引用的运行依赖分发到指定成员。 | Karmada 必须持有已注册成员的 API 访问身份。 | 成员不可达或资源发生冲突时，按上游调谐语义处理，不强制覆盖其他管理者的对象。 | KAR-1、KAR-2；SP-02 |

**开发交付边界**：开发人员维护 `PropagationPolicy` 及关联权限，并检查目标 Runner Pod 被选中、非目标 Pod 不被选中。不得将 GitHub Job 重新派发作为 Karmada 对象传播失败的默认恢复动作。 Kubernetes 原生对象使用目标版本的 Kubernetes OpenAPI 定义；ARC 和 Karmada 自定义资源使用相应版本的 CRD Schema。

### 5.3.9 DFX分析

**功能安全与性能约束**：传播策略不能无意包含其他租户的 Pod。JIT Secret 必须只分发到有实际执行需求的成员。监控应分别记录成员选择、Secret 可用和 Runner 注册等阶段，不得宣称未经定义的平均分配比例。

**可靠性故障分析（FMEA）**

| 编号 | 失效原因 | 业务或系统影响 | 检测方法 | 处理与恢复要求 | 负责方 |
|---|---|---|---|---|---|
| F03-01 | 传播策略错误地选择了非 Runner Pod。 | 其他工作负载可能被复制到不应访问的成员。 | 比对策略标签与目标 Runner Pod、非目标 Pod 的标签。 | 策略维护者必须收窄选择条件，并确认其他租户 Pod 不匹配。 | 联邦策略维护者 |
| F03-02 | JIT Secret 晚于 Runner Pod 到达成员。 | Runner Pod 无法挂载所需 Secret，启动推迟。 | 检查成员 Pod 的 `FailedMount` 事件和 JIT Secret 引用。 | Karmada 继续调谐依赖传播；成员 Kubernetes 继续挂载重试。平台不得因此重复派发 Job。 | Karmada、成员运维 |
| F03-03 | 候选成员不可达或资源不足。 | Runner Pod 无法进入可执行状态。 | 检查成员 Cluster Ready、Binding/Work 和 Pod 调度事件。 | 运维人员应恢复成员或调整已授权的候选集；不得重复提交正在执行的 Job。 | 联邦与资源池运维 |

**运行诊断与敏感信息处理**：诊断时先查看成员集群的 Ready 状态与 Karmada Binding/Work，再检查成员 Pod 事件和 Secret 引用。日志不得保存 Secret 数据。

## 5.4 F-04 Listener 跨控制面认证与访问

### 5.4.1 功能概述

**关联需求**：SR-04-01；**用例**：UC-08 Listener 访问统一 Karmada API、UC-11 Token 失效或证书不匹配；**功能域规格**：SP-04、SP-05。

F-04 为部署在成员集群中的 ARC Listener 提供访问统一 Karmada API 的身份和连接参数。平台必须使用正确的服务器证书、访问令牌和 RBAC 权限，使 Listener 只能处理对应 Runner 服务的对象。

**职责边界**：本功能负责 Listener 访问 Karmada API 的身份、网络端点和权限约束。不负责修改 Kubernetes API Server 的认证实现。

### 5.4.3 实现思路

平台采用 Kubernetes ServiceAccount、TokenRequest 和 RBAC 配置独立的 Listener 身份。Listener 必须验证 Karmada API 的证书链和服务器名称。部署方应以可配置参数提供 API 地址、证书和凭证，不能把示例中的固定 IP 或手工签发脚本视为完整的凭证轮换方案。

### 5.4.4 设计思路

身份管理方颁发访问凭证，部署方把受控凭证和 CA 交给 Listener。Listener 连接 Karmada API 时验证服务器证书；API Server 验证凭证和权限。只有校验通过，Listener 才能更新指定的 `EphemeralRunnerSet`。网络、TLS、401 和 403 错误必须分别记录，并由对应管理者处理。

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



### 5.4.5 用户接口设计

开发者无需直接操作控制面身份。安全管理员负责角色和凭证更新；平台运维查看 Listener 状态、错误类别与证书或 Token 版本。本功能使用已有身份管理和运行监控系统，不新增专用网页界面。

### 5.4.6 实现接口设计

#### 5.4.6.1 接口设计

| 交互双方 | 原生接口或资源 | 输入、操作与输出 | 权限与前置条件 | 异常处理与限制 | 依据及规格 |
|---|---|---|---|---|---|
| ARC Listener 与 Karmada API | HTTPS Kubernetes API、`EphemeralRunnerSet` | Listener 必须使用指定 API 地址、可信 CA 和访问令牌更新所负责的 Runner 对象。 | Token 必须来自受控身份；RBAC 只授予所需对象和操作。 | 网络、证书、HTTP 401、HTTP 403 和超时必须分别记录，不允许绕过认证。 | K8S-1、K8S-2；SP-05 |
| 部署与密钥管理系统和 Listener Pod | Listener 模板、Secret 或受控凭证投射卷 | 部署方必须提供 CA、Token、有效期及 API 连接信息，并明确更新后的加载方式。 | 凭证应以只读方式提供给需要的容器，禁止出现在日志中。 | Token 到期或未加载时，必须报告身份状态；不得假定修改 Secret 后立即生效。 | SP-04、SP-05 |
| 安全管理员与 Kubernetes RBAC | `Role`、`RoleBinding` | 对指定 `EphemeralRunnerSet` 授予所需 `patch` 权限，对必要的 `EphemeralRunner` 资源授予对应读写和状态权限。 | 必须确认 ServiceAccount 命名空间、绑定对象以及操作范围。 | RBAC 配置与契约不一致时，应修正 Role/Binding；不得赋予 `cluster-admin`。 | K8S-2；SP-05 |

**开发交付边界**：开发人员维护 ServiceAccount、Role/RoleBinding、Listener 模板和证书/令牌加载配置。Token 的到期处理和重新加载方式必须在部署方案中确定。 Kubernetes 原生对象使用目标版本的 Kubernetes OpenAPI 定义；ARC 和 Karmada 自定义资源使用相应版本的 CRD Schema。

### 5.4.9 DFX分析

**功能安全与性能约束**：Listener 必须验证 HTTPS 服务器证书。凭证只授予必要操作权限，日志不得包含 Token 或私钥。高可用与不中断轮换属于部署参数，不能宣称已自动具备。

**可靠性故障分析（FMEA）**

| 编号 | 失效原因 | 业务或系统影响 | 检测方法 | 处理与恢复要求 | 负责方 |
|---|---|---|---|---|---|
| F04-01 | API 地址不可达或 TLS 证书校验失败。 | Listener 不能访问 Karmada API。 | 分别检查 DNS/TCP 连通性、CA 与证书 SAN。 | 部署维护者应修正服务地址或证书配置，不能关闭 TLS 校验。 | 部署与证书管理员 |
| F04-02 | Token 已到期或 Listener 未加载新 Token。 | Listener 的 Runner 数量更新操作失败。 | 检查 HTTP 401、Token 有效期和凭证加载记录。 | 身份管理员更新 Token；部署维护者按规定重载 Listener。 | 身份与部署维护者 |
| F04-03 | Role 或 RoleBinding 的授权范围错误。 | Listener 无法操作所需资源，或存在越权风险。 | 检查 HTTP 403 与 Role/Binding 中的资源名称及操作。 | 安全管理员按最小权限规则修正配置，保留变更审计。 | 安全管理员 |

**运行诊断与敏感信息处理**：平台必须分开记录网络连接错误、证书校验错误、HTTP 401 与 HTTP 403。出现权限失败时，不能通过关闭 TLS 或授予 `cluster-admin` 来绕过。

## 5.5 F-05 Runner Pod 状态反馈与一致性

### 5.5.1 功能概述

**关联需求**：SR-05-01；**用例**：UC-09 Runner Ready 状态反馈、UC-12 状态缺失、冲突或滞后；**功能域规格**：SP-06、SP-07。

F-05 将成员 Kubernetes 中 Runner Pod 的真实状态反馈到 Karmada 控制面，使 ARC 能读取容器名称、运行信息和 Ready 条件。当前设计只覆盖每个 Runner Pod 在一个成员集群执行的场景。

**职责边界**：本功能负责状态解释规则及状态来源的约束。GitHub Job 运行结果仍由 GitHub Actions 管理。

### 5.5.3 实现思路

本功能使用 Karmada `ResourceInterpreterCustomization` 的状态解释机制保留成员 Pod 的状态字段。实现人员只维护状态解释规则，不修改上游 ARC 或 Karmada 控制器。多个成员同时提供同一 Pod 状态时，需要另行定义冲突处理规则，不能沿用选择第一个非空状态的示例方法。

### 5.5.4 设计思路

成员 kubelet 更新 Pod 状态后，Karmada 接收对应状态。状态解释器将所需字段写入控制面 Pod 的状态。ARC 再从控制面读取 Ready 条件和容器运行信息。缺少成员状态时，平台不得自行生成 Ready 条件，也不得根据缺失信息推定 Job 已成功。

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



### 5.5.5 用户接口设计

开发者在 GitHub Actions 查看作业状态。运维人员按 Pod UID、执行成员、容器名称和 Ready 条件分析 Runner 启动问题。本功能不新增网页界面。

### 5.5.6 实现接口设计

#### 5.5.6.1 接口设计

| 交互双方 | 原生接口或资源 | 输入、操作与输出 | 权限与前置条件 | 异常处理与限制 | 依据及规格 |
|---|---|---|---|---|---|
| 成员 kubelet 与 Karmada | Kubernetes `Pod.status` | 成员 Kubernetes 提供 `phase`、`conditions`、`containerStatuses` 和容器运行信息。 | Karmada 必须能读取目标成员 Pod 的状态。 | 状态未返回时应保留未确认状态，不得生成不存在的容器状态。 | KAR-3；SP-06 |
| Karmada 状态解释器与控制面 Pod | `ResourceInterpreterCustomization` 的 `AggregateStatus` | Lua 规则必须将单目标成员的必要 Pod 状态字段写入控制面对象。 | 目标对象为 `apiVersion: v1`、`kind: Pod`；来源必须与选定执行成员一致。 | 没有状态时不能伪造 Ready；出现多个状态来源时必须有明确的选择规则。 | KAR-3；SP-06 |
| ARC Controller 与 Pod 状态 | Kubernetes Pod 读取或监听操作 | ARC 读取 Runner 容器名称、容器状态与 Pod Ready 条件。 | Controller 必须具有读取对应 Pod 的权限。 | 字段缺失时不能假定 Runner 已就绪；必须提供状态与事件诊断。 | ARC-1；SP-06 |

**开发交付边界**：开发人员维护 `ResourceInterpreterCustomization` 中的 Lua 解释规则，并保证目标版本的 ARC 所需字段完整保留。 Kubernetes 原生对象使用目标版本的 Kubernetes OpenAPI 定义；ARC 和 Karmada 自定义资源使用相应版本的 CRD Schema。

### 5.5.9 DFX分析

**功能安全与性能约束**：必须保留 ARC 需要的 Pod 状态字段，同时避免在状态聚合或日志中记录凭证数据。状态同步延迟和告警时限由业务 SLO 确定，不得把测试采样参数当作生产指标。

**可靠性故障分析（FMEA）**

| 编号 | 失效原因 | 业务或系统影响 | 检测方法 | 处理与恢复要求 | 负责方 |
|---|---|---|---|---|---|
| F05-01 | 状态解释器遗漏容器名称或 Ready 条件。 | ARC 可能把 Runner 判定为未就绪。 | 逐项比较成员和控制面 Pod 的状态字段。 | 状态适配维护者必须保留 ARC 所需字段并检查映射规则。 | 状态适配维护者 |
| F05-02 | 同一个 Pod 出现多个互相冲突的状态来源。 | 控制面可能显示错误成员的状态。 | 核对 Binding 的目标成员与各来源 Pod 的标识。 | 设计必须先定义状态来源规则；不得静默采用第一个非空状态。 | 状态适配与 Karmada 管理者 |
| F05-03 | 成员状态同步延迟。 | Runner 就绪信息不能及时反映到控制面。 | 比较 Pod 条件时间和控制面状态更新时间。 | 平台应根据批准的时效性目标设计告警，不能填写未经定义的 SLA。 | Karmada、ARC 运维 |

**运行诊断与敏感信息处理**：排障时比较目标成员与控制面 Pod 的 UID、容器状态和 Ready 条件。状态来源发生冲突时必须先确认目标成员，不能静默取第一个来源。

## 5.6 F-06 异构 NPU Runner 执行资源适配

### 5.6.1 功能概述

**扩展设计，当前无正式核心 SR**：原候选需求 SR-06-01 暂不纳入第 2.4 节；**用例**：UC-07 请求 NPU Runner 并在兼容节点执行、UC-13 NPU 资源不足或设备插件不可用；**功能域规格**：SP-02、SP-08。仓库当前没有 NPU 资源请求或插件部署配置，本节仅提供未来选型后的实施参考。

F-06 允许 CI 开发者选择具有 NPU 能力的 Runner 服务。平台使用成员 Kubernetes 的扩展资源信息配置 Runner Pod 的设备请求，并将该 Pod 分发至满足设备、驱动和运行环境约束的成员集群。

**职责边界**：本功能负责 NPU Runner 服务的资源请求、成员约束和设备兼容配置。设备分配本身由成员 Kubernetes 和设备插件完成。

### 5.6.3 实现思路

目标 NPU 的 Kubernetes Device Plugin 负责发布设备扩展资源。平台将设备插件实际提供的资源名称和数量写入 Runner Pod 模板，并根据成员标签与资源条件选择合格集群。成员 kube-scheduler 负责节点选择。设备型号、扩展资源名称、驱动与容器运行时必须由实际设备规格提供，不能假定所有 NPU 可互换。

### 5.6.4 设计思路

开发者选择 NPU Runner 服务后，ARC 创建具有对应扩展资源请求的 Runner Pod。Karmada 选择满足成员约束的集群，成员 Kubernetes 再选择具有所需设备资源的节点。设备插件负责向节点分配设备。如果没有合格设备，Pod 必须保持不可调度状态，并提供资源不足原因；平台不得将请求改成普通 CPU Runner。

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

开发者使用单独的 NPU Runner 服务名称选择硬件能力。平台必须说明该服务所需的设备类型与软件栈约束。工作流使用 GitHub 原生 `runs-on` 字段；本功能没有新增独立界面。

### 5.6.6 实现接口设计

#### 5.6.6.1 接口设计

| 交互双方 | 原生接口或资源 | 输入、操作与输出 | 权限与前置条件 | 异常处理与限制 | 依据及规格 |
|---|---|---|---|---|---|
| 工作流使用者与 NPU Runner 服务 | GitHub Actions `runs-on` | 开发者必须选择明确标注 NPU 能力的 Runner 服务名称。 | 目标仓库必须获得相应服务的使用授权。 | 没有匹配的服务时，按 GitHub 队列机制显示状态；不得改选普通 CPU Runner。 | ARC-1；SP-08 |
| Runner Pod 与成员 Kubernetes | `Pod.spec.containers[].resources` | Pod 必须声明实际设备插件提供的扩展资源名称和数量，并配置必要的节点约束。 | 成员必须安装与所选设备相符的插件、驱动和运行时。 | 资源不足时保持 Pending 并记录调度原因，不得删除 NPU 请求。 | K8S-3；SP-08 |
| Karmada 传播策略与成员候选集 | `PropagationPolicy.spec.placement` | 传播策略必须把 NPU Runner Pod 分发到满足设备与隔离条件的成员。 | 平台必须提供正确的成员标签及资源条件。 | 无合格成员时保留不可调度原因，不得分发到不兼容集群。 | KAR-1；SP-02、SP-08 |

**开发交付边界**：开发人员在 Runner Pod 模板及传播策略中配置扩展资源请求和目标成员条件；具体值必须来源于选定的设备插件和设备兼容清单。 Kubernetes 原生对象使用目标版本的 Kubernetes OpenAPI 定义；ARC 和 Karmada 自定义资源使用相应版本的 CRD Schema。

### 5.6.9 DFX分析

**功能安全与性能约束**：目标 NPU 的驱动、资源隔离、并发容量和租户权限应由设备管理员提供。没有真实负载指标时，不能写入性能改善百分比或设备共享保证。

**可靠性故障分析（FMEA）**

| 编号 | 失效原因 | 业务或系统影响 | 检测方法 | 处理与恢复要求 | 负责方 |
|---|---|---|---|---|---|
| F06-01 | Runner 请求的资源名称与插件发布的名称不同。 | Runner Pod 无法匹配需要的 NPU 资源。 | 比较 Pod 资源请求与 Node `status.allocatable`。 | 资源池维护者应按插件正式资源名修正服务模板。 | 设备与平台维护者 |
| F06-02 | 设备驱动、固件或容器运行时不兼容。 | Runner 容器启动失败，或者 NPU 作业执行失败。 | 检查节点事件、容器退出原因及设备运行环境版本。 | 设备维护者按厂商兼容资料调整镜像、驱动及运行时版本。 | 设备与运维维护者 |
| F06-03 | NPU 设备资源耗尽或候选成员不可达。 | Pod 不能调度，GitHub Job 需要等待可用 Runner。 | 检查 Karmada 目标成员、Node allocatable 与 `FailedScheduling` 事件。 | 平台必须报告资源不足并维持 NPU 约束，不能改用普通 CPU 节点。 | 调度与资源池维护者 |

**运行诊断与敏感信息处理**：平台应保留 Node allocatable、Runner Pod 的资源请求、成员调度结果和 FailedScheduling 事件。设备异常时，不得通过删除 NPU 请求使错误任务运行在 CPU 节点。

## 5.7 功能追踪与开发接口清单

| SR | 功能 | 关联规格 | 运行视图 | 开发实现入口 |
|---|---|---|---|---|
| FT-01：SR-01-01 | F-01 | SP-01 | 5.1.4.1（UC-02 为异常场景） | `04-autoscalingrunnerset.yaml`、GitHub Workflow |
| FT-01/FT-04：SR-02-01、SR-02-02 | F-02 | SP-01、SP-07 | 5.2.4.1 / 5.2.4.2 | 官方 ARC、RunnerSet、对象生命周期契约 |
| FT-02/FT-03：SR-03-01、SR-03-02 | F-03 | SP-02、SP-03、SP-04 | 5.3.4.1 / 5.3.4.2 / 5.3.4.3 | `02-propagation-policies.yaml`、关联资源 |
| FT-03：SR-04-01 | F-04 | SP-04、SP-05 | 5.4.4.1 | `03-rbac.yaml`、`04-autoscalingrunnerset.yaml` Listener Template |
| FT-03：SR-05-01 | F-05 | SP-06 | 5.5.4.1 | `01-karmada-interpreter.yaml` |
| **扩展候选：原 SR-06-01，当前非正式 SR** | F-06 | SP-02、SP-08 | 5.6.4.1 / 5.6.4.2 | 尚需设备选型；仓库未配置 NPU 扩展资源 |

### 5.7.1 非功能需求与详细设计追踪

| 非功能需求 | 功能域规格 | 主要实现设计章节 |
|---|---|---|
| SR-NF-01 身份与权限 | SP-01、SP-05 | 5.1.9、5.4.6、5.4.9 |
| SR-NF-02 凭证保护 | SP-04、SP-09 | 5.2.9、5.3.9、5.4.9 |
| SR-NF-03 成员故障隔离 | SP-02、SP-07 | 5.2.9、5.3.4.3、5.3.9 |
| SR-NF-04 可服务性 | SP-09 | 5.1～5.6 各功能的 DFX |
| SR-NF-05 性能与容量 | SP-01、SP-09 | 5.1.9、5.2.9；量化参数 P-SR-01、P-SR-02 |
| SR-NF-06 成本与资源利用 | SP-01、SP-07 | 5.2.3、5.2.9；量化参数 P-SR-04 |
| SR-NF-08 策略可维护性 | SP-01、SP-02 | 4.2、4.3、5.3.6 |

本表只将与 FT-01～FT-04 相关的正式 SR 关联至详细设计。第 4、5 章仍可保存 ARC/Karmada 的必要技术契约和 NPU 扩展参考，但不把所有实现机制或通用 DFX 项目升级为正式系统需求。量化参数未获得业务批准时，对应指标保持待定。

**统一资料来源及版本边界**：本文件 3.7 给出 ARC/GitHub Actions、Karmada、Kubernetes 官方文档索引；本仓库现有 Manifest 用于定位配置差异；任何仅有内部运行记录支持、而无上游接口规范支持的声明都应标明为集成设计假设。缺少 PoC 不构成功能设计缺口，但 **SR/接口/异常/DFX 未定义**属于设计缺口。

**实施前需选择的参数（P）**：GitHub 组织/仓库与权限、目标版本和镜像 digest、联邦控制面 API/DNS/CA、凭证发行及轮换、成员与资源标签、NPU 型号与扩展资源名、隔离与容量策略、性能/可用性 SLO。若有正式输入来源，则填写批准值；如果尚无，不得硬编码临时 Kind IP 或杜撰业务 SLA。
