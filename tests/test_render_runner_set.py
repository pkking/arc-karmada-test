"""Contract checks for environment-specific, nonsecret RunnerSet rendering."""
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("render_runner_set",
                                               ROOT / "scripts" / "render-runner-set.py")
render = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(render)


class RenderRunnerSetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (ROOT / "manifests" / "04-autoscalingrunnerset.yaml").read_text(encoding="utf-8")

    def test_per_environment_ip_and_repo_are_replaced_without_other_changes(self):
        changed = render.render(self.template, "https://github.com/example/ci", "172.18.0.2", 32443)
        self.assertIn("githubConfigUrl: https://github.com/example/ci", changed)
        self.assertIn('ip: "172.18.0.2"', changed)
        self.assertNotIn('ip: "172.22.0.4"', changed)
        self.assertIn('value: "32443"', changed)
        self.assertIn("runnerScaleSetName: karmada-runner", changed)
        self.assertIn("automountServiceAccountToken: false", changed)
        self.assertNotEqual(changed, self.template)

    def test_invalid_ip_port_or_url_rejected(self):
        for ip in ("not-an-ip", "abc.example", "127.0.0.1; echo leak"):
            with self.assertRaises(ValueError):
                render.render(self.template, "https://github.com/example/ci", ip, 32443)
        for port in (0, 65536, -1):
            with self.assertRaises(ValueError):
                render.render(self.template, "https://github.com/example/ci", "172.18.0.2", port)
        for url in ("http://github.com/o/r", "https://alice:secret@github.com/o/r"):
            with self.assertRaises(ValueError):
                render.render(self.template, url, "172.18.0.2", 32443)

    def test_drifted_template_is_rejected(self):
        with self.assertRaises(ValueError):
            render.render("metadata:\n  name: another\n", "https://github.com/example/ci",
                          "172.18.0.2", 32443)


if __name__ == "__main__":
    unittest.main()
