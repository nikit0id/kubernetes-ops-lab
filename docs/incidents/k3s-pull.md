# ImagePullBackOff

У тестового Deployment отсутствуют готовые реплики. Проверка `kubectl describe pod` и событий показала попытки загрузить отсутствующий тег. Команда `kubectl set image` вернула образ, импортированный на все узлы. Проверка исправления: завершившийся rollout. Предотвращение: проверка существования digest до promotion.

Воспроизведение: `bash scripts/k3s/incidents.sh`. Протокол прогона: артефакт k3s-evidence, incidents.log.
