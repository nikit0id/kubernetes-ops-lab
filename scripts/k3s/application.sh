#!/usr/bin/env bash
source scripts/k3s/common.sh
k wait hpa/portfolio-api --for=condition=ScalingActive --timeout=4m
k wait deploy/portfolio-api --for=jsonpath='{.status.readyReplicas}'=2 --timeout=3m
# Keep the original replicas serving. A standalone canary exercises the Service selector.
canary=readiness-rehearsal
owner=$(k create configmap "$canary" -o jsonpath='{.metadata.uid}')
k get deploy portfolio-api -o json | jq --arg name "$canary" --arg owner "$owner" '{
  apiVersion:"v1",kind:"Pod",metadata:{name:$name,labels:.spec.template.metadata.labels,
    ownerReferences:[{apiVersion:"v1",kind:"ConfigMap",name:$name,uid:$owner,controller:true}]},
  spec:.spec.template.spec} | .spec.containers[0].readinessProbe={
    exec:{command:["sh","-c","test -f /tmp/ready"]},periodSeconds:2,failureThreshold:1}' | k create -f -
trap 'k delete pod "$canary" --ignore-not-found --wait=false' EXIT
until_ok 120 k exec "$canary" -c api -- touch /tmp/ready
ip=$(k get pod "$canary" -o jsonpath='{.status.podIP}')
endpoint() {
    k get endpointslice -l kubernetes.io/service-name=portfolio-api -o json |
        jq -e --arg ip "$ip" --argjson state "$1" 'any(.items[].endpoints[];
        (.addresses|index($ip)) != null and .conditions.ready==$state)'
}
until_ok 90 endpoint true
k exec "$canary" -c api -- rm /tmp/ready
until_ok 90 endpoint false
k delete pod "$canary"
k delete configmap "$canary"
trap - EXIT
# CPU is the HPA signal; this bounded job runs inside one API container.
pod=$(k get pods -l app=portfolio-api -o jsonpath='{.items[0].metadata.name}')
k exec "$pod" -- python -c 'import time; end=time.monotonic()+180
while time.monotonic()<end: pass' >/dev/null &
burn=$!
scaled() { k get hpa portfolio-api -o json | jq -e '.status.currentReplicas >= 3'; }
until_ok 180 scaled
k get hpa -o wide
wait "$burn"
sql -c 'CREATE TABLE IF NOT EXISTS lab_recovery(id text primary key);'
marker="node-$(date +%s)"
sql -c "INSERT INTO lab_recovery VALUES ('$marker');"
node=$(k get pod portfolio-postgres-0 -o jsonpath='{.spec.nodeName}')
[[ "$node" == k3s-agent-1 ]]
docker restart k3s-lab-agent-1-1 >/dev/null
restored() { [[ $(sql -c "SELECT id FROM lab_recovery WHERE id='$marker';") == "$marker" ]]; }
until_ok 300 restored
echo "EndpointSlice readiness transition and HPA scale-out verified; $marker survived restart of $node"
