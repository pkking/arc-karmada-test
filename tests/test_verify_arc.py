"""Offline guardrails for the runtime verifier; E2E evidence lives in docs/evidence."""
import contextlib
import importlib.util
import io
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "verify_arc", Path(__file__).resolve().parents[1] / "scripts" / "verify-arc.py"
)
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


class CommandTests(unittest.TestCase):
    def test_success_returns_output_and_bypasses_internal_proxy(self):
        result = subprocess.CompletedProcess([], 0, stdout='{"items": []}', stderr="")
        with patch.object(verifier.subprocess, "run", return_value=result) as run:
            self.assertEqual(verifier.get("kubectl", "get", "pods"), {"items": []})
        options = run.call_args.kwargs
        self.assertEqual(options["env"]["NO_PROXY"], "*")
        self.assertEqual(options["env"]["no_proxy"], "*")
        self.assertEqual(options["timeout"], 25)
        self.assertTrue(options["capture_output"])

    def test_github_calls_allow_for_network_latency_without_dispatch_retries(self):
        result = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch.object(verifier.subprocess, "run", return_value=result) as run:
            verifier.command("gh", "workflow", "run", "test-arc.yml")
        self.assertEqual(run.call_args.kwargs["timeout"], 90)
        self.assertEqual(run.call_count, 1)

    def test_error_never_quotes_captured_secret_data(self):
        result = subprocess.CompletedProcess([], 1, stdout="SECRET_STDOUT", stderr="SECRET_STDERR")
        with patch.object(verifier.subprocess, "run", return_value=result):
            with self.assertRaises(RuntimeError) as raised:
                verifier.command("kubectl", "get", "secret", "test")
        self.assertEqual(str(raised.exception), "kubectl failed (exit 1)")
        self.assertNotIn("SECRET", str(raised.exception))


class ArgumentTests(unittest.TestCase):
    def check_rejected_before_cluster_access(self, arguments):
        with patch.object(verifier, "command") as command, patch.object(verifier, "get") as get:
            with patch("sys.argv", ["verify-arc.py", *arguments]):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        verifier.main()
            self.assertEqual(raised.exception.code, 2)
            command.assert_not_called()
            get.assert_not_called()

    def test_zero_duration_is_not_a_successful_stability_test(self):
        self.check_rejected_before_cluster_access(["--duration", "0"])

    def test_zero_interval_is_rejected(self):
        self.check_rejected_before_cluster_access(["--interval", "0"])

    def test_required_context_must_be_observed(self):
        self.check_rejected_before_cluster_access(["--required-contexts", "unknown-member"])


if __name__ == "__main__":
    unittest.main()
