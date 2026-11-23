#!/usr/bin/env bash
source scripts/k3s/common.sh
ns=incident-rehearsal
kubectl create ns "$ns"
i() { kubectl -n "$ns" "$@"; }
trap 'kubectl delete ns "$ns" --wait=false' EXIT
i create deployment pull-test --image=ops-lab:missing
pull_failure() {
 i get pods -l app=pull-test -o json | jq -e 'any(.items[].status.containerStatuses[]?; .state.waiting.reason=="ImagePullBackOff")'
}
until_ok 150 pull_failure
i get events --field-selector reason=Failed -o custom-columns=REASON:.reason,MESSAGE:.message
i set image deployment/pull-test '*=ops-lab:1.0.0'
i rollout status deployment/pull-test --timeout=3m
i run memory-test --image=ops-lab:1.0.0 --restart=Never \
    --overrides='{"spec":{"containers":[{"name":"memory-test","image":"ops-lab:1.0.0","resources":{"limits":{"memory":"32Mi"}},"command":["python","-c","x=bytearray(96*1024*1024)"]}]}}'
i wait pod/memory-test --for=jsonpath='{.status.containerStatuses[0].state.terminated.reason}'=OOMKilled --timeout=3m
i delete pod memory-test
i run memory-test --restart=Never --image=ops-lab:1.0.0 \
    --overrides='{"spec":{"containers":[{"name":"memory-test","image":"ops-lab:1.0.0","resources":{"limits":{"memory":"160Mi"}},"command":["python","-c","x=bytearray(96*1024*1024)"]}]}}'
i wait pod/memory-test --for=jsonpath='{.status.phase}'=Succeeded --timeout=3m
# A wrong DNS name fails from the same Pod that resolves the correct Service.
if i exec deploy/pull-test -- python -c "import socket; socket.gethostbyname('smkoe.smoke.svc.cluster.local')"; then exit 1; fi
i exec deploy/pull-test -- python -c "import urllib.request; print(urllib.request.urlopen('http://smoke.smoke.svc.cluster.local:8080/healthz').status)"
