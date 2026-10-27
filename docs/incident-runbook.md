# Runbook: неуспешное обновление образа

Это инструкция для воспроизведения, **не отчёт об уже случившемся инциденте**. Подтверждённые проверки перечислены в [verification.md](verification.md).

## Цель

В учебном кластере изменить образ Deployment на несуществующий, найти причину по Pod/events и восстановить предыдущую ревизию. До эксперимента приложение должно пройти `python scripts/lab.py verify`.

## Симптом → факты → причина

1. Новая реплика не становится Ready, обновление Deployment не завершается.
2. `kubectl describe pod` и events показывают ошибку получения образа; состояние может сначала быть `ErrImagePull`, затем `ImagePullBackOff`.
3. Сравниваем `.spec.containers[].image` в Pod с ожидаемым `ops-lab:1.0.0`.
4. Для данного упражнения причина задана явно: `registry.invalid/ops-lab:missing` не существует и не может быть загружен. В реальной среде похожий симптом также возможен при неверном теге, проблемах сети/доступа к registry или отсутствии imagePullSecrets — без событий нельзя выбирать причину наугад.
5. Минимальное исправление упражнения: откат предыдущего изменения образа через Deployment, а не удаление случайных Pod.

## Команды вручную

**Windows Git Bash, из корня проекта**; Linux Bash — те же команды. Используется kubeconfig только этого стенда:

```bash
kubectl --kubeconfig .local/kubeconfig --context kind-ops-lab -n ops-lab get pods -o wide
kubectl --kubeconfig .local/kubeconfig --context kind-ops-lab -n ops-lab describe pods -l app=ops-lab
kubectl --kubeconfig .local/kubeconfig --context kind-ops-lab -n ops-lab get events --sort-by=.metadata.creationTimestamp
kubectl --kubeconfig .local/kubeconfig --context kind-ops-lab -n ops-lab rollout history deployment/ops-lab
```

После подтверждения ошибочного образа, **только в данном учебном эксперименте**:

```bash
kubectl --kubeconfig .local/kubeconfig --context kind-ops-lab -n ops-lab rollout undo deployment/ops-lab
kubectl --kubeconfig .local/kubeconfig --context kind-ops-lab -n ops-lab rollout status deployment/ops-lab --timeout=180s
python scripts/lab.py verify
```

На Linux в последней команде используйте `python3`.

## Критерии восстановления

- Rollout завершился; Deployment использует `ops-lab:1.0.0`.
- Доступны две реплики.
- `/`, `/healthz`, `/readyz` отвечают HTTP 200 с ожидаемым JSON.
- Версия и имя сервиса в ответе правильные; одного сообщения `deployment successfully rolled out` недостаточно.

## Дополнительное наблюдение

При `maxUnavailable: 0` и `maxSurge: 1` ошибочная новая реплика не должна вытеснять две исправные старые реплики. Проверьте это по Pod и ответам сервиса. Не называйте неудачный rollout полной недоступностью, если старые реплики продолжали работать.

## Документация

- [kind: загрузка Docker-образов и imagePullPolicy](https://kind.sigs.k8s.io/docs/user/quick-start/#loading-an-image-into-your-cluster)
- [Kubernetes: Deployments и rollback](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/)
- [Kubernetes: HTTP probes](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/)
