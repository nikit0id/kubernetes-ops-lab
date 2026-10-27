import unittest
from pathlib import Path

import kubernetes_validate
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


class ManifestTests(unittest.TestCase):
    def test_kubernetes_schemas(self):
        for path in sorted((ROOT / "k8s").glob("*.yaml")):
            with self.subTest(file=path.name):
                resource = yaml.safe_load(path.read_text(encoding="utf-8"))
                kubernetes_validate.validate(resource, "1.34.0", strict=True)

    def test_two_nodes(self):
        cluster = load("kind/cluster.yaml")
        self.assertEqual(cluster["kind"], "Cluster")
        self.assertEqual(cluster["apiVersion"], "kind.x-k8s.io/v1alpha4")
        self.assertEqual([node["role"] for node in cluster["nodes"]], ["control-plane", "worker"])
        self.assertEqual(cluster["networking"]["apiServerAddress"], "127.0.0.1")
        self.assertTrue(all("@sha256:" in node["image"] for node in cluster["nodes"]))

    def test_service_matches_deployment(self):
        deployment = load("k8s/deployment.yaml")
        service = load("k8s/service.yaml")
        namespace = load("k8s/namespace.yaml")["metadata"]["name"]
        spec = deployment["spec"]
        self.assertEqual(spec["replicas"], 2)
        self.assertEqual(service["spec"]["type"], "ClusterIP")
        self.assertEqual(spec["selector"]["matchLabels"], spec["template"]["metadata"]["labels"])
        self.assertEqual(service["spec"]["selector"], spec["selector"]["matchLabels"])
        self.assertEqual(deployment["metadata"]["namespace"], namespace)
        self.assertEqual(service["metadata"]["namespace"], namespace)
        container = spec["template"]["spec"]["containers"][0]
        port = container["ports"][0]
        self.assertEqual(service["spec"]["ports"][0]["targetPort"], port["name"])
        self.assertEqual(port["containerPort"], 8080)

    def test_probes_resources_and_security(self):
        spec = load("k8s/deployment.yaml")["spec"]["template"]["spec"]
        container = spec["containers"][0]
        self.assertEqual(container["image"], "ops-lab:1.0.0")
        self.assertEqual(container["imagePullPolicy"], "IfNotPresent")
        for probe, path in (("livenessProbe", "/healthz"), ("readinessProbe", "/readyz")):
            self.assertEqual(container[probe]["httpGet"], {"path": path, "port": "http"})
        for key in ("requests", "limits"):
            self.assertEqual(set(container["resources"][key]), {"cpu", "memory"})
        self.assertTrue(spec["securityContext"]["runAsNonRoot"])
        self.assertFalse(spec["automountServiceAccountToken"])
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertFalse(container["securityContext"]["allowPrivilegeEscalation"])


if __name__ == "__main__":
    unittest.main()
