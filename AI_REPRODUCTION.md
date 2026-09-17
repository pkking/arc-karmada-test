# ARC + Karmada 多集群联邦 PoC - AI 协作复现说明与 Prompt 记录

本项目（ARC + Karmada 原生多集群 CI/CD 算力底座验证）全程采用 AI 智能体（Agentic AI）以结对编程与自动化排障模式协作完成。为了便于后续技术团队、开源贡献者与架构师完整复现方案研究与推导过程，特将本次研究所使用的开发工具、底层模型及关键 Prompt 详细归档记录于此。

---

## 1. 工具与运行环境 (Tooling & Model)

| 类别 | 配置信息 | 说明 |
| :--- | :--- | :--- |
| **开发协作工具** | **Antigravity CLI (`agy`)** | 基于自主多工具调用的 AI 编码与系统排障智能体 CLI |
| **驱动模型** | **Gemini 3.8 flash** | 具备长上下文推理、代码分析、实时命令排障与技术架构设计能力的模型 |
| **工作模式** | 结对编程 (Pair Programming) | 涵盖从可行性分析、KinD 联邦集群拉起、源码分析、补丁编译到 PPTX 生成的全流程 |

---

## 2. 核心 Prompt 记录 (Core Prompts for Reproduction)

在整个 PoC 的探索与落地过程中，通过以下关键 Prompt 驱动 Agent 逐步拆解问题并完成端到端落地：

### 阶段一：初始需求提出与架构可行性论证（最初的 Prompt）

> **用户 Prompt（初始输入）**：
> ```text
> 我期望arc这个项目可以对接使用karmada 以便能使用多个集群的资源，之前我们使用过liqo，这是将某个集群模拟成node，不太符合要求，更期望使用karmada，先分析下这个方案是否可行，以及是否可以快速poc
> ```
> - **Agent 响应成果**：完成了 Liqo（虚拟节点）与 Karmada（原生联邦调度）在网络拓扑、资源利用率、故障隔离维度的优劣势技术对比，确立了“管控控制面集中调谐 + 算力执行面轻量承载”的双平面架构可行性，并制定了基于 KinD 的最小化 PoC 验证路线图。

---

### 阶段二：核心业务场景与设计目标确立

> **用户 Prompt**：
> ```text
> 我期望的测试场景：一个统一的runner label，用户不感知底层有多个集群，github也不知道底层有多个集群，只需要把任务发给一个listener，这个listener把任务投递给karmada，由karmada负责找有资源的集群，执行任务
> ```
> - **Agent 响应成果**：确立了“统一 GitHub Runner Label”、“单一 Listener 长连接会话”、“Karmada Scheduler 负责跨集群动态负载均衡”的架构顶层设计原则。

---

### 阶段三：环境构建与最小化 PoC 准备

> **用户 Prompt**：
> ```text
> 已经安装了kind并放了pat在 @gh-token.txt ， 新建一个个人仓库（arc-karmada-test）用来测试吧
> ```
> - **Agent 响应成果**：利用本地 Kind 环境初始化了包含 Karmada 控制面与两个成员集群（member1、member2）的本地拓扑；在 GitHub 远端自动创建了测试验证仓库 `pkking/arc-karmada-test`。

---

### 阶段四：深入代码层排障与四大核心机制攻克

在实际联调过程中，Agent 面对多集群协同断裂场景，自主定位根因并提交解决方案：

1. **Pod 状态同步断裂（误删 Pod）**：
   - 发现 Karmada 默认 `aggregatePodStatus` 丢弃 `containerStatuses[].name` 与 `conditions`，导致 ARC 误判超时。
   - 编写并注入 `ResourceInterpreterCustomization` 声明式 Lua 解释器，实现完整状态 100% 无损回写。
2. **Listener 跨集群网络与鉴权阻碍**：
   - 定位 `x509: certificate is valid for karmada-apiserver, not IP` 与 401 Unauthorized。
   - 利用 `listenerTemplate` 注入 `hostAliases` 与控制面专属 ServiceAccount Token 挂载。
3. **JIT Secret 调度分离断裂**：
   - 配置针对 `arc-runners` Pod 的 `PropagationPolicy`，声明 `propagateDeps: true`，实现瞬时凭证与 Pod 原子协同分发。
4. **ARC 0.14.2 控制器 Secret 调谐死循环**：
   - 发现控制面对 Secret 注入标签导致控制器不断触发更新与重启死循环。
   - 分析 ARC 上游源码，合并 Master 分支修复补丁（PR `#4492`、`#4516`），并注入构建版本号编译出生产可用控制器。

---

### 阶段五：技术资产沉淀与交付

> **用户 Prompt**：
> ```text
> 先把本次poc修改的代码写一份adr
> 把这个adrs整理成一份pptx
> 写一个完整的配置指导放到arc-karmada-test的仓库里，说明arc，karmada如何配置复现这个poc
> ```
> - **Agent 响应成果**：
>   - 沉淀了标准技术架构决策记录 [`2026-09-15-arc-karmada-multi-cluster-federation.md`](https://github.com/actions/actions-runner-controller)；
>   - 输出了 13 页高管/专家汇报级演示文稿 [`2026-09-15-arc-karmada-multi-cluster-federation.pptx`](https://github.com/actions/actions-runner-controller)（包含 6 幅 300 DPI 原生 Matplotlib 架构图）；
>   - 构建了开源复现仓库 [`arc-karmada-test`](https://github.com/pkking/arc-karmada-test)（包含完整 Manifests、自动化脚本及详尽配置文档）。

---

## 3. 复现方法建议 (Reproduction Guide)

如果您希望利用 Antigravity CLI 与 Gemini 3.8 flash 复现本项目的完整探索过程：

1. **安装并启动 Antigravity CLI**：
   ```bash
   agy
   ```
2. **选择模型**：在会话中选择或默认使用 `Gemini 3.8 flash`。
3. **输入阶段一的初始 Prompt**，根据实际集群环境逐步驱动 Agent 完成从架构论证到脚本编排的闭环。
