#!/usr/bin/env bash
# Mirror only public Git contents. All rehearsal writes go to local branches.
source scripts/k3s/common.sh
umask 077
mirror="$lab/git"
mkdir -p "$mirror"
git init -q --bare "$mirror/release.git"
git --git-dir="$mirror/release.git" fetch -q https://github.com/nikit0id/compose-app-cicd.git \
    refs/heads/gitops-values:refs/heads/rehearsal
git --git-dir="$mirror/release.git" symbolic-ref HEAD refs/heads/rehearsal
git clone -q "$mirror/release.git" "$lab/release"
git -C "$lab/release" config user.name 'K3s rehearsal'
git -C "$lab/release" config user.email 'lab@localhost'
git clone -q --bare --no-local . "$mirror/chart.git"
git --git-dir="$mirror/chart.git" update-ref refs/heads/rehearsal "$(git rev-parse HEAD)"
gateway=$(docker network inspect k3s-lab_k3s | jq -er '.[0].IPAM.Config | map(.Gateway | strings | select(test("^[0-9.]+$"))) | first')
git daemon --reuseaddr --export-all --listen="$gateway" --port=17883 --base-path="$mirror" \
    --strict-paths "$mirror/chart.git" "$mirror/release.git" >"$lab/git-server.log" 2>&1 &
echo $! > "$lab/git-server.pid"
giturl="git://$gateway:17883"
until_ok 20 git ls-remote --exit-code "$giturl/release.git" refs/heads/rehearsal
helm upgrade --install monitoring kube-prometheus-stack --repo https://prometheus-community.github.io/helm-charts \
  --version 93.6.1 -n monitoring --create-namespace -f portfolio/cluster/monitoring.yaml --wait --timeout 10m
kubectl create ns argocd
kubectl apply --server-side -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/v3.6.2/manifests/install.yaml >/dev/null
kubectl -n argocd rollout status sts/argocd-application-controller --timeout=6m
kubectl create ns portfolio
openssl rand -hex 32 | tr -d '\n' | k create secret generic portfolio-db --from-file=password=/dev/stdin
kubectl patch --local -f portfolio/cluster/registry-application.yaml --type=json \
    -p "$(jq -nc --arg url "$giturl" '[
    {op:"replace",path:"/spec/sources/0/repoURL",value:($url+"/chart.git")},
    {op:"replace",path:"/spec/sources/1/repoURL",value:($url+"/release.git")},
    {op:"replace",path:"/spec/sources/0/targetRevision",value:"rehearsal"},
    {op:"replace",path:"/spec/sources/1/targetRevision",value:"rehearsal"},
    {op:"add",path:"/spec/sources/0/helm/valueFiles/0",value:"values-k3s.yaml"}]')" \
    -o yaml | kubectl apply -f -
argo() { kubectl -n argocd "$@"; }
sync_release() {
    local rev
    rev=$(git -C "$lab/release" rev-parse HEAD)
    argo annotate app portfolio argocd.argoproj.io/refresh=hard --overwrite
    argo wait app/portfolio "--for=jsonpath={.status.sync.revisions[1]}=$rev" --timeout=6m
}
sync_release
k rollout status deploy/portfolio-api --timeout=6m
# Change a managed env var without restarting Pods, using a ConfigMap.
k patch cm portfolio-api -p '{"data":{"PGDATABASE":"drift"}}'
k wait cm/portfolio-api --for=jsonpath='{.data.PGDATABASE}'=portfolio --timeout=3m
values="$lab/release/portfolio-values.yaml"
good=$(git -C "$lab/release" rev-parse HEAD)
old_image=$(k get deploy portfolio-api -o jsonpath='{.spec.template.spec.containers[0].image}')
bad=sha256:$(printf '%064d' 0)
sed -Ei "s/sha256:[a-f0-9]{64}/$bad/" "$values"
git -C "$lab/release" commit -qam 'Exercise a missing manifest digest'
broken=$(git -C "$lab/release" rev-parse HEAD)
git -C "$lab/release" push -q origin HEAD:rehearsal
sync_release
k wait deploy/portfolio-api "--for=jsonpath={.spec.template.spec.containers[0].image}=${old_image%@*}@$bad" --timeout=2m
if k rollout status deploy/portfolio-api --timeout=90s; then echo 'Invalid digest rolled out' >&2; exit 1; fi
# Revert only our failed promotion, without overwriting a concurrent release.
git -C "$lab/release" revert --no-edit "$broken"
git -C "$lab/release" push -q origin HEAD:rehearsal
sync_release
k wait deploy/portfolio-api "--for=jsonpath={.spec.template.spec.containers[0].image}=$old_image" --timeout=2m
k rollout status deploy/portfolio-api --timeout=4m
argo wait app/portfolio --for=jsonpath='{.status.health.status}'=Healthy --timeout=4m
{
    echo "good=$good broken=$broken rollback=$(git -C "$lab/release" rev-parse HEAD)"
    echo "restored_image=$old_image"
    argo get app portfolio -o json | jq '{history:.status.history,sync:.status.sync,health:.status.health}'
} >"$evidence/gitops.jsonl"
