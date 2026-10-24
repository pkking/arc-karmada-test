# ARC + Karmada Story 验收契约与追踪矩阵

> 基线：2026-10-15 PoC；目标是可验证的行为等价，不是生产 SLA。对应[设计文档](KARMADA-ARC-SUBSYSTEM-DESIGN.md)、[实现契约](IMPLEMENTATION-CONTRACT.md)和 [Issue #1](https://github.com/pkking/arc-karmada-test/issues/1)。

## 1. 验收分级与结果语义

| 等级 | 要验证的属性 | 判断逻辑 |
|---|---|---|
| **T0：静态符合性** | 精确 CRD/Policy/RunnerSet/RBAC/工作流契约、脚本行为、敏感信息保护 | 离线单测、精确 Manifest 检查、`kubectl apply --dry-run=server`（需真实 API）；静态通过不等于 E2E |
| **T1：确定性功能** | GitHub 三 Job 成功、Runner 生命周期、JIT Secret 同成员、Pod 状态回传、本轮清理、Listener 稳定性 | 观测 Run、Runner Pod/Secret/ER 的采样与清理；即使三个 Job 都落到同一可用成员，也可以功能成功 |
| **T2：各成员独立可执行** | member1、member2 在定向 placement 下分别能完成 Job | 两次明确的定向测试，分别保存配置改变/恢复和证据；不是单轮随机两成员覆盖 |
| **T3：统计均衡、资源利用和排队收益** | 多轮任务在异构资源和排队条件下是否改善 | 需单独定义任务负载、并发、采样窗口、队列等待/资源利用/成员分布指标和 SLO；**当前未完成，不能默认为必须通过** |

**冲突规则**：GitHub Job 成功 ≠ 单轮两成员覆盖；单轮 3:0 ≠ Job 失败；一次 2:1 ≠ 统计意义的负载均衡；静态 Manifest 正确 ≠ 全新 Kind 环境可初始化。

## 2. 共同前置条件与采样规则

- ARC Controller/Listener 官方 0.15.0，Runner 2.337.0，Karmada v1.19.0，两个 member K8s v1.37.0，三 Job workflow [`test-arc.yml`](../.github/workflows/test-arc.yml) 都用 `runs-on: karmada-runner`。
- Karmada API kubeconfig、成员 `kind-member1`/`kind-member2` contexts 和 GitHub CLI 权限存在；两个成员 Ready。Listener 恰好一个、Running/Ready，GitHub Session 已建立。
- `scripts/verify-arc.py` 默认持续 `--duration 180` 秒、`--interval 3` 秒；`kubectl --request-timeout=15s`、`gh` 命令网络预算单独记录。**180 秒是采样时间窗口，不是系统完成/恢复的 SLA。**
- 稳定性探针会在 Listener config Secret 和已由 ARC 生成的 Role 上注解并恢复；先等待传播稳定再锁定 Listener UID / config Secret RV；不要直接改写 AutoscalingListener CR 触发父控制器替换。
- 默认脚本要求所有 member 都观测到 Runner，属于**带覆盖要求的综合回归**。功能 T1 的独立验收应允许只记录覆盖不强制逐轮双成员；**实现中必须用显式 CLI 模式选择，不能偷偷关掉旧回归断言**。
- GitHub dispatch 网络超时后先搜索匹配的 Run；不能盲目重派发而制造重复任务。并发运行需识别歧义，不可把其他 Run 当作本轮证据。

## 3. Story → 契约 → 实现 → 测试 → 证据

以下“已验证”只对应 2026-10-15 运行证据，**不是本次文档修改后的新 E2E 测试**。

| Story | 验收目标、关键断言（T1/T2） | 实现入口 | 验证入口和既有证据 | 当前状态 |
|---|---|---|---|---|
| **US-01** 统一标签 | Workflow 三 Job 均请求 `karmada-runner`；GitHub 匹配到 Runner；Job 成功 | [workflow](../.github/workflows/test-arc.yml)、[RunnerSet](../manifests/04-autoscalingrunnerset.yaml)、Listener | [Run 38667994949](https://github.com/pkking/arc-karmada-test/actions/runs/38667994949)、`verify-arc.py --dispatch` | T1 已验证 |
| **US-02** 生命周期 | 任务需求引出 ER/Runner Pod/JIT；执行后本轮临时对象被回收 | [RunnerSet](../manifests/04-autoscalingrunnerset.yaml)、[Controller 启动脚本](../scripts/run-arc-controller.sh)、官方 ARC | [运行验证](arc-0.15.0-validation.md)、[本轮收尾证据](evidence/arc-0.15.0-final-state.json) | T1 已验证本轮 |
| **US-03** 多成员执行 | T1：Job 成功；T2：member1 和 member2 分别定向运行成功。**不得断言每轮必须 2:1** | [传播策略](../manifests/02-propagation-policies.yaml)、Karmada 上游 | [2:1](evidence/arc-0.15.0-multicluster.json)、[3:0 覆盖不足](evidence/arc-0.15.0-single-cluster-coverage-failed.json)、[member2 定向](evidence/arc-0.15.0-directed-member2.json) | T1 和两个成员实际执行能力有既有证据；完整 T2（两侧分别定向）尚需补 member1 定向演练；T3 未验证 |
| **US-04** JIT 依赖 | 每个 Runner 引用的 JIT Secret 在其执行成员可见，Runner Ready；短暂 FailedMount 可恢复；结束后删除 | [RunnerSet](../manifests/04-autoscalingrunnerset.yaml)、[propagateDeps](../manifests/02-propagation-policies.yaml) | `verify-arc.py` 采样字段 `readyWithSecrets`、[运行验证](arc-0.15.0-validation.md) | T1 已验证 |
| **US-05** Listener 认证 | Listener 访问 Karmada API，Role 事件反复调谐后 UID/config Secret RV 稳定，Token/CA/RBAC 正确；不包含自动轮换验证 | [RBAC](../manifests/03-rbac.yaml)、[Token](../scripts/setup-karmada-token.sh)、[RunnerSet](../manifests/04-autoscalingrunnerset.yaml) | `verify-arc.py` `listenerStabilityPassed`、[定向验证](evidence/arc-0.15.0-directed-member2.json) | T1 已验证 PoC 稳定窗口 |
| **US-06** Pod 状态 | Karmada 控制面能看到 Runner 容器名 `runner`、状态和 Ready 条件；当前仅单目标成员 | [Lua Interpreter](../manifests/01-karmada-interpreter.yaml) | `verify-arc.py` `namedStatusOnControlPlane`、[验证记录](arc-0.15.0-validation.md) | T1 已验证；多副本聚合未验证 |
| **US-07** 任务清理 | 按本轮 Runner UID/名称盘点控制面和成员 Pod/ER/JIT，及关联 Binding/Work，无本轮残留；遗留 ERS Finalizer 独立保留 | ARC/Karmada 官方生命周期、[验证工具](../scripts/verify-arc.py) | [最终状态](evidence/arc-0.15.0-final-state.json)、[运行验证](arc-0.15.0-validation.md) | T1 已验证本轮；非全环境零残留 |
| **US-08** NPU | NPU 节点/Device Plugin/Runner Pod 资源请求匹配，真实 GitHub NPU Job 成功，资源释放 | **目前没有完成的 NPU Runner 实现**；目标依赖成员 K8s/设备插件 | 无本仓库 NPU E2E 证据 | **待验证，不参与 P0 通过率** |

上述每个 Story 的输入、异常分支及恢复责任详见[第 4 章](KARMADA-ARC-SUBSYSTEM-DESIGN.md#4-story-设计)和[实现契约第 7 节](IMPLEMENTATION-CONTRACT.md#7-异步故障和恢复契约)。

## 4. T0 静态 / T1 功能 / T2 定向执行入口

~~~bash
# T0：本地离线单测；新资源契约静态检查与现有 verify-arc 逻辑测试
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
bash -n scripts/*.sh
# 若存在真实 Karmada API，再补充 server-side dry run，而非用 mock 代替
kubectl --kubeconfig="$HOME/karmada-data/karmada-apiserver.config" apply --dry-run=server -f manifests/

# T1：功能验收，仍记录 clusterCoveragePassed，但不因单轮无覆盖而判定 Job 失败
python3 scripts/verify-arc.py --dispatch --coverage-mode report-only --duration 180 \
  --output /tmp/arc-015-validation/t1-functional.json

# 兼容已有综合回归：显式强制要求本轮两个成员均观测 Runner
python3 scripts/verify-arc.py --dispatch --coverage-mode require --duration 180 \
  --output /tmp/arc-015-validation/coverage-experiment.json

# T2：先由人工审阅后临时将 Runner PropagationPolicy 的 clusterAffinity 定向到 member1
# 接着以 --required-contexts kind-member1 运行并留存证据；恢复后对 member2 依次重复。
# 不能让两个定向测试并发运行，也不能忘记恢复原双成员 placement。
python3 scripts/verify-arc.py --dispatch --required-contexts kind-member1 --coverage-mode require \
  --output /tmp/arc-015-validation/t2-member1.json
~~~

**现有证据范围：** member1 已在正常工作流中实际执行，member2 已在定向运行中实际执行；尚无单独的 member1 定向验收记录，因此严格按本文件的 T2 定义，完整双向定向矩阵**仍需补测**。

**T2 member2** 同样使用 `--required-contexts kind-member2`，结束必须恢复 `clusterNames: [member1, member2]`，并采集恢复后的稳定性/最终状态。若没有凭证、集群或 Runner 镜像，这些命令**不可假定已执行成功**。

## 5. 异常与失败归因标准

| 观测/断言 | 失败归类 | 不能做的事 |
|---|---|---|
| GitHub Job 无法成功、Runner 注册失败 | **T1 功能失败** | 以另一轮成功或屏蔽断言代替 |
| 三任务成功，但都在 member1（3:0） | T1 可成功；**单轮双成员覆盖实验失败** | 将覆盖失败混同为 Job 失败，或假称均衡 |
| 分别定向 member1/member2 中有一侧不能完成 Job | **T2 成员可执行失败** | 以一次随机跨成员分配替代 |
| JIT Secret 晚到产生瞬时 FailedMount，最终 Job 正常、凭证同成员 | 合法的异步过渡，需要保存事件/时序 | 默认认定为原子传播 |
| 控制面丢失 Runner 容器名称或 Ready Conditions | **T1 状态/生命周期失败** | 删除 Lua 而不验证版本行为 |
| Cleanup 超时、Finalizer 阻塞 | T1 本轮残留失败；历史遗留另行分类 | 强清 Finalizer 让脚本通过 |
| Token 失效、TLS/SAN 不匹配或权限不足 | 身份/网络失败 | 提升到 admin 绕过问题 |
| 统计分摊指标未定义 | **T3 未验证** | 将单轮覆盖作为容量均衡承诺 |

## 6. 验收证据格式与完成条件

- 每次运行保存执行基线（工具、版本/镜像 digest、目标仓库、成员状态、时间窗口）、脱敏 JSON、真实 GitHub Run URL、对应 Story、实际测试命令、断言与失败分类。
- `verify-arc.py` 已生成 `startedAt`、`finishedAt`、`requiredContexts`、`samples`、`runners`、`runId`、`workflow`、`listenerUID`、`configSecretResourceVersion`、`clusterCoveragePassed`、`cleanupPassed` 等字段。报告中不存在的字段不得伪造。Binding/Work 最终盘点证据见[final-state](evidence/arc-0.15.0-final-state.json)，不能凭 `verify-arc.py` 的 Pod/Secret/ER 断言冒充完整 Binding/Work 审计。
- **禁止**写入真实 Token、PAT、Secret Data、Kubeconfig、GitHub App 私钥或证书私钥；拒绝未经脱敏的日志提交。
- P0 完成标准：US-01～US-07 都有契约、实现和真实对应证据；US-08 标待验证；T0 检查通过；**另需在干净 Kind 环境完成真实重建并提交新证据**。没有该证据时，Issue #1 不能整体关闭。
