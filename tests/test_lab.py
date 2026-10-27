import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import lab

ROOT = Path(__file__).resolve().parents[1]


class LabSafetyTests(unittest.TestCase):
    def test_explicit_cluster_context_and_kubeconfig(self):
        args = lab.kube_args("get", "pods")
        self.assertEqual(args[args.index("--context") + 1], "kind-ops-lab")
        self.assertEqual(args[args.index("--kubeconfig") + 1], str(ROOT / ".local/kubeconfig"))
        self.assertEqual(args[args.index("--namespace") + 1], "ops-lab")
        self.assertEqual(args[-2:], ["get", "pods"])

    def test_failure_needs_confirmation_before_any_kubectl_call(self):
        result = subprocess.run(
            [sys.executable, "scripts/lab.py", "break-image"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--confirm-lab-failure", result.stderr)

    def test_doctor_reports_missing_tools(self):
        with patch("scripts.lab.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Missing tools: docker, kind, kubectl"):
                lab.doctor()

    def test_rollback_refuses_healthy_image(self):
        healthy = '{"spec":{"template":{"spec":{"containers":[{"image":"ops-lab:1.0.0"}]}}}}'
        with patch("scripts.lab.kube", return_value=healthy) as kube:
            with self.assertRaisesRegex(RuntimeError, "Rollback only allowed"):
                lab.rollback()
            self.assertEqual(kube.call_count, 1)


if __name__ == "__main__":
    unittest.main()
