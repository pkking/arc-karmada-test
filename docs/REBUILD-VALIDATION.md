# 全新 Kind 环境重建与验收记录

> Issue #1 的最后一道 P0 完成门槛。**当前状态：未完成真实重建验证。**
> 2026-10-15 的已验证 PoC 复用了先前存在的 Kind 集群；新增离线 CI 只能验证契约与代码，不能替代重新创建集群并跑真实 GitHub Job。

## 1. 需要具备的执行环境

- 可运行 Docker、Kind、kubectl、karmadactl 的 Linux 计算环境，以及足够的 CPU/内存和可访问官方镜像仓库的网络。
- 用于 GitHub 自托管 Runner 注册/执行的授权凭证和目标测试仓库；凭证需从安全存储注入，禁止将其交给日志或 Git。
- Controller 可访问 Karmada API；member1 Listener 通过 TLS SAN/CA/Token 访问控制面；两个成员 Runner 可通过各自网络访问 GitHub。
- 本仓库所有 [Manifest](../manifests/) 和官方 ARC 0.15.0 CRD；固定版本/镜像 digest 见[实现契约](IMPLEMENTATION-CONTRACT.md)。

## 2. 前置检查（无副作用 / 部分只读）

~~~bash
# 不要在公共群聊或 CI log 输出任何 Secret/Token/Kubeconfig
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
bash -n scripts/*.sh

# 需要宿主机确实安装所需工具；不作集群访问
python3 scripts/preflight-poc.py --mode host \
  --output /tmp/arc-015-validation/host-preflight.json

# 按 README 在全新 Kind 环境完成部署后，以只读方式检测现有集群
python3 scripts/preflight-poc.py --mode ready \
  --output /tmp/arc-015-validation/ready-preflight.json
~~~

以上命令的 JSON 会明确标注 `e2eVerified: false`；Ready 仅表示控制面连接/成员/Listener 基本条件，不证明 GitHub Job 已完成。

## 3. 部署和端到端验收顺序

| 阶段 | 需保留的证据 | 通过条件 |
|---|---|---|
| R0：新建前 | 主机工具/版本、Kind 节点镜像、空环境状态、脱敏网络拓扑 | 记录初始环境，与旧 PoC 隔离；不会复用旧集群 |
| R1：新建 Karmada | 创建控制面与两成员的日志、Karmada v1.19.0 镜像摘要、成员状态 | 两个成员 `Ready/Push`，控制面 API 证书与 Listener 访问路径有效 |
| R2：安装 ARC | 四个 CRD hash/版本、Policy/Role、RunnerSet、Controller/Listener 镜像摘要 | 单 Listener Ready、镜像 `0.15.0`、GitHub session 成功；`minRunners=0` 可无空闲 Runner |
| R3：T1 功能 | `verify-arc.py --dispatch --coverage-mode report-only` 的脱敏 JSON、三 Job URL | 三 Job success；各观测 Pod/JIT 同成员，status 名称/Ready 完整，任务后临时对象清理；单轮 3:0 不否定功能 |
| R4：T2 定向 | 明确保存 member1/member2 的临时 placement，分别运行的 URL/JSON | 每个成员各自完成三 Job 与清理；结束后恢复原双成员 placement |
| R5：安全专项 | [只读凭证审计](SECURITY-REVIEW.md)和 Runner 真实 Pod labels | 消费者/label 依赖证据足够后才能执行候选最小传播与回滚演练 |
| R6：收尾 | 恢复策略状态、控制面/成员 Runner Pods、ERS/ER/JIT、Binding/Work 列表（脱敏） | 本轮资源无残留；历史 Finalizer 单列，不伪造全环境无泄漏 |

执行命令的来源： [README 第五部分](../README.md#五-逐步复现操作指南)。当前仓库没有承诺“一个 shell 脚本即可自动完成全环境初始化”；尤其要从新的宿主网桥实际发现 Karmada IP/端口，不得照搬 `172.22.0.4:32443`。

### T1/T2 可重复的验证入口

~~~bash
# 需要真实授权的 gh CLI、Listener Ready 和 Karmada/member contexts
python3 scripts/verify-arc.py --dispatch --coverage-mode report-only --duration 180 \
  --output /tmp/arc-015-validation/t1-functional.json

# T2：先将 Runner placement 审阅后暂时定向 member1，执行：
python3 scripts/verify-arc.py --dispatch --coverage-mode require \
  --required-contexts kind-member1 \
  --output /tmp/arc-015-validation/t2-member1.json

# 再将 placement 定向 member2，执行：
python3 scripts/verify-arc.py --dispatch --coverage-mode require \
  --required-contexts kind-member2 \
  --output /tmp/arc-015-validation/t2-member2.json

# 两次完成后，恢复 manifests/02-propagation-policies.yaml 的双成员策略；
# 必须另行核对工作流、对象最终状态与恢复后的控制面。
~~~

## 4. 本次请求可确认的执行结果

| 工作 | 当前事实 |
|---|---|
| 离线契约测试 | 已通过 GitHub Actions 托管 Runner；具体 Run URL 在 [Issue #1](https://github.com/pkking/arc-karmada-test/issues/1) 跟踪 |
| Secret PodSpec 消费者审计程序 | 已提交及单测；真实成员审计没有执行 |
| Host / Ready 预检工具 | 已提交及单测；在隔离执行环境运行尚无真实输出 |
| 新建 Kind + Karmada + ARC | **尚未执行**，不能将 README 初始化命令当作已验证 |
| 新环境 T1 三 Job | **尚未执行** |
| 新环境 T2 两成员独立定向 | **尚未执行**；历史证据有 member2 定向，不等于本轮新环境 T2 完成 |
| D-01/D-02 安全收敛 | 只备好候选配置；尚未使用真实环境执行或批准替换稳定策略 |
| US-08 NPU、HA、生产 SLO | P1，不在本轮 PoC 交付范围；仍未验证 |

## 5. 失败分类和保密记录

- 资源初始化失败：保存工具版本、脱敏命令、阶段/退出码、Kubernetes Event 原因，但**不保存**含 Token 的环境变量、Secret.data 或 kubeconfig。
- GitHub dispatch 超时：通过 gh 查询本轮 Run，不可直接重复派发。
- 某轮三个 Job 成功但仅分布一个成员：T1 可通过，覆盖实验不足；T2 由定向运行证明。
- 成员不可达、Pod Pending、JIT Secret 未到、Runner 注册失败、清理 Finalizer：按[实现契约](IMPLEMENTATION-CONTRACT.md#7-异步故障和恢复契约)区分职责。
- 敏感信息不能提交仓库、PR 评论或公共日志。生成的证据报告仅存对象名/UID/RV、集群别名、诊断代码、Runner 实例名、GitHub Run 链接和已脱敏摘要。

## 6. 真正关闭 Issue #1 的证据门槛

必须有新的**实际** R0～R4/R6 运行记录与恢复结果；安全变更还必须通过 R5 和独立复核。只有静态 CI 通过或没有环境/凭证时，Issue 必须保持 Open。发现新环境或安全审批是外部依赖时，明确列出提供者、所需访问权限与复验步骤，不宣称自动完成。
