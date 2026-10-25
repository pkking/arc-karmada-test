# Agent 协作规则 — 昇腾CI 联邦集群功能域

## 唯一设计源

正式设计文档只有 [docs/ASCEND-CI-FEDERATED-CLUSTER-FUNCTIONAL-DESIGN.md](docs/ASCEND-CI-FEDERATED-CLUSTER-FUNCTIONAL-DESIGN.md)，标题《昇腾CI 联邦集群功能设计文档》。**禁止重新拆成多个平行的正式架构设计、实现契约、Story 或 DFX 文档。** 原 `KARMADA-ARC-SUBSYSTEM-DESIGN.md`、`IMPLEMENTATION-CONTRACT.md`、`UPSTREAM-SOURCE-MATRIX.md`、`ACCEPTANCE-CONTRACT.md`、`SECURITY-REVIEW.md` 只保留迁移提示；历史版本可查 Git 提交记录。

对本功能域的设计变更，直接修改统一文档的对应章节：

- **第 1 章**：保留原有目的、职责、边界、整体架构与关键约束。
- **第 2 章**：功能域概述；维护 IR → UC → SR → F/Story 的可追踪关系，新增需求不能只写一个技术机制名称。
- **第 3 章**：总体方案、选型、系统元素、逻辑/部署图、领域数据模型与敏感数据销毁；不展开官方上游组件的内部架构。
- **第 4 章**：跨功能的统一规格 SP、现状/目标差异、实现原理和实现设计；规格只在此定义一次。
- **第 5 章**：按每个功能 F-xx 维护概述、实现思路、用例时序、用户接口、实现接口（API/CRD 与字段契约）、DFX/FMEA；至少保持 US-01～US-08 的时序可追踪。

主文档 SVG 图继续保存在 `docs/images/`；可保留 Markdown 内嵌 Mermaid 运行图。**未新增 REST 服务时不得人为生成 Swagger Endpoint**；ARC/Karmada/Kubernetes 现有 CRD 应引用所选版本 OpenAPI Schema 及关键字段约束。

## 设计来源与完成标准

- **U**：仅由上游 GitHub Actions/ARC、Karmada、Kubernetes 的官方组件文档及 API 定义支持的机制。
- **D**：本系统将上游机制集成为跨控制面方案时自行定义的对象、接口、权限、状态和失败处理规则，不得宣称 ARC+Karmada 官方联合认证。
- **P**：设备型号、NPU extended resource 名、API/CA、成员集合、性能 SLO 等尚需选型或业务输入的参数；需给出类型和约束，不能编造数值。
- **设计完成不要求 PoC**、Kind、GitHub Token、真实 Job、T1/T2、硬件 NPU 实测。也不可以拿历史运行数据冒充上游规格。

修改设计时应检查：IR/SR/UC/功能/Story 对应、SP 统一规格引用、接口输入输出与版本、权限/Secret 最小化、异步/故障语义、每个功能的 FMEA、领域数据及销毁规则、上游依据链接。新增需求若触及架构取舍，应明确备选方案与理由；不能静默调整正式设计的安全或调度语义。

## 工程资产与 PoC（独立于设计）

`README.md`、`manifests/`、`scripts/`、`tests/`、`docs/ENGINEERING-TEST-PLAN.md`、`docs/REBUILD-VALIDATION.md`、`docs/arc-0.15.0-validation.md` 是工程实施和历史验证资料，**不成为另一份正式设计说明书**。

实际工程代码的变更需要按对应契约执行静态/单元/集成测试；不得擅自扩大 GitHub 主凭证传播、Pod 选择范围、Karmada RBAC 或提交敏感信息。当前参考配置仍存在主 Secret 分发和全命名空间 Pod 匹配，统一文档第 4 章已标记为与目标设计不同。

## 交付格式

设计变更提交注明：受影响 IR/SR、F/UC/Story、SP、上游文档依据和本系统 D 类决策、接口及 DFX 影响、P 类待选参数。只涉及功能设计时，不需要执行 PoC，亦不要因此阻塞提交。
