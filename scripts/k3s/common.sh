# Sourced by Bash exercises from the repository root.
set -euo pipefail
export KUBECONFIG="$PWD/.local/k3s/kubeconfig"
[[ $(kubectl config current-context) == k3s-lab ]]
lab="$PWD/.local/k3s"
evidence="$PWD/docs/evidence/k3s"
mkdir -p "$evidence"
k() { kubectl -n portfolio "$@"; }
sql() { k exec -i portfolio-postgres-0 -- psql -XAtq -v ON_ERROR_STOP=1 -U portfolio -d portfolio "$@"; }
until_ok() {
    local deadline=$((SECONDS + $1))
    shift
    until "$@"; do
        if ((SECONDS >= deadline)); then echo "Timed out waiting for $1" >&2; return 1; fi
        sleep 3
    done
}
