#!/usr/bin/env bash
# Restore into a newly named Compose project. Only this invocation's volumes are removed.
source scripts/k3s/common.sh
umask 077
backup=$(mktemp -d "$lab/backup.XXXXXX")
project="k3s-dr-$(date +%s)-$$"
case "$project" in k3s-dr-*) ;; *) exit 1;; esac
conf="$backup/kubeconfig"
dr() { kubectl --request-timeout=20s --kubeconfig="$conf" "$@"; }
dsql() { dr -n portfolio exec -i portfolio-postgres-0 -- psql -XAtq -v ON_ERROR_STOP=1 -U portfolio -d portfolio "$@"; }
# Compose assigns independent loopback ports so the source stays online.
export K3S_API_PORT=0 K3S_HTTP_PORT=0 K3S_HTTPS_PORT=0
export K3S_VERSION="${K3S_VERSION:-v1.34.13-k3s1}"
docker exec k3s-lab-server-1 cat /var/lib/rancher/k3s/server/token >"$backup/token"
K3S_TOKEN=$(cat "$backup/token")
export K3S_TOKEN
{
    echo "services:"
    for node in server agent-1 agent-2; do
        printf '  %s:\n    volumes:\n      - %s/%s.password:/etc/rancher/node/password\n' "$node" "$backup" "$node"
        if [[ "$node" == server ]]; then
            printf '      - %s/etcd.snapshot:/restore/snapshot:ro\n' "$backup"
            cat <<'RESET'
    entrypoint: ["/bin/sh", "-ec"]
    command:
      - |
        marker=/var/lib/rancher/k3s/server/db/.lab-restored
        if [ ! -f "$$marker" ]; then
          k3s server --node-name=k3s-server --cluster-reset --cluster-reset-restore-path=/restore/snapshot
          touch "$$marker"
        fi
        exec k3s server --node-name=k3s-server --cluster-init --disable=traefik
RESET
        fi
    done
} >"$backup/identities.yml"
dc() { docker compose -p "$project" -f k3s/compose/docker-compose.yml -f "$backup/identities.yml" "$@"; }
cleanup() {
    local result=$?
    if ((result)); then
        dc ps -a || true
        df -h "$lab" || true
        for node in server agent-1 agent-2; do
            docker inspect --format 'exit={{.State.ExitCode}} oom={{.State.OOMKilled}}' "$project-$node-1" || true
            docker logs --tail 160 "$project-$node-1" 2>&1 |
                grep -Ei 'fatal|no space|permission denied|node password|bootstrap data|cluster membership|panic' |
                grep -Evi 'token=|password=' | tail -15 || true
        done
        if [[ -s "$conf" ]]; then
            dr --request-timeout=5s get pods -A -o wide || true
            dr --request-timeout=5s get events -A --sort-by=.lastTimestamp | tail -35 || true
        fi
    fi
    docker network disconnect "${project}_k3s" k3s-lab-registry 2>/dev/null || true
    dc down -v --remove-orphans >/dev/null
}
trap cleanup EXIT
marker="dr-$(date +%s)"
sql -c 'CREATE TABLE IF NOT EXISTS lab_recovery(id text primary key);'
sql -c "INSERT INTO lab_recovery VALUES ('$marker');"
backup_time=$(date +%s)
k exec portfolio-postgres-0 -- pg_dump --no-owner --no-acl -U portfolio -d portfolio -Fc >"$backup/portfolio.dump"
test -s "$backup/portfolio.dump"
docker exec k3s-lab-server-1 k3s etcd-snapshot save --name lab-restore
snapshot=$(docker exec k3s-lab-server-1 sh -ec 'ls -t /var/lib/rancher/k3s/server/db/snapshots/lab-restore-* | head -1')
docker cp "k3s-lab-server-1:$snapshot" "$backup/etcd.snapshot"
for node in server agent-1 agent-2; do
    docker cp "k3s-lab-$node-1:/etc/rancher/node/password" "$backup/$node.password"
done
sql -c "INSERT INTO lab_recovery VALUES ('$marker-after-backup');"
start=$(date +%s)
# Only the runner's throwaway source can be stopped to release memory.
if [[ ${GITHUB_ACTIONS:-} == true ]]; then
    docker stop k3s-lab-agent-1-1 k3s-lab-agent-2-1 k3s-lab-server-1 >/dev/null
fi
dc create
docker network connect --alias lab-registry "${project}_k3s" k3s-lab-registry
dc up -d
port=$(dc port server 6443 | awk -F: '{print $NF}')
config_ready() {
    docker exec "$project-server-1" test -f /var/lib/rancher/k3s/server/db/.lab-restored &&
        docker exec "$project-server-1" cat /etc/rancher/k3s/k3s.yaml >"$conf" &&
        kubectl --kubeconfig="$conf" config set-cluster default --server="https://127.0.0.1:$port" >/dev/null &&
        dr get --raw='/readyz' | grep -qx ok
}
until_ok 240 config_ready
dr wait --for=condition=Ready nodes --all --timeout=5m
# Restored Ready conditions can predate the disaster. Wait for every kubelet
# to publish its new address before executing commands against the restored Pods.
for node in server agent-1 agent-2; do
    ip=$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$project-$node-1")
    node_updated() {
        dr get node "k3s-$node" -o json | jq -e --arg ip "$ip" 'any(.status.addresses[]; .type=="InternalIP" and .address==$ip)' >/dev/null
    }
    until_ok 150 node_updated
done
postgres_ready() { dsql -c 'SELECT 1' | grep -qx 1; }
until_ok 300 postgres_ready
dr -n portfolio exec -i portfolio-postgres-0 -- pg_restore -U portfolio -d portfolio --clean --if-exists \
    --no-owner --no-acl --exit-on-error <"$backup/portfolio.dump"
[[ $(dsql -c "SELECT id FROM lab_recovery WHERE id LIKE '$marker%';") == "$marker" ]]
dr -n portfolio rollout status deploy/portfolio-api --timeout=5m
dr -n portfolio exec deploy/portfolio-api -c api -- python -c \
    "import json,urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8000/health'))['status']=='ok'"
end=$(date +%s)
printf 'RTO=%ss RPO=%ss restored_row=%s lost_post_backup_rows=1 health=ok\n' \
    "$((end-start))" "$((start-backup_time))" "$marker" | tee "$evidence/dr.txt"
