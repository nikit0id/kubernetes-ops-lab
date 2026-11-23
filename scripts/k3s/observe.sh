#!/usr/bin/env bash
source scripts/k3s/common.sh
k create configmap pvc-metrics-code --from-file=scripts/k3s/pvc_metrics.py
kubectl apply -f k3s/storage-metrics.yaml
# Discover the controller Service port so this selector follows the installed chart.
kubectl -n cert-manager get svc cert-manager -o json | jq '{
  apiVersion:"monitoring.coreos.com/v1",kind:"ServiceMonitor",
  metadata:{name:"lab-certificates",namespace:"cert-manager",labels:{release:"monitoring"}},
  spec:{selector:{matchLabels:.metadata.labels},endpoints:[
    {port:(.spec.ports[]|select(.port==9402)|.name),interval:"30s"}]}}' | kubectl apply -f -
kubectl apply -f k3s/alerts.yaml
kubectl -n monitoring create cm portfolio-dashboards --from-file=k3s/dashboards
kubectl -n monitoring label cm portfolio-dashboards grafana_dashboard=1
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
k delete prometheusrule test-notification
