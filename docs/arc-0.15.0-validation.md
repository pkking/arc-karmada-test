# 官方 ARC 0.15.0 + Karmada 运行验证

日期：2026-10-15 UTC。使用现有本地 Kind 环境和真实 GitHub Actions 任务；不是仅做源码比对或 dry-run。

## 结论

**本方案可以不修改 ARC 源码，使用官方 0.15.0 Controller/Listener 执行任务。** Karmada 的状态解释器、Listener 跨控制面认证与依赖分发仍需保留。

已观察到同一 runner label 的三任务在两个成员集群执行（2:1）、JIT Secret 同集群可用、完整容器名称状态回传，以及本轮 Pod/ER/JIT Secret 的结束清理。强化回归通过 Role 事件反复触发真实 Listener 调谐，稳定窗口内 config Secret ResourceVersion 与 Listener UID 保持不变。

**不承诺单轮自动均匀分摊**：另一轮三任务均落在 member1，工作流成功但“两成员覆盖”断言失败；该结果完整保留，不算全套回归通过。对每个独立 Pod 配置 `Divided / Weighted` 不是全局 Pod spread 约束。另做 member2 定向运行，只用于验证该成员执行能力，不作为均匀分摊或故障自动切换的证据。

## 实际环境与制品

| 项目 | 实际值 |
| --- | --- |
| Karmada | v1.19.0，Push 模式，member1/member2 Ready |
| Kubernetes | 两成员均 v1.37.0 |
| Controller / Listener | `ghcr.io/actions/gha-runner-scale-set-controller:0.15.0` |
| 官方镜像 RepoDigest | `sha256:162dfb5bfbda9e7e49fbed67d428994c9ced55b41a37a18b75b46dab0aa98d37` |
| Runner | `ghcr.io/actions/actions-runner:2.337.0` |
| Runner RepoDigest | `sha256:e5496277be5d09bc968b3d64911b74e219ac4a3f2edce956a3ecf9271bea1ef4` |
| CRD | 官方 tag `gha-runner-scale-set-0.15.0`，四个文件原样同步，见 [来源及 SHA256](../crds/README.md) |
| 入口 | `runs-on: karmada-runner`，min=0 / max=5，仓库 `pkking/arc-karmada-test` |
| Controller 运行方式 | 官方 Docker 镜像，host networking，只读挂载 flatten kubeconfig；日志 build version=0.15.0 |

未构建本地镜像，未编译 Go，未调用旧 `arc-manager-fixed`，未对 ARC 源码应用补丁。原 `$HOME/actions-runner-controller` 下的个人资产和旧二进制未被擅自删除，但新脚本不读取它们或其中的 Token。

## 执行记录与证据

