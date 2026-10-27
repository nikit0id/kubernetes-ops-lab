import http.client
import subprocess
import sys
import tempfile
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

    def test_verify_waits_for_actual_pod_readiness(self):
        def output(*args, **kwargs):
            if args[:2] == ("get", "nodes"):
                return '{"items": [{}, {}]}'
            if args[:2] == ("get", "deployment/ops-lab"):
                return '{"status":{"availableReplicas":2},"spec":{"template":{"spec":{"containers":[{"image":"ops-lab:1.0.0"}]}}}}'
            return ""

        with patch("scripts.lab.kube", side_effect=output) as kube:
            with patch("scripts.lab.smoke"):
                lab.verify()
        kube.assert_any_call(
            "wait", "--for=condition=Ready", "pods", "-l", "app=ops-lab", "--timeout=120s"
        )

    def test_smoke_retries_temporary_remote_disconnect(self):
        from unittest.mock import MagicMock

        state = ROOT / ".local"
        state.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=state) as directory:
            process = MagicMock()
            process.poll.return_value = None

            def start(*args, **kwargs):
                kwargs["stdout"].write("Forwarding from 127.0.0.1:12345 -> 8080\n")
                kwargs["stdout"].flush()
                return process

            with patch("scripts.lab.STATE", Path(directory)):
                with patch("scripts.lab.subprocess.Popen", side_effect=start):
                    with patch("scripts.lab.time.sleep"):
                        with patch(
                            "scripts.lab.check_json",
                            side_effect=[
                                http.client.RemoteDisconnected("temporary startup race"),
                                None,
                                None,
                                None,
                            ],
                        ) as check:
                            lab.smoke()
            self.assertEqual(check.call_count, 4)
            process.terminate.assert_called_once()

    def test_rollback_refuses_healthy_image(self):
        healthy = '{"spec":{"template":{"spec":{"containers":[{"image":"ops-lab:1.0.0"}]}}}}'
        with patch("scripts.lab.kube", return_value=healthy) as kube:
            with self.assertRaisesRegex(RuntimeError, "Rollback only allowed"):
                lab.rollback()
            self.assertEqual(kube.call_count, 1)


if __name__ == "__main__":
    unittest.main()
