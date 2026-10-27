"""Cross-platform lab commands. Always target the dedicated kind context."""

import argparse
import http.client
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".local"
CONFIG = STATE / "kubeconfig"
CLUSTER = "ops-lab"
CONTEXT = "kind-ops-lab"
IMAGE = "ops-lab:1.0.0"
BAD_IMAGE = "registry.invalid/ops-lab:missing"


def run(*args, capture=False):
    print("+", " ".join(map(str, args)), flush=True)
    result = subprocess.run(
        list(map(str, args)),
        cwd=ROOT,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=capture,
    )
    return result.stdout if capture else ""


def kube_args(*args):
    return [
        "kubectl",
        "--kubeconfig",
        str(CONFIG),
        "--context",
        CONTEXT,
        "--namespace",
        CLUSTER,
        "--request-timeout=20s",
        *args,
    ]


def kube(*args, capture=False):
    return run(*kube_args(*args), capture=capture)


def doctor():
    missing = [tool for tool in ("docker", "kind", "kubectl") if not shutil.which(tool)]
    if missing:
        raise RuntimeError("Missing tools: " + ", ".join(missing))
    run("docker", "info")
    run("kind", "version")
    run("kubectl", "version", "--client")


def up():
    doctor()
    STATE.mkdir(exist_ok=True)
    clusters = run("kind", "get", "clusters", capture=True).splitlines()
    if CLUSTER in clusters:
        raise RuntimeError("Cluster ops-lab already exists; use verify or rollback, not up.")
    run("docker", "build", "-t", IMAGE, ".")
    run(
        "kind",
        "create",
        "cluster",
        "--name",
        CLUSTER,
        "--config",
        ROOT / "kind/cluster.yaml",
        "--kubeconfig",
        CONFIG,
        "--wait",
        "180s",
    )
    run("kind", "load", "docker-image", IMAGE, "--name", CLUSTER)
    # Namespace must exist before namespaced server-side validation can succeed.
    kube("apply", "-f", "k8s/namespace.yaml")
    kube("apply", "--dry-run=server", "-f", "k8s/")
    kube("apply", "-f", "k8s/")
    verify()


def check_json(url, path):
    with urllib.request.urlopen(url + path, timeout=3) as response:
        if response.status != 200:
            raise RuntimeError(f"Unexpected HTTP status: {response.status}")
        body = json.load(response)
    if path == "/":
        if body.get("service") != "ops-lab" or body.get("version") != "1.0.0":
            raise RuntimeError(f"Unexpected application response: {body}")
    elif body != {"status": "ok"}:
        raise RuntimeError(f"Unhealthy application: {body}")
    print(path, json.dumps(body), flush=True)


def smoke():
    """Use an OS-selected localhost port, keep output for troubleshooting."""
    STATE.mkdir(exist_ok=True)
    log_path = STATE / "port-forward.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            kube_args("port-forward", "--address", "127.0.0.1", "service/ops-lab", ":8080"),
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(log_path.read_text(encoding="utf-8"))
                match = re.search(
                    r"Forwarding from 127\.0\.0\.1:(\d+)",
                    log_path.read_text(encoding="utf-8"),
                )
                if match:
                    url = "http://127.0.0.1:" + match.group(1)
                    try:
                        for path in ("/", "/healthz", "/readyz"):
                            check_json(url, path)
                        print("HTTP smoke passed", flush=True)
                        return
                    except (urllib.error.URLError, TimeoutError, http.client.RemoteDisconnected):
                        pass
                time.sleep(0.5)
            raise RuntimeError("Port-forward smoke timed out; see .local/port-forward.log")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def verify():
    kube("wait", "--for=condition=Ready", "nodes", "--all", "--timeout=120s")
    nodes = json.loads(kube("get", "nodes", "-o", "json", capture=True))["items"]
    if len(nodes) != 2:
        raise RuntimeError("Expected exactly two nodes")
    kube("rollout", "status", "deployment/ops-lab", "--timeout=180s")
    # Deployment status can briefly be stale after a node/WSL restart.
    kube("wait", "--for=condition=Ready", "pods", "-l", "app=ops-lab", "--timeout=120s")
    deployment = json.loads(kube("get", "deployment/ops-lab", "-o", "json", capture=True))
    if deployment["status"].get("availableReplicas", 0) != 2:
        raise RuntimeError("Expected two available replicas")
    if deployment["spec"]["template"]["spec"]["containers"][0]["image"] != IMAGE:
        raise RuntimeError("Deployment is not using the expected lab image")
    kube("get", "pods", "-o", "wide")
    smoke()


def diagnostics():
    kube("get", "pods", "-o", "wide")
    kube("describe", "deployment/ops-lab")
    kube("describe", "pods", "-l", "app=ops-lab")
    kube("get", "events", "--sort-by=.metadata.creationTimestamp")
    kube("rollout", "history", "deployment/ops-lab")


def break_image():
    verify()
    kube("set", "image", "deployment/ops-lab", "app=" + BAD_IMAGE)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        pods = json.loads(kube("get", "pods", "-l", "app=ops-lab", "-o", "json", capture=True))[
            "items"
        ]
        for pod in pods:
            for container in pod.get("status", {}).get("containerStatuses", []):
                reason = container.get("state", {}).get("waiting", {}).get("reason")
                if container.get("image") == BAD_IMAGE and reason == "ImagePullBackOff":
                    print("Observed real ImagePullBackOff:", pod["metadata"]["name"], flush=True)
                    diagnostics()
                    return
        time.sleep(3)
    raise RuntimeError("ImagePullBackOff not observed; run diagnose, then rollback")


def rollback():
    # Refuse to undo arbitrary revisions of unrelated or already healthy workloads.
    deployment = json.loads(kube("get", "deployment/ops-lab", "-o", "json", capture=True))
    image = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
    if image != BAD_IMAGE:
        raise RuntimeError("Rollback only allowed immediately after the lab's break-image command")
    kube("rollout", "undo", "deployment/ops-lab")
    verify()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "doctor",
            "up",
            "verify",
            "smoke",
            "diagnose",
            "break-image",
            "rollback",
            "port-forward",
        ),
    )
    parser.add_argument("--confirm-lab-failure", action="store_true")
    args = parser.parse_args()
    if args.command == "break-image" and not args.confirm_lab_failure:
        parser.error(
            "break-image requires --confirm-lab-failure; rollback: python scripts/lab.py rollback"
        )
    commands = {
        "doctor": doctor,
        "up": up,
        "verify": verify,
        "smoke": smoke,
        "diagnose": diagnostics,
        "break-image": break_image,
        "rollback": rollback,
        "port-forward": lambda: kube(
            "port-forward", "--address", "127.0.0.1", "service/ops-lab", "8080:8080"
        ),
    }
    try:
        commands[args.command]()
    except (RuntimeError, subprocess.SubprocessError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
