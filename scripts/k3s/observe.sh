#!/usr/bin/env bash
source scripts/k3s/common.sh
k create configmap pvc-metrics-code --from-file=scripts/k3s/pvc_metrics.py
pg_uid=$(k exec portfolio-postgres-0 -- id -u postgres)
[[ "$pg_uid" =~ ^[1-9][0-9]*$ ]]
sed "s/runAsUser: 999/runAsUser: $pg_uid/" k3s/storage-metrics.yaml | kubectl apply -f -
# Discover the controller Service port so this selector follows the installed chart.
kubectl -n cert-manager get svc cert-manager -o json | jq '{
  apiVersion:"monitoring.coreos.com/v1",kind:"ServiceMonitor",
  metadata:{name:"lab-certificates",namespace:"cert-manager",labels:{release:"monitoring"}},
  spec:{selector:{matchLabels:.metadata.labels},endpoints:[
    {port:(.spec.ports[]|select(.port==9402)|.name),interval:"30s",honorLabels:true}]}}' | kubectl apply -f -
kubectl apply -f k3s/alerts.yaml
kubectl -n monitoring create cm portfolio-dashboards --from-file=k3s/dashboards
kubectl -n monitoring label cm portfolio-dashboards grafana_dashboard=1
# Query Grafana itself. Its runtime image has no shell.
(
    umask 077
    auth=$(mktemp "$lab/grafana-auth.XXXXXX")
    kubectl -n monitoring get secret monitoring-grafana -o json |
        jq -r '"machine 127.0.0.1 login " + (.data["admin-user"]|@base64d) +
               " password " + (.data["admin-password"]|@base64d)' >"$auth"
    kubectl -n monitoring port-forward --address 127.0.0.1 svc/monitoring-grafana :80 >"$lab/grafana.port" 2>&1 &
    forward=$!
    trap 'kill "$forward" 2>/dev/null || true; rm -f "$auth"' EXIT
    dashboards_loaded() {
        local port uid
        port=$(sed -nE 's/.*127[.]0[.]0[.]1:([0-9]+) ->.*/\1/p' "$lab/grafana.port" | head -1)
        [[ -n "$port" ]] || return 1
        for uid in portfolio-api portfolio-operations; do
            curl -fsS --max-time 4 --netrc-file "$auth" "http://127.0.0.1:$port/api/dashboards/uid/$uid" |
                jq -e --arg uid "$uid" '.dashboard.uid==$uid' >/dev/null || return 1
        done
    }
    until_ok 150 dashboards_loaded
)
# Receive one synthetic alert and measure elapsed wall time on the same host clock.
k create configmap alert-receiver --from-file=receiver.py=scripts/k3s/receiver.py
k run alert-receiver --image=ops-lab:1.0.0 --port=8080 --overrides='{"spec":{"volumes":[{"name":"code","configMap":{"name":"alert-receiver"}}],"containers":[{"name":"alert-receiver","image":"ops-lab:1.0.0","command":["python","-u","/receiver/receiver.py"],"volumeMounts":[{"name":"code","mountPath":"/receiver"}]}]}}'
k expose pod alert-receiver --port=8080
k wait pod/alert-receiver --for=condition=Ready --timeout=2m
kubectl apply -f k3s/alert-route.yaml
if [[ -n ${TELEGRAM_BOT_TOKEN:-} && -n ${TELEGRAM_CHAT_ID:-} ]]; then
    printf %s "$TELEGRAM_BOT_TOKEN" | k create secret generic lab-telegram --from-file=token=/dev/stdin
    k patch alertmanagerconfig lab-route --type=json -p "$(jq -nc --argjson chat "$TELEGRAM_CHAT_ID" '[{op:"add",path:"/spec/receivers/0/telegramConfigs",value:[{chatID:$chat,botToken:{name:"lab-telegram",key:"token"}}]}]')"
fi
start=$(date +%s)
cat <<YAML | k apply -f -
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata: {name: test-notification, labels: {release: monitoring}}
spec:
  groups:
    - name: rehearsal
      interval: 15s
      rules:
        - alert: LabDeliveryTest
          expr: up{namespace="portfolio",service="portfolio-api"} == 1
          for: 30s
YAML
received() { k logs alert-receiver | grep -q '^LabDeliveryTest '; }
until_ok 300 received
end=$(k logs alert-receiver | awk '/^LabDeliveryTest / {print $2; exit}')
echo "delivery_seconds=$((end-start)) start=$start received=$end scrape=15s evaluation=15s for=30s group_wait=5s"
# Verify all four rules are loaded and the panels have source series on this cluster.
prom_service=monitoring-kube-prometheus-prometheus
prom_port=$(kubectl -n monitoring get svc "$prom_service" -o json | jq -er '.spec.ports[]|select(.port==9090)|.name')
proxy="/api/v1/namespaces/monitoring/services/http:$prom_service:$prom_port/proxy/api/v1"
prom() {
    local encoded
    encoded=$(jq -rn --arg q "$1" '$q|@uri')
    kubectl get --raw "$proxy/query?query=$encoded"
}
series() { prom "$1" | jq -e '.status=="success" and (.data.result | length)>0' >/dev/null; }
for metric in 'portfolio_pvc_used_bytes' 'certmanager_certificate_expiration_timestamp_seconds' \
    'kube_horizontalpodautoscaler_status_current_replicas' 'kube_pod_container_status_restarts_total'; do
    until_ok 150 series "${metric}{namespace=\"portfolio\"}"
done
rules=$(kubectl get --raw "$proxy/rules?type=alert")
jq -e '[.data.groups[] | select(.name=="portfolio.operations") | .rules[] | select(.health=="ok")] | length==4' <<<"$rules"
if [[ -n ${TELEGRAM_BOT_TOKEN:-} && -n ${TELEGRAM_CHAT_ID:-} ]]; then
    delivered() { series 'sum(alertmanager_notifications_total{integration="telegram"}) - (sum(alertmanager_notifications_failed_total{integration="telegram"}) or vector(0)) > 0'; }
    until_ok 180 delivered
    echo 'Telegram receiver acknowledged notification'
else
    echo 'Telegram delivery was not exercised (credentials unavailable to this run)'
fi
k delete prometheusrule test-notification
if [[ -n ${TELEGRAM_BOT_TOKEN:-} && -n ${TELEGRAM_CHAT_ID:-} ]]; then
    k patch alertmanagerconfig lab-route --type=json -p='[{"op":"remove","path":"/spec/receivers/0/telegramConfigs"}]'
    k delete secret lab-telegram
fi
