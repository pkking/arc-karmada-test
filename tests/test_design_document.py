"""Offline integrity checks for the one canonical Ascend CI functional design.

These checks validate documentation structure and local links, not the correctness
of third-party API claims or deployed ARC/Karmada runtime behavior.
"""
from pathlib import Path
import re
import unittest


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
                self.assertRegex(self.design, rf"(?m)^### 5\\.{n}\\.{part}$")
            self.assertIn(f"#### 5.{n}.6.1 接口设计", self.design)
            block = self.design.split(f"## 5.{n} ", 1)[1].split("\n## 5.", 1)[0]
            self.assertIn("FMEA", block)

    def test_story_and_requirement_coverage(self):
        for n in range(1, 9):
            self.assertIn(f"US-{n:02d}", self.design)
        for sr in ("SR-01-01", "SR-01-02", "SR-02-01", "SR-02-02",
                   "SR-03-01", "SR-03-02", "SR-04-01", "SR-05-01", "SR-06-01"):
            self.assertIn(sr, self.design)
        self.assertEqual(self.design.count("sequenceDiagram"), 10)

    def test_six_column_implementation_interface_tables(self):
        for n in range(1, 7):
            head = f"#### 5.{n}.6.1 接口设计"
            block = self.design.split(head, 1)[1].split("**开发交付边界**", 1)[0]
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

    def test_design_not_po_c_gated(self):
        self.assertIn("文档完成不要求 PoC", self.design)
        self.assertIn("官方文档", self.design)
        self.assertIn("当前仓库 Manifest 是参考实现", self.design)

    def test_governance_and_readme_are_unified(self):
        target = "docs/ASCEND-CI-FEDERATED-CLUSTER-FUNCTIONAL-DESIGN.md"
        self.assertIn(target, (ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertIn(target, (ROOT / "AGENTS.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
