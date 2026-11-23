import json
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts import k3s_lab

ROOT = Path(__file__).resolve().parents[1]
# Compose expands the token from the environment; a typo in this marker fails at runtime.
TOKEN_MARKER = "${K3S_TOKEN:?err}"


def load(name):
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


class K3sLabBootstrapTests(unittest.TestCase):
    def test_compose_defines_one_server_and_two_agents(self):
        compose = load("k3s/compose/docker-compose.yml")
        services = compose["services"]
        self.assertEqual(set(services), {"server", "agent-1", "agent-2"})
        self.assertIn("server --cluster-init", services["server"]["command"])
        self.assertIn("--disable=traefik", services["server"]["command"])
        for agent in ("agent-1", "agent-2"):
            environment = services[agent]["environment"]
            self.assertIn("K3S_URL=https://server:6443", environment)
            self.assertIn("K3S_TOKEN=" + TOKEN_MARKER, environment)
            # Each agent needs a distinct node name, or they collide in the API.
            self.assertIn("K3S_NODE_NAME=k3s-" + agent, environment)
        self.assertNotEqual(services["agent-1"]["environment"], services["agent-2"]["environment"])

    def test_compose_never_pins_latest(self):
        text = (ROOT / "k3s/compose/docker-compose.yml").read_text(encoding="utf-8")
        self.assertNotIn(":latest", text)
        self.assertIn("K3S_VERSION:?err", text)

    def test_compose_env_supplies_every_required_variable(self):
        """ps/down/logs must not abort on Compose interpolation of :?err variables."""
        with patch("scripts.k3s_lab.run") as run:
            with patch("scripts.k3s_lab.write_token", return_value="token"):
                with patch("scripts.k3s_lab.PORTS_FILE") as ports_file:
                    ports_file.is_file.return_value = False
                    k3s_lab.compose("ps")
        environment = run.call_args.kwargs["env"]
        for name in ("K3S_TOKEN", "K3S_VERSION"):
            self.assertIn(name, environment)
            self.assertTrue(environment[name])

    def test_bootstrap_script_defaults_to_a_pinned_release(self):
        with patch.dict("os.environ", {}, clear=False):
            self.assertRegex(k3s_lab.k3s_version(), r"^v\d+\.\d+\.\d+-k3s\d+$")

    def test_ingress_values_disable_traefik_conflict(self):
        values = load("k3s/ingress-nginx-values.yaml")
        self.assertTrue(values["controller"]["hostPort"]["enabled"])
        selector = values["controller"]["nodeSelector"]
        self.assertEqual(selector["node-role.kubernetes.io/control-plane"], "true")

    def test_smoke_workload_matches_lab_security_baseline(self):
        manifests = [
            doc
            for doc in yaml.safe_load_all(
                (ROOT / "k3s/manifests/smoke.yaml").read_text(encoding="utf-8")
            )
            if doc
        ]
        deployment = next(doc for doc in manifests if doc["kind"] == "Deployment")
        pod = deployment["spec"]["template"]["spec"]
        container = pod["containers"][0]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertTrue(pod["securityContext"]["runAsNonRoot"])
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertFalse(container["securityContext"]["allowPrivilegeEscalation"])
        self.assertEqual(container["image"], k3s_lab.IMAGE)

    def test_issuer_is_a_cluster_scoped_ca(self):
        documents = [
            doc
            for doc in yaml.safe_load_all(
                (ROOT / "k3s/manifests/issuer.yaml").read_text(encoding="utf-8")
            )
            if doc
        ]
        issuer = next(doc for doc in documents if doc["kind"] == "ClusterIssuer")
        certificate = next(doc for doc in documents if doc["kind"] == "Certificate")
        self.assertEqual(issuer["spec"]["ca"]["secretName"], "lab-root-ca")
        self.assertEqual(certificate["spec"]["issuerRef"]["name"], "lab-selfsigned")
        self.assertEqual(certificate["spec"]["issuerRef"]["kind"], "ClusterIssuer")
        self.assertEqual(certificate["metadata"]["namespace"], "cert-manager")

    def test_up_refuses_to_reuse_an_existing_project(self):
        with patch("scripts.k3s_lab.doctor"):
            with patch("scripts.k3s_lab.running_nodes", return_value=["k3s-lab-server-1"]):
                with self.assertRaisesRegex(RuntimeError, "already exists"):
                    k3s_lab.up()

    def test_doctor_names_every_missing_tool(self):
        with patch("scripts.k3s_lab.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Missing tools: docker, kubectl, helm"):
                k3s_lab.doctor()

    def test_every_kubectl_call_is_pinned_to_the_lab_context(self):
        args = k3s_lab.kube_args("get", "nodes")
        self.assertEqual(args[args.index("--context") + 1], "k3s-lab")
        self.assertEqual(
            args[args.index("--kubeconfig") + 1],
            str(ROOT / ".local/k3s/kubeconfig"),
        )
        self.assertEqual(args[-2:], ["get", "nodes"])

    @staticmethod
    def node(name, ready):
        return {
            "metadata": {"name": name},
            "status": {"conditions": [{"type": "Ready", "status": "True" if ready else "False"}]},
        }

    def test_wait_for_nodes_needs_all_three_ready(self):
        snapshots = [
            [self.node("k3s-server", True), self.node("k3s-agent-1", True)],
            [
                self.node("k3s-server", True),
                self.node("k3s-agent-1", True),
                self.node("k3s-agent-2", False),
            ],
            [
                self.node("k3s-server", True),
                self.node("k3s-agent-1", True),
                self.node("k3s-agent-2", True),
            ],
        ]
        reads = []

        def output(*args, **kwargs):
            result = snapshots[len(reads)]
            reads.append(result)
            return json.dumps({"items": result})

        with patch("scripts.k3s_lab.kube", side_effect=output):
            with patch("scripts.k3s_lab.time.sleep"):
                k3s_lab.wait_for_nodes()
        self.assertEqual(len(reads), 3)

    def test_free_port_returns_a_usable_port(self):
        self.assertGreater(k3s_lab.free_port(0), 0)

    def test_kubeconfig_is_rewritten_for_the_host(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            kubeconfig = state / "kubeconfig"
            kubeconfig.write_text(
                "clusters:\n"
                "  - name: default\n"
                "    cluster:\n"
                "      server: https://127.0.0.1:6443\n"
                "      certificate-authority-data: abc\n"
                "contexts:\n"
                "  - name: default\n"
                "    context:\n"
                "      cluster: default\n"
                "      user: default\n"
                "current-context: default\n",
                encoding="utf-8",
            )
            (state / "ports.json").write_text(
                json.dumps({"K3S_API_PORT": "16443"}), encoding="utf-8"
            )
            with patch("scripts.k3s_lab.KUBECONFIG", kubeconfig):
                with patch("scripts.k3s_lab.STATE", state):
                    with patch("scripts.k3s_lab.PORTS_FILE", state / "ports.json"):
                        with patch("scripts.k3s_lab.run", return_value=kubeconfig.read_text()):
                            self.assertTrue(k3s_lab.rewrite_kubeconfig())
                        self.assertEqual(kubeconfig.stat().st_mode & 0o777, 0o600)
            rewritten = kubeconfig.read_text(encoding="utf-8")
            self.assertIn("server: https://127.0.0.1:16443", rewritten)
            self.assertIn("current-context: k3s-lab", rewritten)
            self.assertNotIn("default", rewritten)


if __name__ == "__main__":
    unittest.main()
