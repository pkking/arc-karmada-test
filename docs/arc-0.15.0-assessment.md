# ARC 0.15.0 对本仓库 Karmada 方案的影响

本文记录升级前的静态评估（release 发布时间为 2026-10-01，核对及升级实施于 2026-10-15 UTC）。范围：官方 GitHub release/API、完整 tag Git 历史及源码；升级前 README、`scripts/`、`manifests/`。**当时仅做静态核对；下文对旧脚本/RBAC/硬编码名称的描述是迁移前快照。**

**后续运行验证已完成，旧补丁构建入口已移除。当前方案和运行事实以 [README](../README.md) 与 [0.15.0 运行报告](arc-0.15.0-validation.md) 为准。**

## 结论与提交证据

**官方 `gha-runner-scale-set-0.15.0` 同时包含 #4492、#4516；当前 tag 的 Secret 比较已保留外部 metadata，因此可替代本仓库针对该问题的自编译补丁，但不能据此认定 Karmada 方案已开箱即用或完成升级验证。**

- [官方 release](https://github.com/actions/actions-runner-controller/releases/tag/gha-runner-scale-set-0.15.0) / [release API](https://api.github.com/repos/actions/actions-runner-controller/releases/tags/gha-runner-scale-set-0.15.0)：`published_at=2026-10-01T11:30:26Z`，`draft=false`、`prerelease=false`。
- [tag API](https://api.github.com/repos/actions/actions-runner-controller/git/ref/tags/gha-runner-scale-set-0.15.0) 指向 commit [`1945b42820760eb50893c8da77fdfc42cf8dad45`](https://github.com/actions/actions-runner-controller/commit/1945b42820760eb50893c8da77fdfc42cf8dad45)（Prepare 0.15.0 release）。不能仅依据 release 的 `target_commitish=master` 判定版本内容。
- [#4492](https://github.com/actions/actions-runner-controller/pull/4492) / [API](https://api.github.com/repos/actions/actions-runner-controller/pulls/4492)：2026-05-11 合并，merge commit [`081b9ce1ee65a6dc9ca8d2492188f27c06d47e76`](https://github.com/actions/actions-runner-controller/commit/081b9ce1ee65a6dc9ca8d2492188f27c06d47e76)。其作用是引入 Listener config Secret 漂移检测及重启逻辑；**该提交本身仍直接 `maps.Equal` 比较整个 labels/annotations，不能把它单独描述为“忽略 Karmada 注入 metadata 的修复”**。
- [#4516](https://github.com/actions/actions-runner-controller/pull/4516) / [API](https://api.github.com/repos/actions/actions-runner-controller/pulls/4516)：2026-06-09 合并，merge commit [`767e58e4b14aa9d9b1363d0a6148e8d8b6afd4ee`](https://github.com/actions/actions-runner-controller/commit/767e58e4b14aa9d9b1363d0a6148e8d8b6afd4ee)。release 明列此 PR；其 diff 引入基于现存对象合并 labels/annotations 的调谐，同时大改原地升级机制，不能视作一个孤立的小补丁。
- 对两个 merge commit 分别执行 `git merge-base --is-ancestor <merge-sha> 1945b42820760eb50893c8da77fdfc42cf8dad45`，**均返回 0**，已验证确实位于 tag 历史中，不只是 release notes 提及。

### 当前 tag 的实际 Secret 比较

[Listener 调谐源码 L429–460](https://github.com/actions/actions-runner-controller/blob/1945b42820760eb50893c8da77fdfc42cf8dad45/controllers/actions.github.com/autoscalinglistener_controller.go#L429-L460) 先合并现存与期望 metadata，再与现存值比较；Data 使用 `maps.EqualFunc(..., bytes.Equal)`，有差异才 Patch。

[合并函数 L1080–1117](https://github.com/actions/actions-runner-controller/blob/1945b42820760eb50893c8da77fdfc42cf8dad45/controllers/actions.github.com/resourcebuilder.go#L1080-L1117) 保留 base labels/annotations，期望值同名覆盖；labels 受 `ExcludeLabelPropagationPrefixes` 过滤。故“额外、不冲突且未被排除”的 Karmada metadata 不会仅因存在而被反复删除。**这不是忽略所有 metadata：同名字段争抢、排除前缀、Secret 内容变化，以及 Karmada 自身写入 ResourceVersion 的影响仍需实测。**

## 其他直接相关变化

| 变化 | 对本方案的含义 |
| --- | --- |
| [#4516 原地升级](https://github.com/actions/actions-runner-controller/pull/4516) | AutoscalingRunnerSet 与 EphemeralRunnerSet 一对一；无关 Listener 的变更不再重建 Listener；Runner spec 更新会处理空闲 Runner。PR 明示 breaking change，并移除旧 upgrade strategy。 |
| [#4557](https://github.com/actions/actions-runner-controller/pull/4557)、[#4583](https://github.com/actions/actions-runner-controller/pull/4583) | 集合级 Runner 数量状态改用 metrics，移除对应 status/printer columns；不能沿用旧 CR 状态字段做验收或监控。 |
| [#4635](https://github.com/actions/actions-runner-controller/pull/4635)、[#4680](https://github.com/actions/actions-runner-controller/pull/4680)、[#4695](https://github.com/actions/actions-runner-controller/pull/4695) | Listener Pod 改为直接漂移比较，并修复 Patch 恢复、空集合漂移；需回归成员集群默认化/注入字段是否造成循环重建，而非只测 Secret。 |
| [#4686](https://github.com/actions/actions-runner-controller/pull/4686) | 修复生成的 controller RBAC 的 Patch 权限；本地手写 RBAC 不能直接当作新版完整权限集。 |
| [#4558](https://github.com/actions/actions-runner-controller/pull/4558) | Listener Kubernetes client QPS/Burst 可配置；面向 Karmada API 的负载需独立评估，不是自动解决多集群限流。 |

以上 PR 均见官方 release；本节是相关性评估，不代表已在 Karmada 验证其运行效果。

## 仍需保留、重新核对的 Karmada 配置

1. **Pod 状态回传**：保留 `manifests/01-karmada-interpreter.yaml`，确认 `containerStatuses[].name`、conditions、退出状态完整回传。未核实当前所用 Karmada 版本是否已不再需要该解释器；ARC release 不能证明 Karmada 聚合问题已修复。
2. **Listener 跨面连接**：`04-autoscalingrunnerset.yaml` 的 API 地址、TLS SAN/CA、控制面 token 挂载仍需配置。[tag scaler 源码](https://github.com/actions/actions-runner-controller/blob/1945b42820760eb50893c8da77fdfc42cf8dad45/cmd/ghalistener/scaler/scaler.go#L60-L80) 仍调用 `rest.InClusterConfig()`。`setup-karmada-token.sh` 请求的十年 token 不等于服务端保证十年有效；应规划最小权限及轮换。
3. **传播与依赖**：保留单 Listener 定向传播及 Runner Pod `propagateDeps: true`，验证 JIT Secret 与 Pod 同集群可用、结束后均回收；不把 README 的“原子下发/Zero Leaks”作为本次实测结论。`02-propagation-policies.yaml` 的 Listener/Secret/SA 是**精确硬编码名称** `karmada-runner-fdfdcc69-*`，不是前缀匹配；新版生成名称需重新获取并更新。Runner SA、namespace 在成员集群是否存在也需检查，现有清单未显式传播该 Runner SA。
4. **权限**：对照 [0.15.0 官方 controller RBAC](https://github.com/actions/actions-runner-controller/blob/1945b42820760eb50893c8da77fdfc42cf8dad45/config/rbac/role.yaml)，为实际 manager 身份、Listener 控制面身份授权。当前 `03-rbac.yaml` 只绑定 `arc-systems/default`，Pod 缺 `patch/list/watch`，也不含 ARC CR 权限；不能独立代表完整控制器权限。

## 迁移建议

- 先在隔离环境固定以上 tag/SHA，使用同版本 **0.15.0 controller、Listener 镜像及 CRD/chart**；不用漂移的 master 或继续伪装 `build.Version=0.14.2`。核对当时的旧启动脚本优先复用未知来源 `arc-manager-fixed`，且固定 Listener 来源镜像 `:0.14.2`，不能直接用于验证纯 0.15.0（迁移后已改为运行官方 Docker 镜像）。
- **旧脚本 flags 未发现被整体移除**：[tag main.go](https://github.com/actions/actions-runner-controller/blob/1945b42820760eb50893c8da77fdfc42cf8dad45/main.go#L129-L172) 仍注册 `--auto-scaling-runner-set-only`、`--github-token`、`--metrics-addr`、`--health-probe-bind-address`、`--listener-metrics-addr`；但该模式不创建旧 GitHub client，scale-set 认证仍应检查 `spec.githubConfigSecret`，不能以脚本 PAT 参数替代。`--kubeconfig` 不在 main.go 显式注册，配置通过 `ctrl.GetConfigOrDie()` 获取；依赖层 flag 的实际解析尚未运行验证，应以目标二进制 `--help` 再确认，不能仅因 main.go 未列出就断言已删除。
- **必须同步审查/升级 CRD 与 RBAC，而非只换二进制**：tag 相对 0.14.2 的四个 [controller chart CRD](https://github.com/actions/actions-runner-controller/tree/1945b42820760eb50893c8da77fdfc42cf8dad45/charts/gha-runner-scale-set-controller/crds) 均有结构变更。备份现有 CR/CRD，审阅 schema diff，先在测试控制面应用匹配 CRD 和权限；不要假定现有 `crds/` 或一次 Helm 升级必然已更新它们。
- 暂停/排空任务后迁移，避免新旧 controller 同时调谐。验收至少覆盖：注入 Karmada metadata 后 Secret 不持续 Patch、Listener UID 稳定；并发任务跨成员执行及 JIT Secret 可用；Runner template 更新与进行中任务；空闲回收、故障重试及清理。通过前保留旧镜像、配置与备份作为回退依据。

**静态评估阶段的未验证边界（后续实测见运行报告）**：当时未拉取/核验 OCI 镜像 digest、未执行 `--help`、构建/单测/端到端测试，也未确认任何运行中集群版本、权限或 token 有效期。这里确认的是官方 tag 的历史与源码行为，不是官方承诺支持 Karmada，更不是生产升级保证。
