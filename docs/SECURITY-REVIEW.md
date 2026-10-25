# 原安全设计专项 — 已合并

主 GitHub 凭证与 JIT Secret 最小传播、跨控制面 Token/CA/TLS/RBAC、Pod selector 风险以及轮换责任，见[《昇腾CI 联邦集群功能设计文档》4.4](ASCEND-CI-FEDERATED-CLUSTER-FUNCTIONAL-DESIGN.md#44-sp-04sp-05-身份凭证与跨控制面访问规格设计)、[4.3](ASCEND-CI-FEDERATED-CLUSTER-FUNCTIONAL-DESIGN.md#43-sp-02sp-03-联邦调度与依赖传播规格设计)和[5.4](ASCEND-CI-FEDERATED-CLUSTER-FUNCTIONAL-DESIGN.md#54-f-04-listener-跨控制面认证与访问)。

工程实验与命令仍可参考 [只读 Secret 消费者审计](../scripts/audit-secret-consumers.py) 和 [未启用的策略候选](experiments/propagation-tightening-candidate.yaml)。该候选不是生产生效清单，任何策略部署和安全验证由工程实施任务管理。

此处保留历史链接，不再维护第二份安全设计正文。
