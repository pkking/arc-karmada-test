"""Offline, dependency-free checks for contractual PoC invariants.

These guards deliberately validate only a small set of exact strings/sections.
They are NOT a YAML schema validator and do not replace kubectl server dry-run.
"""
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def policies():
    document = read("manifests/02-propagation-policies.yaml")
    result = {}
    for body in re.split(r"(?m)^---\s*$", document):
        match = re.search(r"(?m)^  name: ([\w-]+)$", body)
        if match:
            result[match.group(1)] = body
    return result


class ContractStaticTests(unittest.TestCase):
    def test_all_three_policies_are_explicit(self):
        self.assertEqual(set(policies()), {
            "arc-secret-propagation",
            "arc-listener-propagation",
            "arc-runner-pod-propagation",
        })

    def test_sensitive_base_propagation_has_exact_names_and_conflict_scope(self):
        base = policies()["arc-secret-propagation"]
        self.assertIn("namespace: arc-runners", base)
        self.assertIn("conflictResolution: Overwrite", base)
        self.assertIn("name: arc-github-secret", base)
        self.assertIn("name: karmada-runner-gha-rs-no-permission", base)
        self.assertIn("- member1", base)
        self.assertIn("- member2", base)
        self.assertNotIn("labelSelector:", base)

    def test_listener_only_targets_member1_by_stable_scale_set_labels(self):
        listener = policies()["arc-listener-propagation"]
        self.assertIn("namespace: arc-systems", listener)
        self.assertIn("actions.github.com/scale-set-name: karmada-runner", listener)
        self.assertIn("actions.github.com/scale-set-namespace: arc-runners", listener)
        self.assertIn("name: karmada-token", listener)
        self.assertIn("propagateDeps: true", listener)
        self.assertIn("- member1", listener)
        self.assertNotIn("- member2", listener)

    def test_runner_pods_keep_current_wide_selector_and_async_jit_dependency(self):
        runner = policies()["arc-runner-pod-propagation"]
        self.assertIn("namespace: arc-runners", runner)
        self.assertRegex(runner, r"(?s)resourceSelectors:\s*\n\s*- apiVersion: v1\s*\n\s*kind: Pod\s*\n\s*propagateDeps: true")
        self.assertNotIn("labelSelector:", runner)
        self.assertIn("replicaSchedulingType: Divided", runner)
        self.assertIn("replicaDivisionPreference: Weighted", runner)
        self.assertIn("- member1", runner)
        self.assertIn("- member2", runner)

    def test_runner_set_uses_official_arc_and_least_privilege_runner(self):
        ars = read("manifests/04-autoscalingrunnerset.yaml")
        self.assertIn('app.kubernetes.io/version: "0.15.0"', ars)
        self.assertIn("runnerScaleSetName: karmada-runner", ars)
        self.assertIn("githubConfigSecret: arc-github-secret", ars)
        self.assertIn("minRunners: 0", ars)
        self.assertIn("maxRunners: 5", ars)
        self.assertIn("automountServiceAccountToken: false", ars)
        self.assertIn("image: ghcr.io/actions/actions-runner:2.337.0", ars)
        self.assertIn("secretName: karmada-token", ars)

    def test_listener_rbac_is_scoped_to_expected_actions_resources(self):
        rbac = read("manifests/03-rbac.yaml")
        for value in ('resourceNames: ["karmada-runner"]', 'resources: ["ephemeralrunnersets"]',
                      'resources: ["ephemeralrunners"]', 'resources: ["ephemeralrunners/status"]',
                      'verbs: ["get", "patch"]', 'name: karmada-listener-sa',
                      'namespace: arc-systems'):
            self.assertIn(value, rbac)
        self.assertNotIn("cluster-admin", rbac)

    def test_interpreter_preserves_whole_first_available_status_only(self):
        interpreter = read("manifests/01-karmada-interpreter.yaml")
        self.assertIn("kind: ResourceInterpreterCustomization", interpreter)
        self.assertIn("name: pod-status-interpreter", interpreter)
        self.assertIn("for i = 1, #statusItems do", interpreter)
        self.assertIn("desiredObj.status = statusItems[i].status", interpreter)
        self.assertIn("break", interpreter)

    def test_listener_token_expiry_not_misrepresented_as_automatic(self):
        script = read("scripts/setup-karmada-token.sh")
        self.assertIn('TOKEN_DURATION="${TOKEN_DURATION:-24h}"', script)
        self.assertIn('create token "${SA_NAME}" --duration="${TOKEN_DURATION}"', script)
        self.assertIn("secret generic karmada-token", script)
        design = read("docs/ASCEND-CI-FEDERATED-CLUSTER-FUNCTIONAL-DESIGN.md")
        self.assertIn("平台必须规定 Listener 如何加载更新后的令牌或证书", design)
        self.assertIn("仅修改 Kubernetes Secret 不能证明 Listener 一定会自动使用新凭证", design)

    def test_real_runner_jobs_are_manual_only(self):
        workflow = read(".github/workflows/test-arc.yml")
        self.assertIn("workflow_dispatch:", workflow)
        # Do not enqueue three self-hosted jobs for every documentation commit.
        self.assertNotRegex(workflow, r"(?m)^  push:")

    def test_security_candidate_is_not_active_manifest(self):
        stable = read("manifests/02-propagation-policies.yaml")
        candidate = read("docs/experiments/propagation-tightening-candidate.yaml")
        self.assertIn("name: arc-github-secret", stable)
        self.assertNotIn("name: arc-github-secret", candidate)
        self.assertIn("labelSelector:", candidate)
        self.assertNotIn("labelSelector:", policies()["arc-runner-pod-propagation"])
        self.assertIn("DO NOT APPLY BY DEFAULT", candidate)

    def test_readme_secret_creation_avoids_token_in_command_line(self):
        readme = read("README.md")
        self.assertIn("--from-file=github_token=", readme)
        self.assertNotIn("--from-literal=github_token=", readme)

    def test_stories_and_npu_design_in_single_official_document(self):
        design = read("docs/ASCEND-CI-FEDERATED-CLUSTER-FUNCTIONAL-DESIGN.md")
        for i in range(1, 9):
            self.assertIn(f"US-{i:02d}", design)
        self.assertIn("SR-06-01", design)
        self.assertIn("K8S-3", design)
        self.assertIn("文档完成不要求 PoC", design)
        self.assertIn("ASCEND-CI-FEDERATED-CLUSTER-FUNCTIONAL-DESIGN.md", read("AGENTS.md"))


if __name__ == "__main__":
    unittest.main()
