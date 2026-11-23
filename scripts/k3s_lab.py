#!/usr/bin/env python3
"""Three-node k3s lab on local Docker: bootstrap, verify and tear down.

Every command targets an isolated kubeconfig in .local/k3s/kubeconfig and the
k3s-lab Docker Compose project, so the kind stand in scripts/lab.py is untouched.
"""

import argparse
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".local" / "k3s"
KUBECONFIG = STATE / "kubeconfig"
PORTS_FILE = STATE / "ports.json"
COMPOSE_FILE = ROOT / "k3s" / "compose" / "docker-compose.yml"
MANIFESTS = ROOT / "k3s" / "manifests"
PROJECT = "k3s-lab"
CONTEXT = "k3s-lab"
NAMESPACE = "smoke"
IMAGE = "ops-lab:1.0.0"
HOST = "smoke.lab.localhost"
# Pinned so a rerun resolves the same release instead of "latest".
INGRESS_CHART = ("ingress-nginx", "https://kubernetes.github.io/ingress-nginx", "4.15.1")
CERT_CHART = ("cert-manager", "https://charts.jetstack.io", "v1.21.3")
# Compose service name -> container name for the "k3s-lab" project.
NODES = ("server", "agent-1", "agent-2")


def run(*args, capture=False, env=None, check=True):
    print("+", " ".join(map(str, args)), flush=True)
    result = subprocess.run(
        list(map(str, args)),
        cwd=ROOT,
        check=check,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=capture,
        env={**os.environ, **(env or {})},
    )
    return result.stdout if capture else ""


def compose(*args, capture=False, check=True, env=None):
    # Compose requires K3S_TOKEN and K3S_VERSION for every subcommand, not only
    # "up": without them ps/down/logs abort on interpolation before running.
    base = {
        "K3S_TOKEN": write_token(),
        "K3S_VERSION": k3s_version(),
        "K3S_API_PORT": "6443",
        "K3S_HTTP_PORT": "8081",
        "K3S_HTTPS_PORT": "8443",
    }
    if PORTS_FILE.is_file():
        base.update(json.loads(PORTS_FILE.read_text(encoding="utf-8")))
    return run(
        "docker",
        "compose",
        "-p",
        PROJECT,
        "-f",
        COMPOSE_FILE,
        *args,
        capture=capture,
        check=check,
        env={**base, **(env or {})},
    )


def node_container(node):
    return f"{PROJECT}-{node}-1"


def kube_args(*args):
    return [
        "kubectl",
        "--kubeconfig",
        str(KUBECONFIG),
        "--context",
        CONTEXT,
        "--request-timeout=30s",
        *args,
    ]


def kube(*args, capture=False, check=True):
    return run(*kube_args(*args), capture=capture, check=check)


def doctor():
    """Fail before touching Docker when a required tool is missing."""
    missing = [tool for tool in ("docker", "kubectl", "helm") if not shutil.which(tool)]
    if missing:
        raise RuntimeError("Missing tools: " + ", ".join(missing))
    compose("version")
    run("docker", "info", capture=True)
    run("kubectl", "version", "--client", capture=True)
    run("helm", "version", "--short", capture=True)


def k3s_version():
    return os.environ.get("K3S_VERSION", "v1.34.13-k3s1")


def free_port(preferred):
    """Return preferred when it is free, otherwise an unused localhost port."""
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", preferred))
        except OSError:
            # Port 0 lets the kernel choose; getsockname then reports the real one.
            probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def write_token():
    """Create the cluster token once; it must survive a server restart."""
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    token_file = STATE / "token"
    if not token_file.is_file():
        token_file.write_text(os.urandom(32).hex(), encoding="utf-8")
    return token_file.read_text(encoding="utf-8").strip()


def running_nodes():
    out = compose("ps", "--format", "json", capture=True, check=False)
    names = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("Name"):
            names.append(entry["Name"])
    return names


def up():
    doctor()
    if running_nodes():
        raise RuntimeError("The k3s-lab project already exists; run verify, not up.")
    token = write_token()
    ports = {
        "K3S_API_PORT": str(free_port(6443)),
        "K3S_HTTP_PORT": str(free_port(8081)),
        "K3S_HTTPS_PORT": str(free_port(8443)),
    }
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    PORTS_FILE.write_text(json.dumps(ports), encoding="utf-8")
    run("docker", "build", "-t", IMAGE, ".")
    compose(
        "up",
        "-d",
        env={"K3S_TOKEN": token, "K3S_VERSION": k3s_version(), **ports},
    )
    wait_for_api()
    wait_for_nodes()
    import_image()
    install_ingress()
    install_cert_manager()
    create_root_ca()
    kube("apply", "-f", str(MANIFESTS / "namespaces.yaml"))
    verify()