| 运行 | 结果与边界 | 脱敏证据 |
| --- | --- | --- |
| [38667994949](https://github.com/pkking/arc-karmada-test/actions/runs/38667994949) | 三 job 全部 success；member1=2、member2=1；29 个采样，窗口约 180 秒；注入 Secret metadata 后无持续变化，本轮清理通过。此轮尚未主动反复触发调谐。 | [首轮 JSON](evidence/arc-0.15.0-multicluster.json) |
| [38668497983](https://github.com/pkking/arc-karmada-test/actions/runs/38668497983) | 工作流成功，但强化探针直接修改自动生成的 AutoscalingListener CR，被其父控制器判为漂移并正常替换，探针失败。该探针不是 Secret 修复的正确验证接口，随后改为注解 ARC 所 watch 的 Role；不以此轮证明稳定性。 | [失败探针 JSON](evidence/arc-0.15.0-probe-failed.json) |
| [38668853059](https://github.com/pkking/arc-karmada-test/actions/runs/38668853059) | 修正为 Role 事件后，28 个采样约 180 秒，UID/Secret RV 稳定，三 job success、JIT/状态证据齐全、Pod/ER/JIT 回收；但三个 Runner 都在 member1，默认两成员覆盖断言失败。 | [覆盖不足 JSON](evidence/arc-0.15.0-single-cluster-coverage-failed.json) |
| [38669390837](https://github.com/pkking/arc-karmada-test/actions/runs/38669390837) | member2 定向派发请求已被 GitHub 接受，但 CLI 响应超过旧 25 秒超时，采样未开始。保留失败，不当作验证通过；脚本改为给 gh 90 秒网络预算，超时后只发现已派发 run、不盲目重复派发。 | [网络超时 JSON](evidence/arc-0.15.0-dispatch-timeout.json) |

最终 member2 定向验证：[38669630663](https://github.com/pkking/arc-karmada-test/actions/runs/38669630663)，**3/3 job success，三个 Runner 均在 member2**。19 次采样、约 120 秒稳定窗口内，Role 事件反复触发调谐，Secret RV `3210525` 与 Listener UID `952c0301-0407-47c9-ba47-b17eb9954a44` 均不变；就绪 Runner/JIT 和控制面容器名称证据齐全，本轮清理通过，两处测试 annotation 均恢复。见 [定向验证 JSON](evidence/arc-0.15.0-directed-member2.json)。

定向验证结束后已恢复 `clusterNames: [member1, member2]`。使用最终脚本再做 30 秒、9 个采样的稳定性检查，结果通过，见 [最终稳定性 JSON](evidence/arc-0.15.0-final-stability.json)。最终盘点确认观察到的 Runner Pod/ER/JIT Secret、关联 ResourceBinding/Work 在控制面及成员均无残留，见 [最终状态 JSON](evidence/arc-0.15.0-final-state.json)；历史 ERS 残留另列于限制项，没有通过清空 finalizer 来伪造本轮自动清理。

离线护栏单测 **6/6 通过**；Shell 语法、`git diff --check`、全部 manifests 的 server dry-run 通过；四个 CRD 均逐字节匹配官方 release commit 的文件。官方 Controller 继续运行，未启动旧补丁版控制器。

所有 JSON 只保存 Pod/Secret 名称、UID/RV、状态和工作流信息，不保存 Secret Data、kubeconfig 或 Token。原始备份/日志在本机 `/tmp/arc-015-validation/`，不提交凭据备份。

## 从旧方案迁移时实际修正的内容

1. **移除补丁构建链**：`run-arc-manager.sh` 不再寻找 patched binary、读取 PAT 文件、checkout master 或注入伪造版本；`run-arc-controller.sh` 保留为兼容 symlink。
2. **升级四个 CRD**：使用 server-side apply。遇到旧 `kubectl-create` 字段所有权冲突后，确认这四个 CRD 的 release schema 再用 `--force-conflicts --field-manager=arc-release-upgrade` 接管；不是全局 force。
3. **补全版本标签**：手写 RunnerSet 原来缺少 `app.kubernetes.io/version`，官方 Controller 会删除不兼容资源。当前明确为 `0.15.0`；旧资源按版本规则被清理，新资源重新声明，不代表完成无中断滚动升级。
4. **固定 Listener 控制面身份**：旧 `karmada-token` 的 subject 指向 ARC 自动生成的旧 SA；升级删除 SA 后令牌会失效。改为固定 `karmada-listener-sa`，并按官方 `rulesForListenerRole` 授予最小权限，不授予 namespace admin。Token 默认请求 24h，需定期轮换。
5. **去除 Listener hash 硬编码**：Pod/SA 用 scale-set labels 匹配，config Secret 由 `propagateDeps` 跟随 Pod；实测可同步依赖。新策略无须手填 `fdfdcc69`。
6. **明确 Runner SA 的传播**：旧成员有手工创建的同名 SA，默认 Abort 使 binding FullyApplied=False；基础策略对两个显式命名资源使用 Overwrite 接管后 FullyApplied=True。Runner 关闭 SA Token 自动挂载。
7. **镜像获取环境问题**：成员 containerd 的旧代理指向 `127.0.0.1:10808`，导致 ErrImagePull。通过宿主机拉取并按 linux/amd64 导入官方镜像解决；没有把此环境问题记作 ARC/Karmada 功能修复。Secret 初次异步传播时也出现过短暂 FailedMount，后恢复，不宣称分发是原子事务。

## 可复跑命令

在配置完毕、Listener Ready、`gh` 可访问 GitHub 后：

```bash
bash -n scripts/*.sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
kubectl --kubeconfig="$HOME/karmada-data/karmada-apiserver.config" apply --dry-run=server -f manifests/
python3 scripts/verify-arc.py --dispatch --duration 180 \
  --output /tmp/arc-015-validation/runtime.json
```

默认 `--dispatch` 要求在全部 `--contexts` 中观察到 Runner；若独立 Pod 都被调到一个成员，脚本会因覆盖不足退出非零，不能据此断言任务执行失败。定向测试时先临时把 Runner policy 的 clusterAffinity 改为 member2，再显式使用 `--required-contexts kind-member2`，结束后恢复原先的双成员 placement。脚本不会替你改变调度策略或关闭覆盖断言。

稳定性探针会在 config Secret 注入测试 annotation，并在 ARC 生成的 Role 上反复更新另一 annotation 以触发调谐；完成/失败后尝试恢复二者。外部 Secret 写入改变 ResourceVersion，可能引起一次合法 Listener 替换，所以先等待稳定再锁定 UID。**不应直接注解自动生成的 AutoscalingListener CR 来做本探针。**

Controller `/healthz` 与 `/readyz` 均返回 `ok`，见 [健康检查](evidence/arc-0.15.0-health-check.txt)。本机 Docker daemon 为 Docker Desktop，Controller 的 host 网络在 Docker VM 内；直接从 Linux 宿主 localhost 探测曾超时，改为在相同 Docker host 网络的独立 Python 容器中探测成功，未放宽服务健康标准。

## 未覆盖与已知限制

- 没有证明生产级容量均衡、集群宕机自动迁移、网络隔离恢复、正在运行任务的升级/模板更新和长时间负载稳定性。
- 本次是排空后的 PoC 重新部署，不是生产滚动升级演练。
- 保留 Pod Lua 解释器；未验证删除它后的 Karmada 1.19 默认聚合器是否已经足够。
- 自动化脚本的清理断言针对本轮 Runner Pod、ER、观察到的 JIT Secret；收尾另盘点其 ResourceBinding/Work。空闲的当前 ERS/Listener/基础 Secret 应保留，不计泄漏；不是对所有历史对象的 Zero Leaks 保证。
- 旧环境还留有 `karmada-runner-wvfql` ERS，带旧 `ephemeralrunner.actions.github.com/finalizer`，owner UID 非当前 RunnerSet；没有子 Runner，却不能由新版正常完成旧 finalizer 清理。本次不擅自清空未知历史 finalizer，也不称整个环境“Zero Leaks”。
- 固定 Listener 控制面 Token 尚无自动轮换；Controller 仍用管理员 kubeconfig。持久化服务、最小权限 Controller 和凭据生命周期治理仍需另做。
- 未验证 Helm 实际安装/渲染。本仓库使用手写 RunnerSet、官方镜像及对应原版 CRD，不把静态参考 Helm 模板说成 Helm 部署已测试。
