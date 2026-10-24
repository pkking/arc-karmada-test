# ARC + Karmada Secret 传播与 Pod 选择范围专项核查

> 状态：**代码/上游语义核查已完成；真实成员消费者审计和端到端收敛回归未执行**。不以静态推理替代动态验证。对应 Issue #1 的两个专项疑点。安全策略基线暂不修改。

## 1. 已确立的事实

1. 官方 ARC Scale Set 模式中，主 GitHub PAT/App 凭证供管理端进行 GitHub API 交互；EphemeralRunner Controller 取得 JIT 配置，Runner Pod 使用 JIT 配置注册，官方明确说明主 PAT / GitHub App installation token **不再直接传给 Runner Pod**。参考 [GitHub 官方 ARC 说明](https://docs.github.com/en/actions/concepts/runners/actions-runner-controller) 和 [上游 Scale Set Controller 文档](https://github.com/actions/actions-runner-controller/blob/master/docs/gha-runner-scale-set-controller/README.md)。
2. 现有 [RunnerSet](../manifests/04-autoscalingrunnerset.yaml) 的 `spec.githubConfigSecret` 指向控制面 `arc-runners/arc-github-secret`；Runner Pod 模板没有引用此 Secret，而是引用 ARC 动态生成的 JIT Secret。
3. 现有 [PropagationPolicy](../manifests/02-propagation-policies.yaml) 仍将主 Secret 显式复制到 member1/member2；该行为不由 JIT 注册流程自动要求。成员上是否存在额外消费者、上游组件动态注入或其他 Pod 直接读取 Secret 尚**没有实测证据**。
4. 同一 Policy 文件的 Runner Pod selector 未按 labels 限制，当前匹配 `arc-runners` 中的**全部 Pod**；这可能扩大传播范围。
5. [Karmada 传播依赖文档](https://karmada.io/docs/userguide/scheduling/propagate-dependencies/)确认 Pod 引用的 Secret 可通过 `propagateDeps: true` 自动传播；**依赖到达时间不是原子事务**。

**判断**：主 GitHub 凭证复制至两个成员属于可疑的冗余安全面；但仅凭 GitHub 官方行为不能证明这个原型所有运行路径不依赖成员副本，所以不能无实测直接在稳定 Policy 中删除。

## 2. 已交付的可执行审计

代码：[`scripts/audit-secret-consumers.py`](../scripts/audit-secret-consumers.py)。

~~~bash
# READ ONLY；不读取、输出或保存任何 Secret.data
python3 scripts/audit-secret-consumers.py \
  --contexts kind-member1 kind-member2 --namespace arc-runners \
  --output /tmp/arc-015-validation/secret-consumer-audit.json

# 查看拟变更配置（仅供人工审阅；不会自动应用）
cat docs/experiments/propagation-tightening-candidate.yaml
~~~

审计 PodSpec `volumes.secret`、`volumes.projected.sources.secret`、`containers/initContainers/ephemeralContainers.env`、`envFrom`、`imagePullSecrets`；仅输出 Pod 名称、Namespace、引用类型、统计和错误代码。

**限制**：没有 PodSpec 引用仍**不能**排除通过 Kubernetes API 直接读取 Secret 的消费者；也不代表 GitHub Job E2E 正常。审计结果固定标记 `safeToStopPropagation: false`，避免误将消极结果当作授权删除。

## 3. 候选最小权限策略（未激活）

[`docs/experiments/propagation-tightening-candidate.yaml`](experiments/propagation-tightening-candidate.yaml) 是**可回退的实验文件**，不是当前生效清单；不在 `manifests/` 下，正常 `kubectl apply -f manifests/` 不会加载。

- D-01：`arc-secret-propagation` 只显式传播 Runner SA，不再下发主 GitHub Secret；不改变控制面 `AutoscalingRunnerSet.spec.githubConfigSecret` 或 ARC 管理端身份。
- D-02：将 `arc-runner-pod-propagation` 限定为两个稳定 Scale Set labels；需先检查由 ARC 0.15.0 生成的 Runner Pod **真实 label** 与 expected matchLabels 一致，不能仅凭 Listener 的 labels 推断。
- 保留 Listener 定向传播和 `propagateDeps: true`，避免无关变化。

## 4. 上线/回归门槛与回滚（必须按序）

1. 在隔离或排空的 **真实双成员 PoC** 获取基线：两个成员 Ready、Listener UID/config RV、RunnerSet 0.15.0、GitHub Job 三任务成功、JIT 同成员/状态聚合/本轮清理。存档脱敏 JSON。
2. 运行只读审计，另人工检查实际 ARC 生成的 Runner Pod labels、各成员 Pod/SA/Secret 及其 GitHub 访问路径。审阅上游 0.15.0 标签生成逻辑与实际事件，确认候选 Pod selector 全匹配且负向排除无关 Pod。
3. 对比并备份变更前的三个 Policy（**不包含 Secret Data**），确认本次要接管的成员副本归属与 Karmada Work 行为。在测试窗口、没有非目标工作负载的环境执行**已审核**候选：
   ~~~bash
   kubectl --kubeconfig="$KUBECONFIG_KARMADA" apply -f docs/experiments/propagation-tightening-candidate.yaml
   ~~~
4. 分别进行 T1 功能测试、member1/member2 T2 定向测试，核对每次 Pod/JIT 同成员 Ready、控制面状态、三 Job success、清理及 Listener 稳定。**负向断言**：主 GitHub Secret 不再出现在成员；若仍存在，先确认 Karmada 下发/删除生命周期，不直接宣称安全面已缩小。审查无关 Pod 未被传播。
5. 任一环节失败：**恢复旧配置**，不要临时扩权或放宽断言：
   ~~~bash
   kubectl --kubeconfig="$KUBECONFIG_KARMADA" apply -f manifests/02-propagation-policies.yaml
   ~~~
   回滚后重复 T1，保存脱敏错误、对象 UID/RV、恢复后的策略及 GitHub Run。任何历史 Secret 副本清理必须按明确 owner/Work 生命周期审阅，不能以匿名删除替代追溯。
6. 只有上述运行证据齐全、独立安全复核通过，才允许将候选策略升级为基线 Manifest、同步更新实现契约/静态检查，关闭 D-01/D-02。

## 5. 当前进度与禁止事项

| 决策 | 完成 | 未完成 |
|---|---|---|
| D-01 主 GitHub 凭证下发 | 官方使用 JIT 的语义核对；消费者审计程序与测试；候选文件 | 成员真实消费者审计、移除主凭证后的 E2E/负向测试、回滚演练 |
| D-02 Pod selector | 当前广域选择器确认；候选 labels 过滤配置 | 动态 Runner Pod labels 实物确认、负向匹配和双成员执行回归 |

不得将本文当成批准在生产集群直接切换策略的授权；**未取得真实集群访问和 GitHub Runner 授权时，以上运行结论必须保持未验证**。
