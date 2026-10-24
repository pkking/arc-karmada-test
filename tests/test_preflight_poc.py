"""Deterministic readiness contract checks, with no live cluster calls."""
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch
import subprocess


SPEC = importlib.util.spec_from_file_location(
    "preflight_poc", Path(__file__).resolve().parents[1] / "scripts" / "preflight-poc.py"
)
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def member(name, ready=True):
    return {"metadata": {"name": name},
            "status": {"conditions": [{"type": "Ready", "status": "True" if ready else "False"}]}}


def pod(ready=True, image="ghcr.io/actions/gha-runner-scale-set-controller:0.15.0"):
    return {"spec": {"containers": [{"name": "listener", "image": image}]},
            "status": {"conditions": [{"type": "Ready", "status": "True" if ready else "False"}]}}


class PreflightTests(unittest.TestCase):
    def test_two_members_and_one_listener_are_ready(self):
        result = preflight.evaluate_state(
            {"items": [member("member1"), member("member2")]},
            ["member1", "member2"], {"items": [pod()]},
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["listenerCount"], 1)

    def test_missing_cluster_is_not_ready(self):
        result = preflight.evaluate_state(
            {"items": [member("member1")]}, ["member1", "member2"], {"items": [pod()]},
        )
        self.assertFalse(result["passed"])
        self.assertIn("missing-member:member2", result["errorCodes"])

    def test_unready_member_and_listener_do_not_pass(self):
        result = preflight.evaluate_state(
            {"items": [member("member1", ready=False), member("member2")]},
            ["member1", "member2"], {"items": [pod(ready=False)]},
        )
        self.assertFalse(result["passed"])
        self.assertIn("member-not-ready:member1", result["errorCodes"])
        self.assertIn("listener-not-ready", result["errorCodes"])

    def test_wrong_image_and_duplicate_listener_are_not_ready(self):
        result = preflight.evaluate_state(
            {"items": [member("member1"), member("member2")]},
            ["member1", "member2"], {"items": [pod(image="wrong:v1"), pod()]},
        )
        self.assertFalse(result["passed"])
        self.assertIn("listener-count:2", result["errorCodes"])
        self.assertIn("listener-image-mismatch", result["errorCodes"])

    def test_command_failures_hide_stderr(self):
        fake = subprocess.CompletedProcess([], 1, stdout="", stderr="private-api-token")
        with patch.object(preflight.subprocess, "run", return_value=fake):
            with self.assertRaises(RuntimeError) as ctx:
                preflight.command_json(["kubectl", "get", "clusters"])
        self.assertNotIn("private-api-token", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
