# Agent 开发与设计符合性规则

本仓库验证官方 ARC 0.15.0 + Karmada v1.19.0 + 双 Kind 成员集群的 GitHub Actions Runner 联邦执行。代码主要是上游集成配置、启动脚本与验证工具，**不以自行开发 ARC/Karmada 内部控制器为目标**。

## 1. 每次修改前的阅读顺序

1. [架构说明](docs/KARMADA-ARC-SUBSYSTEM-DESIGN.md)：范围、设计决策、US-01～US-08。
2. [实现契约](docs/IMPLEMENTATION-CONTRACT.md)：必须满足的资源名、Namespace、Policy、RBAC、凭证与生命周期。
3. [验收契约](docs/ACCEPTANCE-CONTRACT.md)：T0/T1/T2/T3 的不同通过条件与 Story 追踪。
4. [README](README.md) 和对应 `manifests/`、`scripts/`、`tests/`：事实基线及操作。
5. [运行验证及限制](docs/arc-0.15.0-validation.md)、[证据目录](docs/evidence/)：已验证与未验证的界限。
6. [安全审查](docs/SECURITY-REVIEW.md) 与 [重建验收清单](docs/REBUILD-VALIDATION.md)：当前 Secret 传播风险、从零 Kind 验证进度及需要真实执行的 T1/T2。

**优先信任与版本匹配的源码/Manifest、现有实测证据。** 文档互相矛盾时，应报告并修订，不能隐式选择更方便的解释。

## 2. 工作顺序

1. 从 Issue 或用户故事明确 US 编号、目标、是否涉及 P0 设计契约还是 P1 生产化能力。
2. 列出受影响资源和跨模块接口；对照契约标记：**上游复用 / 本仓库配置与适配 / 未验证**。
3. 在动代码前列出关键不变量、负向场景、对 T0/T1/T2/T3 哪个验收等级产生影响。需要设计取舍时单独记录 D-xx 决策项。
4. 最小化变更；不修改官方 ARC/Karmada 源码，不重建上游内部详细设计，不为修复测试而放宽断言。
5. 运行离线回归，若有真实集群则补充 API dry-run、真实 GitHub Job 和定向成员验收；交付时严格区分“执行过/未执行”。
6. 更新 Story → 契约 → 实现 → 测试 → 证据映射，并在提交/PR 中给出本次变化、故障模式、验证命令、执行结果和剩余风险。

## 3. 不得违反的实现边界

- ARC Controller/Listener 使用官方 0.15.0，CRD、RunnerSet 版本标签一致；Runner 镜像 2.337.0 为当前验证基线。
- `arc-secret-propagation` 目前包含 `arc-github-secret` 和 Runner SA 的两成员传播及 `Overwrite`；这是**现状**，不是宣称主凭证必须传播。删除前先验证依赖。
- `arc-listener-propagation` 定向 member1；`arc-runner-pod-propagation` 目前选择 `arc-runners` 下**全部 Pod**。收窄策略需要兼容性/负向选择测试，不能在不说明的情况下修改。
- `propagateDeps: true` 的 Secret/Pod 传播是异步的；短暂 FailedMount 允许，不能伪造为原子事务。
- Karmada Pod 状态 Lua 当前只取第一个非空 status；不要宣称支持多副本状态融合或运行中迁移。
- Listener 必须持控制面 SA 的 Token/CA，通过 TLS SAN 和最小 RBAC 访问 Karmada API；当前 Token 手工签发/轮换，Controller 使用管理 kubeconfig。
- `Divided / Weighted` 对独立 Pod **不保证逐轮两成员覆盖**；不能用一次 2:1 宣称统计负载均衡。
- 全新 Kind/Karmada 初始化和 Manifest dry-run 已通过 [CI #40111697216](https://github.com/pkking/arc-karmada-test/actions/runs/40111697216)，但真实 GitHub Runner Job 的全新环境 T1/T2 **未验收**；NPU、HA、自动 Token 轮换和生产级容量优化仍不在已验证范围。
- 不得打印或提交真实 PAT、Token、Secret Data、Kubeconfig、GitHub App 私钥、证书私钥，证据需脱敏。

## 4. 推荐验证流程

~~~bash
# 不依赖真实集群的回归
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
bash -n scripts/*.sh

# 有真实且已鉴权的 Karmada API 时
kubectl --kubeconfig="$HOME/karmada-data/karmada-apiserver.config" apply --dry-run=server -f manifests/

# T1 功能验收：即使 3:0 分布也可区分 Job 功能成功与单轮覆盖不足
python3 scripts/verify-arc.py --dispatch --coverage-mode report-only --duration 180 \
  --output /tmp/arc-015-validation/t1-functional.json

# 兼容已有综合覆盖验证（默认 require）
python3 scripts/verify-arc.py --dispatch --coverage-mode require --duration 180 \
  --output /tmp/arc-015-validation/coverage-experiment.json
~~~

T2 要先**显式、审阅后**定向 placement 到指定 member，再以 `--required-contexts kind-member1` / `kind-member2` 分别执行；验证后恢复原始双成员策略并保存证据。T3 统计均衡不在上述确定性门槛内。

## 5. 验收与安全规则

- 不能把测试入口存在当作已经执行，也不能把旧证据当作本次新版本 E2E。
- GitHub dispatch 超时可能已被接受；**先发现 Run，不可立即重复派发**。
- 不清理未知历史 ERS Finalizer 来让报告变绿；本轮 UID 盘点与历史遗留分别报告。
- 发现需要超出现有契约的设计选择时，应记录备选方案、风险和验收影响，等待明确决定；不擅自扩大到 NPU、HA 或生产迁移。
- 生成的 PR/提交说明至少包含：`US-ID`、变更目标、接口/资源差异、T0/T1/T2/T3 执行结果、证据 URL/JSON（脱敏）及未验证条目。
