# DNS

Из одного Pod запрос к ошибочному имени smkoe.smoke.svc.cluster.local завершается ошибкой разрешения. Исправляем имя на smoke.smoke.svc.cluster.local и проверяем ответ /healthz по HTTP из того же Pod. Проверка изнутри кластера отделяет ошибку имени сервиса от проблем внешнего ingress.

Воспроизведение: `bash scripts/k3s/incidents.sh`. Протокол прогона: артефакт k3s-evidence, incidents.log.
