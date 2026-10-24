"""Contract tests for read-only ARC secret consumer audit."""
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch
import subprocess


SPEC = importlib.util.spec_from_file_location(
    "audit_secret_consumers", Path(__file__).resolve().parents[1] / "scripts" / "audit-secret-consumers.py"
)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class SecretConsumerAuditTests(unittest.TestCase):
    def test_references_cover_every_pod_spec_secret_use(self):
        pod = {"spec": {
            "volumes": [
                {"name": "v", "secret": {"secretName": "arc-github-secret"}},
                {"name": "p", "projected": {"sources": [{"secret": {"name": "arc-github-secret"}}]}},
            ],
            "imagePullSecrets": [{"name": "arc-github-secret"}],
            "initContainers": [{"envFrom": [{"secretRef": {"name": "arc-github-secret"}}]}],
            "containers": [{"env": [{"name": "AUTH", "valueFrom": {
                "secretKeyRef": {"name": "arc-github-secret", "key": "github_token"}}}]}],
            "ephemeralContainers": [],
        }}
        self.assertEqual(
            audit.secret_references(pod, "arc-github-secret"),
            ["env", "envFrom", "imagePullSecret", "projected", "volume"],
        )

    def test_other_secret_and_jit_do_not_count(self):
        pod = {"spec": {"containers": [{"env": [
            {"name": "RUNNER", "valueFrom": {"secretKeyRef": {"name": "jit-secret"}}},
        ]}]}}
        self.assertEqual(audit.secret_references(pod, "arc-github-secret"), [])

    def test_output_never_includes_pod_spec_or_secret_values(self):
        pods = {"items": [{
            "metadata": {"name": "runner-1", "namespace": "arc-runners"},
            "spec": {"containers": [{
                "env": [
                    {"name": "DANGER", "value": "fake-secret-should-never-be-copied"},
                    {"name": "AUTH", "valueFrom": {"secretKeyRef": {
                        "name": "arc-github-secret", "key": "github_token"}}},
                ]
            }]},
        }]}
        actual = audit.pod_reference_report(pods, "arc-github-secret")
        self.assertEqual(actual, [{
            "namespace": "arc-runners",
            "pod": "runner-1",
            "referenceTypes": ["env"],
        }])
        self.assertNotIn("fake-secret", str(actual))
        self.assertNotIn("github_token", str(actual))

    def test_api_error_does_not_leak_stderr(self):
        result = subprocess.CompletedProcess([], 1, stdout="", stderr="fake-sensitive-token")
        with patch.object(audit.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "kubectl failed"):
                audit.kubectl_json("kind-member1", "arc-runners", "pods", 20)
        # stderr was suppressed; only command/exit status is exposed.


if __name__ == "__main__":
    unittest.main()
