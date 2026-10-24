# ARC + Karmada 设计依据与上游文档追踪矩阵

> 文档性质：**架构设计评审的依据清单**，不是运行验证报告。
> 本设计的完成条件为“关键需求有组件文档依据、集成决策合理、接口/异常/DFX 契约可审查”，**不依赖 PoC、Kind 环境、GitHub Runner 凭证、T1/T2 测试或运行证据**。

## 1. 依据类型与使用规则

- **上游已定义的机制（U）**：ARC、Karmada、Kubernetes、GitHub Actions 的正式公开文档描述的 API/行为；引用精确文档链接。
- **本系统的设计决策（D）**：将不同上游能力组合时由本系统负责的布置、配置、职责、边界、最小权限、依赖传播和状态回传方案。必须写明前提与异常；不得描述成上游对这种组合提供的官方兼容性承诺。
- **待具体化的接口参数（P）**：真实 GitHub 组织与仓库、Karmada API 地址、证书 SAN、目标成员、Runner 标签与设备厂商资源名称。设计可明确参数约束及提供者；不需要在设计阶段填某个 Kind 容器 IP。
- **实现/运行验收（I）**：Manifest、PoC 记录、测试 Run、E2E/HA/负载实验仅供后续实现工作使用，**不是本设计文档的证据或通过前提**。

如文档间版本不同，应以选定部署版本 API 为准继续接口兼容性审查；官网滚动文档不能自动证明 ARC 0.15.0/Karmada v1.19.0 的所有组合行为。

## 2. 官方文档与支持范围

