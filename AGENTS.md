# ARC + Karmada Agent 工作规范

本仓库同时包含 **架构设计文档** 和 **PoC/工程实施资产**。两者的完成标准必须严格区分：设计文档只需要官方组件文档依据和自洽的集成接口/异常/DFX 设计；**不允许把 PoC、T1/T2、Kind 重建、GitHub PAT 或真实 Runner Job 作为设计文档完成的前提**。

## 1. 设计任务的阅读顺序

1. [系统设计说明书](docs/KARMADA-ARC-SUBSYSTEM-DESIGN.md)：系统边界、问题、关键需求、逻辑/部署架构、US-01～US-08 运行视图。
2. [上游官方文档依据矩阵](docs/UPSTREAM-SOURCE-MATRIX.md)：ARC/Karmada/Kubernetes/GitHub 官方机制、文档链接及禁止外推的结论。
3. [集成接口与资源设计契约](docs/IMPLEMENTATION-CONTRACT.md)：本系统需要承担的资源边界、身份、依赖、状态、回收与异常责任。
4. [架构设计评审与完成准则](docs/ACCEPTANCE-CONTRACT.md)：DR-01～DR-10 的设计完整性检查。

**设计证据只能来自上游组件文档/API/版本规范**。仓库 Manifest 可用于解释当前示例，不是官方产品兼容性证明；历史 PoC 数据、GitHub Run 和测试结果不需要收集，也不构成架构设计评审证据。

## 2. 设计写作规则

- ARC、Karmada、Kubernetes 均作为复用的上游系统。3.1 逻辑架构只描述主要问题与模块协作，不展开其内部控制器；3.2 部署架构只说明组件在哪里运行及与周边系统交互。
- 针对每个 Story：明确用户目标、上游能力出处、项目自己承担的集成决策、接口、正常/异常交互、故障责任与 DFX 约束。
- 用 **U（官方能力）/ D（本项目设计决策）/ P（待选参数）** 区分事实、推论和环境输入。尤其不能把官方 ARC JIT 与 Karmada propagateDeps 的分别存在，叙述为上游保证二者组合端到端兼容。
- 主 GitHub Secret 应最小传播；不要把现有成员间主凭证复制或整个命名空间 Pod 匹配当作目标的安全设计。
- Pod.status 聚合、控制面 TLS/SA/RBAC、Secret 异步到达、Runner 临时生命周期、成员故障迁移的语义需要写清适用条件。不承诺未经官方定义的跨资源原子性、跨 Pod 逐轮均衡、无损故障迁移或生产 SLA。
- NPU 可基于 Kubernetes Device Plugin 与 extended resource 标准接口给出完整设计；实际厂商硬件、资源名、Runtime/驱动为选型参数，**不因尚无 NPU 实测就阻塞设计**。
- 修改主文档应维护第 5 章的 Story→上游能力→集成决策映射，且至少引用相应官方资料。设计资料的核对不需要启动集群或执行工作流。

## 3. 工程实施任务（与设计审批解耦）

仅在用户明确要求实施、部署、CI 或测试时，继续使用[README](README.md)、[Manifest](manifests/)、[工程测试计划](docs/ENGINEERING-TEST-PLAN.md)、[安全审查](docs/SECURITY-REVIEW.md)、[环境重建记录](docs/REBUILD-VALIDATION.md)、[历史验证资料](docs/arc-0.15.0-validation.md)。

实施阶段保留以下护栏：

- 默认优先复用官方 ARC 0.15.0/Karmada v1.19.0，不私自引入上游源码补丁；实际目标版本由部署基线决定。
- `propagateDeps: true` 不是原子事务；当前 Runner Pod 全 namespace 选择器和主凭证跨成员传播是待收敛风险。未经依赖/影响分析不得静默扩大或收窄生产策略。
- Listener Token、控制面证书及最小 RBAC 需由部署方案提供，不能将示例管理员 kubeconfig 当作推荐权限。
- 运行测试必须严格区分功能成功、成员独立可执行、单轮覆盖与统计均衡，不为通过而放宽断言。
- 任何测试、PoC、CI 结果必须如实标注已执行/未执行，不提交 PAT、Token、Secret Data、私钥和 kubeconfig。
- 变更代码时执行对应的单元/静态/工程验证；变更**仅设计文档**时按 DR-01～DR-10 核对官方依据即可，不触发 GitHub Runner 或 Kind 部署。

## 4. 交付说明

- **设计文档提交**：列明 US 编号、官方组件资料链接、本系统新增/修改的决策、对接口/状态/DFX 的影响，以及未确定但有约束的参数；不需要附带 PoC Run。
- **实施变更提交**：列明变更文件、功能/安全影响、可执行测试及脱敏日志，不把运行结果伪装成文档证据。
- 若关键上游 API 文档不支持某项设计断言，应标记为本系统集成假设并说明风险，而不是编造已经验证的事实。
