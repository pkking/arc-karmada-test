# ARC + Karmada 架构设计评审与完成准则

> 适用范围：**设计文档编写、设计评审与 Agent 设计闭环**。证据仅来自各组件的官方技术文档、API/版本规范，以及被明确标记为本项目设计决策的集成约束。
>
> **设计完成不需要 PoC**，不要求 Kind 集群、GitHub Runner 凭证、真实三 Job、T1/T2 定向运行、NPU 硬件实验或其他运行报告。工程实施阶段可参考独立的 [测试计划](ENGINEERING-TEST-PLAN.md)，不得把该文件作为设计评审门槛。

## 1. 依据准则

设计陈述须分成三类：

| 标记 | 意义 | 接受的证据 |
|---|---|---|
| **U：上游能力** | ARC、Karmada、Kubernetes、GitHub Actions 已公开支持的接口和语义 | 官方 API、组件文档与版本化说明，具体链接见[上游资料矩阵](UPSTREAM-SOURCE-MATRIX.md) |
| **D：集成决策** | 本系统基于上游机制制定的控制面部署、凭证/传播、状态与生命周期方案 | 清晰的需求动机、接口/资源契约、交互与异常处理逻辑；明确声明属于本系统设计而非上游官方保证 |
| **P：待具体化参数** | 依环境或供应商确定的值（API 地址、SAN、NPU 资源名、节点选择器、业务 SLA 等） | 给出输入来源、类型、约束、默认原则和决策责任；不强求设计文档给出某次 PoC 的硬编码值 |

**不能**用单次 GitHub Job 成功或 Pod 分布证明 ARC/Karmada 上游 API 的一般保证；也不能因为没有 PoC 就判定文档不合格。

## 2. 设计评审检查项

| 编号 | 设计必须回答的问题 | 验收方式与完成条件 |
|---|---|---|
| DR-01 业务价值和职责 | 本系统为谁解决什么问题，外部 GitHub Actions、执行集群和运营系统如何交互？ | 第 1 章目标、边界、Before/After 和系统级职责一致，未宣称未经测量的性能百分比 |
| DR-02 需求完整性 | US-01～US-08 的参与者、目标、正常/异常路径、外部体验是否定义？ | 第 2 章与第 4 章逐 Story 对应，需求无无主组件或不明输入 |
| DR-03 上游依据 | 每个 Story 依赖的 ARC/Karmada/Kubernetes 原生能力是否确有官方资料？ | [依据矩阵](UPSTREAM-SOURCE-MATRIX.md)提供精确链接、所支持的语义及禁止外推的结论 |
| DR-04 架构分层 | 系统自有逻辑与上游复用组件的边界、部署域、对外链接是否清晰？ | 第 3.1 只写职责/协作，第 3.2 只写部署与外部连接；不深入 ARC/Karmada 内部模块 |
| DR-05 资源及接口 | Runner CR/Pod、JIT Secret、Policy、Binding/Work、状态资源的生产者、消费者与权限是否明确？ | [实现契约](IMPLEMENTATION-CONTRACT.md)可追溯到官方能力/设计决策，接口字段与状态语义自洽 |
| DR-06 时序与异常 | Listener 认证、JIT 依赖传播、Pod Ready、成员故障、任务清理如何处理？ | 每个时序图体现关键交互，明确异步先后、最终一致、失败责任及不支持的语义 |
| DR-07 安全 DFX | 跨控制面 Token/CA/TLS、Secret 最小传播、Runner SA 权限、证书轮换与敏感信息处理是否明确？ | 采用 [Kubernetes SA](https://kubernetes.io/docs/concepts/security/service-accounts/) 和 [RBAC Good Practices](https://kubernetes.io/docs/concepts/security/rbac-good-practices/) 原则；样例高权限或全域传播应标风险而非目标能力 |
| DR-08 可靠性与性能 | 均衡、超时、迁移、清理、容量和 SLO 哪些是保证、哪些是待设计指标？ | 不将 `Divided/Weighted` 等同逐轮均衡，也不把组件特性等同生产级 HA |
| DR-09 NPU 扩展 | NPU 资源可通过什么标准接口接入？选型前哪些参数未知？ | 基于 [Kubernetes Device Plugins](https://kubernetes.io/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/) 给出设备插件/扩展资源/节点调度的设计，型号/资源名明确标为待选型，而非「缺少 PoC 导致设计不通过」 |
| DR-10 文档一致性 | 主设计、实现契约、官方依据、Story 图与现有配置示例是否存在矛盾或未标注差异？ | 逐项引用明确，所有与示例不同的目标设计列作设计决策或实施差异；禁止把 PoC 配置默认为官方规范 |

## 3. Story 的文档证据追踪

| Story | 主要上游机制 | 项目集成边界 | 文档评审结论 |
|---|---|---|---|
| US-01：统一标签 | ARC-1、ARC-2 | `runs-on` 与 Scale Set 映射 | 可文档化 |
| US-02：Runner 生命周期 | ARC-1、GH-1 | 集中 ARC 与执行成员分工 | 可文档化 |
| US-03：跨成员分发 | KAR-1 | PropagationPolicy、成员选择；无跨 Pod 固定均衡保证 | 可文档化 |
| US-04：JIT Secret | ARC-1、KAR-2 | `propagateDeps` 和非原子传播 | 可文档化 |
| US-05：Listener 认证 | ARC-1、K8S-1、K8S-2 | 控制面 Token/CA/TLS/RBAC | 可文档化 |
| US-06：Pod 状态 | ARC-1、KAR-3 | 自定义聚合规则和单目标约束 | 可文档化 |
| US-07：临时对象回收 | ARC-1、KAR-1 | 跨控制面生命周期、异步删除责任 | 可文档化 |
| US-08：NPU 接入 | KAR-1、K8S-3 | Device Plugin、资源名和兼容性输入 | 设计可完成；实际 NPU 型号/资源名待选型，运行行为仍属**待验证** |

所有编号对应[官方组件资料与设计推论矩阵](UPSTREAM-SOURCE-MATRIX.md)。表中的“可文档化”表示设计依据充分，**不代表集成已经运行或经过 PoC 证明**。

## 4. 设计审查结论的边界

以下条件**只属于工程实施或运行验收**，不能成为 Issue #1 的设计完成阻塞项：建立 Kind 集群、配置 PAT、运行 T1/T2/T3、确认工作流结果、执行故障注入、进行性能和 NPU 压测、证明生产级容灾或自动 Token 轮换。

设计完成需要：

- [x] 官方能力的来源、约束、版本风险均有清晰说明，并区分本系统提出的联邦集成假设。
- [x] US-01～US-08 有对应的上游资料、架构责任、消息时序与异常边界。
- [x] 控制面访问、JIT Secret、Pod 状态、生命周期、成员放置、NPU 扩展都定义必要接口与待选参数。
- [x] 已识别高权限 kubeconfig、主 GitHub Secret 传播与 Pod 广域选择器等设计风险，不把样例误写为生产规范。
- [x] 实施测试与文档设计评审互相独立：**设计评审不需要 PoC 或运行证据**。

**结论**：完成上述文档审查，便可认为设计文档本身闭环；真实跨组件兼容性属于独立的工程实现风险，官方组件文档并不能提供端到端担保。工程测试方法见[可选实施测试计划](ENGINEERING-TEST-PLAN.md)，与本设计完成标准无关。
