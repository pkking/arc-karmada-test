"""Offline integrity checks for the one canonical Ascend CI functional design.

These checks validate documentation structure and local links, not the correctness
of third-party API claims or deployed ARC/Karmada runtime behavior.
"""
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "ASCEND-CI-FEDERATED-CLUSTER-FUNCTIONAL-DESIGN.md"


class CanonicalDesignDocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = DOC.read_text(encoding="utf-8")

    def test_exact_title_and_template_chapters(self):
        self.assertTrue(self.design.startswith("# 昇腾CI 联邦集群功能设计文档"))
        for chapter in ("# 1 功能域背景与目的", "# 2 功能域概述",
                        "# 3 功能域总体方案", "# 4 功能域规格变更与设计",
                        "# 5 功能实现设计"):
            self.assertEqual(self.design.count("\n" + chapter), 1)

    def test_six_complete_developer_function_units(self):
        for n in range(1, 7):
            for part in ("1 功能概述", "3 实现思路", "4 设计思路",
                         "5 用户接口设计", "6 实现接口设计", "9 DFX分析"):
                self.assertRegex(self.design, rf"(?m)^### 5\.{n}\.{part}$")
            self.assertIn(f"#### 5.{n}.6.1 接口设计", self.design)
            block = self.design.split(f"## 5.{n} ", 1)[1].split("\n## 5.", 1)[0]
            self.assertIn("FMEA", block)

    def test_story_and_requirement_coverage(self):
        for n in range(1, 10):
            self.assertIn(f"US-{n:02d}", self.design)
        for sr in ("SR-01-01", "SR-01-02", "SR-02-01", "SR-02-02",
                   "SR-03-01", "SR-03-02", "SR-04-01", "SR-05-01",
                   "SR-06-01", "SR-06-02", "SR-06-03"):
            self.assertIn(sr, self.design)
        self.assertGreaterEqual(self.design.count("sequenceDiagram"), 8)

    def test_system_requirements_are_scenario_based_and_include_dfx(self):
        section = self.design.split("## 2.4 需求 SR", 1)[1].split("\n## 2.5 ", 1)[0]
        functional = section.split("### 2.4.1 ", 1)[1].split("### 2.4.2 ", 1)[0]
        nonfunctional = section.split("### 2.4.2 ", 1)[1].split("### 2.4.3 ", 1)[0]
        function_ids = re.findall(r"(?m)^\| \*\*(SR-\d\d-\d\d)\*\* \|", functional)
        nfr_ids = re.findall(r"(?m)^\| \*\*(SR-NF-\d\d)\*\* \|", nonfunctional)
        self.assertEqual(function_ids, [
            "SR-01-01", "SR-02-01", "SR-03-01", "SR-03-02",
            "SR-04-01", "SR-05-01", "SR-02-02",
            "SR-06-01", "SR-06-02", "SR-06-03"
        ])
        self.assertEqual(nfr_ids, [
            "SR-NF-01", "SR-NF-02", "SR-NF-03", "SR-NF-04",
            "SR-NF-05", "SR-NF-06", "SR-NF-08"
        ])
        for requirement in ("跨控制面访问安全", "跨成员凭证隔离", "成员故障边界",
                            "跨集群问题定位", "容量与排队性能", "资源使用与成本",
                            "放置策略可维护性"):
            self.assertIn(requirement, nonfunctional)
        for implementation_detail in ("PropagationPolicy", "EphemeralRunnerSet", "Pod.status",
                                      "githubConfigSecret", "ResourceInterpreterCustomization"):
            self.assertNotIn(implementation_detail, functional)
        for code in nfr_ids:
            self.assertIn(code, self.design.split("### 5.8.2 ", 1)[1])
        self.assertIn("| **SR-06-01** |", functional)
        self.assertIn("| **SR-06-02** |", functional)
        self.assertIn("| **SR-06-03** |", functional)
        self.assertNotIn("| **SR-01-02** |", functional)
        self.assertNotIn("| **SR-NF-07** |", nonfunctional)
        self.assertNotIn("| **SR-NF-09** |", nonfunctional)
        self.assertIn("未确定", section)
        self.assertIn("P-SR-04", section)
        for feature in ("FT-01", "FT-02", "FT-03", "FT-04", "FT-05"):
            self.assertIn(feature, self.design)

    def test_federated_features_remain_reader_facing(self):
        overview = self.design.split("## 2.2 核心特性", 1)[1].split("## 2.3 ", 1)[0]
        for feature in ("FT-01", "FT-02", "FT-03", "FT-04", "FT-05"):
            self.assertIn(feature, overview)
        for concept in ("统一入口与资源共享", "可控成员放置",
                        "跨控制面执行", "临时资源回收"):
            self.assertIn(concept, overview)
        for implementation_evidence in ("manifests/", "scripts/", ".github/", ".yaml",
                                        "当前仓库的依据", "实现状态边界", "代码依据"):
            self.assertNotIn(implementation_evidence, overview)
        self.assertIn("统一入口 → 成员选择 → 跨集群执行与状态反馈 → 资源回收", overview)
        self.assertIn("FT-05：SR-06-01～03", self.design)
        self.assertIn("PL-D07", self.design)
        self.assertIn("UC-14", self.design)

    def test_npu_is_a_core_requirement_with_profile_and_gang(self):
        overview = self.design.split("## 2.2 核心特性", 1)[1].split("## 2.3 ", 1)[0]
        self.assertIn("FT-05", overview)
        self.assertIn("Runner Service Resource Profile", self.design)
        self.assertIn("multi-pod-group", self.design)
        self.assertIn("PodGroup", self.design)
        self.assertIn("ProfileUnknown", self.design)
        self.assertIn("ResourceMismatch", self.design)
        self.assertIn("SR-06-01～03", self.design)
        self.assertNotIn("NPU 专项适配为后续扩展", self.design)
        self.assertNotIn("非本期正式 SR", self.design)

    def test_mermaid_diagrams_have_balanced_fences(self):
        self.assertNotIn("§§§", self.design)
        lines = self.design.splitlines()
        fences = [line for line in lines if line.strip().startswith("```")]
        self.assertEqual(len(fences) % 2, 0)
        self.assertGreaterEqual(self.design.count("```mermaid"), 15)

    def test_reserve_node_granularity_and_gang_recovery(self):
        self.assertIn("4.3.6.1 Reserve Node 的保护粒度", self.design)
        self.assertIn("8 卡 Node", self.design)
        self.assertIn("ReservationPartial", self.design)
        self.assertIn("protectedNpuCardSeconds", self.design)
        self.assertIn("| PL-18 |", self.design)
        self.assertIn("| PL-19 |", self.design)
        self.assertIn("| QA-15 ", self.design)
        self.assertIn("| QA-16 ", self.design)
        for fmea in ("F03-14", "F03-15", "F03-16"):
            self.assertIn(fmea, self.design)

    def test_six_column_implementation_interface_tables(self):
        for n in range(1, 7):
            head = f"#### 5.{n}.6.1 接口设计"
            # Stop at the next numbered section: DFX tables use another schema.
            block = re.split(r"(?m)^### ", self.design.split(head, 1)[1], maxsplit=1)[0]
            rows = [l for l in block.splitlines() if l.startswith("| ")]
            self.assertGreaterEqual(len(rows), 2)
            for row in rows:
                self.assertEqual(row.count("|"), 7, f"5.{n}: {row}")

    def test_referenced_images_exist(self):
        paths = re.findall(r"!\[[^]]*\]\(([^)]+)\)", self.design)
        self.assertGreaterEqual(len(paths), 4)
        for relative in paths:
            self.assertFalse(relative.startswith("http"))
            self.assertTrue((DOC.parent / relative).is_file(), relative)

    def test_architecture_svgs_show_target_dispatcher_and_member_volcano(self):
        figures = (
            "federation-subsystem-goal-before-after.svg",
            "overall-subsystem-architecture.svg",
            "federation-layered-architecture.svg",
            "arc-karmada-deployment-architecture.svg",
        )
        for filename in figures:
            svg = DOC.parent / "images" / filename
            root = ET.parse(svg).getroot()
            label_text = " ".join((node.text or "") for node in root.iter())
            self.assertIn("Dispatcher", label_text, filename)
            self.assertIn("Volcano", label_text, filename)
            self.assertNotIn("原型验证", label_text, filename)

    def test_every_diagram_has_caption_below_and_next_line_explanation(self):
        lines = self.design.splitlines()
        image_lines = [i for i, line in enumerate(lines)
                       if re.match(r"!\[[^]]+\]\(images/[^)]+\.svg\)", line)]
        mermaid_ends = []
        inside = False
        for i, line in enumerate(lines):
            if line == "```mermaid":
                inside = True
            elif inside and line == "```":
                mermaid_ends.append(i)
                inside = False
        self.assertFalse(inside)
        self.assertEqual(len(image_lines), 5)
        self.assertGreaterEqual(len(mermaid_ends), 18)
        for i in image_lines + mermaid_ends:
            later = [line for line in lines[i + 1:] if line.strip()]
            self.assertTrue(later[0].startswith('<p align="center">图 '), i)
            self.assertTrue(later[1].startswith("说明："), i)

    def test_design_explains_scenario_without_process_commentary(self):
        # Protect the reader-facing design, not an author's review workflow.
        self.assertIn("vLLM-Omni", self.design)
        self.assertIn("Dispatcher", self.design)
        self.assertIn("Karmada", self.design)
        self.assertIn("ResourceInterpreterCustomization", self.design)
        self.assertNotIn("文档完成不要求 PoC", self.design)

    def test_pre_dispatch_resource_profile_contract(self):
        # A fixed Runner Service supplies a trusted profile before ARC assigns a Job.
        for clause in (
            "同一 Runner Service 在某一 Profile 版本的生效范围内只接纳一种完整的 NPU 资源规格",
            "不同规格应由不同的可信 Runner Service/Profile 承载",
            "默认 ARC 扩容事件不提供可信的逐 Job 需求",
            "唯一 Job→Runner/Binding 关联",
            "ProfileUnknown",
            "放行前的正确性约束",
        ):
            self.assertIn(clause, self.design)

    def test_job_pod_and_gang_boundary_contract(self):
        for clause in (
            "Runner Pod、Job Container Pod 与 NPU Workload Pod/PodGroup",
            "Kubernetes mode 不自动等于多机 Gang 支持",
            "跨节点条件",
            "成员 NPU 工作负载控制器",
            "无需与 CPU Runner 固定同节点",
        ):
            self.assertIn(clause, self.design)
        self.assertIn("W->>V: 创建关联的 NPU Pods 与 PodGroup", self.design)

    def test_status_source_identity_and_stale_ready_contract(self):
        for clause in (
            "LC-08 Ready 来源有效性",
            "Ready --> StatusUnknown",
            "StatusUnknown --> Ready",
            "statusItems",
            "仅增加监控侧 StatusUnknown 不满足 LC-08",
            "F05-04",
            "F05-05",
        ):
            self.assertIn(clause, self.design)
        self.assertNotIn("当前 Lua 能确保旧 Ready 自动失效", self.design)

    def test_governance_and_readme_are_unified(self):
        target = "docs/ASCEND-CI-FEDERATED-CLUSTER-FUNCTIONAL-DESIGN.md"
        self.assertIn(target, (ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertIn(target, (ROOT / "AGENTS.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