| ID | 组件及官方资料 | 文档明确支持的能力 | 不应外推的结论 |
|---|---|---|---|
| ARC-1 | [GitHub 官方：Actions Runner Controller](https://docs.github.com/en/actions/concepts/runners/actions-runner-controller) | Scale Set 任务匹配；Listener 长轮询、PATCH EphemeralRunnerSet；Controller 产生 EphemeralRunner/Pod、JIT 注册、任务完成回收 | ARC 原生已经支持 Karmada 联邦控制面或跨集群网络 |
| ARC-2 | [GitHub 官方：部署 Runner Scale Sets](https://docs.github.com/en/actions/how-tos/manage-runners/use-actions-runner-controller/deploy-runner-scale-sets) | Scale Set 安装与配置、注册凭证及指标/日志来源 | Listener 放在另一集群、Token 镜像传播的完整可用性 |
| GH-1 | [GitHub 官方：Self-hosted runners reference](https://docs.github.com/en/actions/reference/runners/self-hosted-runners) | Ephemeral Runner 设计建议与单次 Job 分配行为 | 跨集群放置、容量/公平性或队列等待改善的保证 |
| KAR-1 | [Karmada 官方：Propagation Policy](https://karmada.io/docs/next/userguide/scheduling/propagation-policy/) | ResourceSelector 的名称/标签范围、placement、clusterAffinity、propagateDeps、replicaScheduling | 多个独立 Pod 必须在不同集群均匀分布；或所有 ARC 对象都适合传播 |
| KAR-2 | [Karmada 官方：Propagate dependencies](https://karmada.io/docs/userguide/scheduling/propagate-dependencies/) | 传播已引用的 ConfigMap/Secret 等依赖对象（配置 propagateDeps） | Secret 与 Pod 的传播存在跨资源原子事务或强时序保证 |
| KAR-3 | [Karmada 官方：Customizing Resource Interpreter](https://karmada.io/docs/userguide/globalview/customizing-resource-interpreter/) | ResourceInterpreterCustomization 的 AggregateStatus 等操作及框架 | 自定义 Lua 聚合一定能正确处理多个独立成员的同名 Pod 或故障迁移 |
| K8S-1 | [Kubernetes：Service Accounts](https://kubernetes.io/docs/concepts/security/service-accounts/) | SA/RoleBinding 跨 Namespace 授权、TokenRequest、projected token 与到期语义 | 把控制面 Token 放入普通 Secret 后可以自动轮换 |
| K8S-2 | [Kubernetes：RBAC Good Practices](https://kubernetes.io/docs/concepts/security/rbac-good-practices/) | 最小权限、减少特权 Token 分发、禁用不必要的 SA token 自动挂载 | 管理员 kubeconfig 是生产推荐方式 |
| K8S-3 | [Kubernetes：Device Plugins](https://kubernetes.io/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/) | 设备插件扩展资源发布、Pod 资源请求、kubelet 分配流程 | 任意 NPU 型号和驱动自动兼容 |
| LOCAL-1 | [本仓库传播策略](../manifests/02-propagation-policies.yaml)、[RunnerSet](../manifests/04-autoscalingrunnerset.yaml) | 项目当前**配置示例**的 labels、namespace、placement 与凭证引用 | 官方文档或生产合规保证；本仓库配置不构成上游规范 |
| LOCAL-2 | [本仓库 Lua 解释器](../manifests/01-karmada-interpreter.yaml)、[RBAC](../manifests/03-rbac.yaml) | 项目设计适配点的具体写法 | 原生 Karmada/ARC 在该组合方案中的兼容性官方声明 |

## 3. US-01～US-08：能力 → 依据 → 本系统集成决策

| Story | 可直接依赖的上游能力 | 需要本系统负责的设计决策 | 文档审查结果 |
|---|---|---|---|
| US-01 统一标签 | ARC-1、ARC-2 | 指定统一 Scale Set 名与 GitHub runs-on 匹配方式、授权范围 | 文档依据可用；标签值是配置决策 |
| US-02 集中 Runner 生命周期 | ARC-1、GH-1 | 仅由集中 ARC 管理 Runner CR/Pod/JIT；联邦成员不部署 ARC 控制器；声明资源所有权 | 上游生命周期依据可用；跨控制面为集成设计 |
| US-03 跨成员分发 | KAR-1 | 传播 Runner Pod、选择合格成员；说明无逐轮均分保证、节点调度由成员 K8s 承担 | 策略 API 有据；组合方案为设计决策 |
| US-04 JIT 凭证跟随 | ARC-1、KAR-2 | Runner Pod 依赖动态 JIT Secret，采用 propagateDeps；容许异步先后顺序与暂时 Pending | 两侧原生能力均有据；跨资源时序非原子 |
| US-05 Listener 跨控制面访问 | ARC-1、K8S-1、K8S-2 | Listener 选择 Karmada API endpoint/CA、签发最小权限 Token、配置网络与轮换责任 | Kubernetes 授权机制有据；Listener 远程 API 访问是本项目适配选择 |
| US-06 Pod 状态回传 | ARC-1、KAR-3 | 定义控制面期望的 containerStatuses/Ready、单目标状态归并语义、冲突处理 | 解释器扩展有据；Lua 规则属本项目设计 |
| US-07 资源回收 | ARC-1、KAR-1、KAR-2 | ARC 临时 Runner 生命周期与 Karmada 传播对象删除的协作；明确最终一致与 Finalizer 边界 | 上游生命周期可用；跨控制面清理策略需契约约束 |
| US-08 NPU 执行设计 | K8S-3、KAR-1 | 定义具体 NPU 扩展资源名、设备插件、候选成员标签与 Runner Pod 请求；不预设产品兼容 | 可完成架构方案；厂商资源名称待选型，不需 PoC 才可写设计 |

## 4. 设计审查必须覆盖的集成前提

1. **职责边界**：ARC 负责 Runner 的 GitHub 对接和生命周期，Karmada 负责成员选择/对象传播，成员 Kubernetes 负责节点调度和执行。不得在逻辑架构中复述三套上游内部架构。
2. **单个 Runner Pod 的放置**：目标模型为每个 Runner Pod 最多选定一个有效执行成员，不能用资源策略的 Weighted/Divided 描述跨多个独立 Pod 的逐轮均匀分配保证。
3. **凭证最小化**：GitHub 主凭证属于 ARC 管理链路，Runner 注册使用 JIT；是否需要把主凭证下发成员必须按引用路径和最小权限审查。默认建议只传播 Runner 实际依赖的临时 Secret；若实现例子与此冲突，应标明为实施风险。
4. **认证链路**：Karmada 控制面 API 与成员 API 分属不同信任边界。跨控制面访问须明确 CA/SAN、Token audience/expiry、RBAC 最小权限和轮换责任。
5. **状态语义**：ARC 需要 Runner Pod 容器和 Ready 信息。聚合策略需适合单目标 Pod；不把“返回首个非空 status”推广为任意多成员语义。
6. **异步与失效**：JIT Secret 晚到、成员不可达、Job 中断/重复、Pod Pending、回收阻塞分别定义观测、责任主体和恢复原则。上游没有承诺的自动故障迁移不得凭空承诺。
7. **NPU 资源**：Kubernetes 设备插件为能力基础，资源名、驱动、兼容性需要特定厂商资料与选型决策；若未确定，文档写成设计参数而不是已交付支持。
8. **性能与 SLO**：应说明衡量方法和预期收益，不能从上游特性文档推出百分比改善或统计均衡数据。

## 5. 文档完成判据（无需 PoC）

- [x] US-01～US-08 都对应官方文档机制、项目集成决策及适用边界。
- [x] 逻辑架构只描述主要问题和协作关系，部署架构只描述组件放置与对外连接。
- [x] 关键配置、资源、异常、身份和状态契约有文档依据或被明确标为本项目设计决策。
- [x] 已知未确定的参数/兼容性不作为“已实现”事实宣称。
- [x] 不要求 Kind 重建、CI、GitHub PAT、T1/T2 或 NPU 硬件数据才能完成架构文档评审。

**重要限定**：文档审查通过，仅证明设计说明完整且其上游机制引用可追溯；并不证明跨产品组合无集成缺陷、软件已经开发完毕或满足运行 SLO。PoC、测试、性能实验属于后续工程验证阶段，由独立交付计划管理。