def rewrite_kubeconfig():
    """Point the generated kubeconfig at the published port and this context name.

    k3s writes the in-container API address and the context name "default".
    Neither is usable from the host, so both are rewritten deterministically.
    """
    text = run(
        "docker",
        "exec",
        node_container("server"),
        "cat",
        "/etc/rancher/k3s/k3s.yaml",
        capture=True,
        check=False,
    )
    if "certificate-authority-data" not in text:
        return False
    ports = json.loads(PORTS_FILE.read_text(encoding="utf-8"))
    api = f"https://127.0.0.1:{ports['K3S_API_PORT']}"
    # "- name: default" entries keep their dash, so the prefix is part of the match.
    line_pattern = re.compile(
        r"^(?P<prefix>\s*(?:-\s+)?)"
        r"(?P<key>server|name|cluster|user|current-context):\s*(?P<value>\S+)\s*$"
    )
    lines = []
    for line in text.splitlines():
        match = line_pattern.match(line)
        if not match:
            lines.append(line)
            continue
        key, value = match.group("key"), match.group("value")
        if key == "server":
            value = api
        elif value == "default":
            value = CONTEXT
        lines.append(f"{match.group('prefix')}{key}: {value}")
    temporary = KUBECONFIG.with_suffix(".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as out:
        out.write("\n".join(lines) + "\n")
    temporary.replace(KUBECONFIG)
    return True


def wait_for_api(timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if rewrite_kubeconfig():
            if kube("get", "--raw", "/readyz", capture=True, check=False).strip() == "ok":
                print("k3s API is ready", flush=True)
                return
        time.sleep(3)
    logs = compose("logs", "--tail", "60", "server", capture=True, check=False)
    raise RuntimeError("k3s server did not become ready:\n" + logs)


def wait_for_nodes(timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        items = json.loads(kube("get", "nodes", "-o", "json", capture=True))["items"]
        ready = sorted(
            node["metadata"]["name"]
            for node in items
            if any(
                condition["type"] == "Ready" and condition["status"] == "True"
                for condition in node["status"].get("conditions", [])
            )
        )
        if len(items) == 3 and len(ready) == 3:
            print("Nodes Ready: " + ", ".join(ready), flush=True)
            return
        time.sleep(3)
    raise RuntimeError("Expected three Ready nodes; see: python scripts/k3s_lab.py logs")


def import_image():
    """Load the lab image into every node; there is no registry in this lab."""
    archive = STATE / "ops-lab.tar"
    run("docker", "save", "-o", archive, IMAGE)
    for node in NODES:
        print(f"+ import {IMAGE} into {node_container(node)}", flush=True)
        inbox = "/var/lib/rancher/k3s/agent/images"
        container = node_container(node)
        run("docker", "exec", container, "mkdir", "-p", inbox)
        run("docker", "cp", archive, f"{container}:/tmp/ops-lab.tar")
        run("docker", "exec", container, "mv", "/tmp/ops-lab.tar", f"{inbox}/ops-lab.tar")
    archive.unlink()


def helm(chart, repo, version, namespace, extra=()):
    run(
        "helm",
        "upgrade",
        "--install",
        chart,
        chart,
        "--repo",
        repo,
        "--version",
        version,
        "--namespace",
        namespace,
        "--create-namespace",
        "--kubeconfig",
        str(KUBECONFIG),
        "--kube-context",
        CONTEXT,
        "--wait",
        "--timeout",
        "10m",
        *extra,
    )


def install_ingress():
    values = ROOT / "k3s" / "ingress-nginx-values.yaml"
    helm(*INGRESS_CHART, "ingress-nginx", ("-f", str(values)))


def install_cert_manager():
    helm(*CERT_CHART, "cert-manager", ("--set", "crds.enabled=true"))
    kube(
        "-n",
        "cert-manager",
        "wait",
        "--for=condition=Available",
        "deployment/cert-manager-webhook",
        "--timeout=300s",
    )


def create_root_ca():
    """Create the self-signed root once; cert-manager only signs with it."""
    secret = "lab-root-ca"
    if kube("-n", "cert-manager", "get", "secret", secret, capture=True, check=False).strip():
        return
    work = STATE / "ca"
    work.mkdir(parents=True, exist_ok=True)
    key, crt = work / "tls.key", work / "tls.crt"
    if not key.is_file() or not crt.is_file():
        run(
            "openssl",
            "req",
            "-x509",
            "-nodes",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:prime256v1",
            "-days",
            "825",
            "-keyout",
            key,
            "-out",
            crt,
            "-subj",
            "/CN=kubernetes-ops-lab local root CA",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
        )
    manifest = kube(
        "-n",
        "cert-manager",
        "create",
        "secret",
        "tls",
        secret,
        "--cert",
        crt,
        "--key",
        key,
        "--dry-run=client",
        "-o",
        "yaml",
        capture=True,
    )
    print("+ kubectl apply -f - (lab-root-ca secret)", flush=True)
    subprocess.run(
        kube_args("apply", "-f", "-"),
        cwd=ROOT,
        check=True,
        text=True,
        encoding="utf-8",
        input=manifest,
    )


def verify():
    """Prove three nodes, DNS, the ingress path and a real issued certificate."""
    items = json.loads(kube("get", "nodes", "-o", "json", capture=True))["items"]
    if len(items) != 3:
        raise RuntimeError(f"Expected exactly three nodes, found {len(items)}")
    kube("wait", "--for=condition=Ready", "nodes", "--all", "--timeout=180s")
    kube("apply", "-f", str(MANIFESTS / "smoke.yaml"))
    for namespace in ("kube-system", "ingress-nginx", "cert-manager", NAMESPACE):
        kube("get", "namespace", namespace, capture=True)
    kube(
        "-n",
        "kube-system",
        "wait",
        "--for=condition=Ready",
        "pods",
        "-l",
        "k8s-app=kube-dns",
        "--timeout=180s",
    )
    kube("apply", "-f", str(MANIFESTS / "issuer.yaml"))
    kube("-n", NAMESPACE, "rollout", "status", "deployment/smoke", "--timeout=180s")
    kube(
        "-n",
        NAMESPACE,
        "exec",
        "deploy/smoke",
        "--",
        "python",
        "-c",
        "import socket; print(socket.gethostbyname('smoke.smoke.svc.cluster.local'))",
    )
    # Issuance is asynchronous; the Ready condition is the real gate, not the apply.
    kube(
        "-n",
        NAMESPACE,
        "wait",
        "--for=condition=Ready",
        "certificate/smoke-tls",
        "--timeout=300s",
    )
    check_ingress()


def check_ingress():
    """Request the smoke host over TLS through the published ingress port."""
    ports = json.loads(PORTS_FILE.read_text(encoding="utf-8"))
    port = int(ports["K3S_HTTPS_PORT"])
    authority = f"https://{HOST}:{port}"
    deadline = time.monotonic() + 180
    last = "no attempt made"
    while time.monotonic() < deadline:
        try:
            context = ssl.create_default_context(cafile=str(STATE / "ca" / "tls.crt"))
            with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
                with context.wrap_socket(raw, server_hostname=HOST) as tls:
                    request = f"GET /healthz HTTP/1.1\r\nHost: {HOST}\r\nConnection: close\r\n\r\n"
                    tls.sendall(request.encode())
                    response = tls.recv(65536).decode("utf-8", "replace")
            if response.startswith("HTTP/1.1 200"):
                print(f"HTTPS OK via {authority}/healthz", flush=True)
                return
            last = response.splitlines()[0] if response else "empty response"
        except (OSError, ssl.SSLError) as error:
            last = f"{type(error).__name__}: {error}"
        time.sleep(3)
    raise RuntimeError(f"HTTPS check via {authority} failed: {last}")


def down():
    compose("down", "--volumes", "--remove-orphans")
    print("Removed k3s-lab containers and volumes; the kind stand is untouched.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("doctor", "up", "verify", "down", "logs"))
    args = parser.parse_args()
    commands = {
        "doctor": doctor,
        "up": up,
        "verify": verify,
        "down": down,
        "logs": lambda: compose("logs", "--tail", "80"),
    }
    try:
        commands[args.command]()
    except (RuntimeError, subprocess.SubprocessError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
