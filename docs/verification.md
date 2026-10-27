# Статус проверок

## Текущий итог: развёртывание и откат проверены

На реальном стенде Windows 11 → Arch WSL2 → Docker → kind выполнены:

- Docker Engine 29.9.2 и запуск hello-world; Buildx 0.38.1.
- Сборка ops-lab:1.0.0 и загрузка образа в оба узла kind 0.33.0.
- Control-plane и worker Ready, Kubernetes/kubectl v1.34.11.
- Server-side dry-run, применение манифестов и две готовые реплики.
- HTTP 200 по /, /healthz, /readyz через port-forward, в том числе из Windows.
- DNS имени ops-lab.ops-lab.svc.cluster.local и HTTP 200 через ClusterIP из Pod.
- Реальный ImagePullBackOff после установки registry.invalid/ops-lab:missing; причина видна в events: не разрешается имя registry.
- Старые реплики продолжали отвечать во время ошибки обновления.
- kubectl rollout undo, возврат к ops-lab:1.0.0 и повторные Ready/HTTP-проверки.
- Все **16 тестов**, lint и форматирование прошли на Windows и Linux. В первоначальном локальном прогоне до исправления CI было 15 тестов; исходные выдержки ниже сохранены как история.

Выдержки реального вывода — [evidence/local-run.txt](evidence/local-run.txt). Повторный запуск на текущей машине — [wsl-local.md](wsl-local.md).

**Не проверены:** нагрузка, балансировка, HA, длительная работа и доступ с другой машины. Результаты GitHub Actions приведены отдельно ниже: локальная проверка сама по себе не доказывает успешность CI. Обе реплики находятся на одном worker.

### GitHub Actions: реальная проверка на GitHub

Успешный запуск: [40482928884](https://github.com/nikit0id/kubernetes-ops-lab/actions/runs/40482928884), коммит кода `1aafb62165e7390ef950817c96abcf8f9d51ba6a`. Оба jobs — checks и kubernetes — завершились success. В логах подтверждены 16 тестов, Docker build, два узла, HTTP smoke, реальный ImagePullBackOff, rollback и повторная проверка. Метаданные результата сохранены в [evidence/github-actions.json](evidence/github-actions.json).

[Первый запуск 40481844490](https://github.com/nikit0id/kubernetes-ops-lab/actions/runs/40481844490) обнаружил ошибку ожидания после отката: kubectl wait выбирал все Pod с app=ops-lab, включая неудачный Pod предыдущей ревизии. Откат выполнился, две исправные реплики были Ready, но ожидание завершилось таймаутом. Проверка заменена на ограниченный по времени опрос живого списка Pod: нужны две Ready-реплики ожидаемого образа, без deletionTimestamp. Устаревшие неудачные и удаляемые Pod больше не блокируют проверку. Сохранена проверка реальной готовности при переходе от одной готовой реплики к двум; добавлен регрессионный сценарий со смешанным набором Pod.

### Исправление гонки при запуске

После рестарта WSL Deployment кратковременно сохранял успешный rollout status, хотя Pod были ещё 0/1 Ready. Smoke мог завершиться RemoteDisconnected. В scripts/lab.py добавлены ожидание Ready для Pod и повтор при временном закрытии соединения. Два новых регрессионных теста до исправления дали FAIL и ERROR; после исправления прошли все 15 тестов, повторный запуск и реальный сценарий отката.

### Установка и ресурсы

Базы pacman ссылались на старые пакеты, зеркало возвращало 404. Полное обновление Arch не выполнялось. Официальные статические бинарники Docker установлены в /opt/ops-lab/docker-29.9.2, отдельный сервис — ops-lab-docker.service, данные — /var/lib/ops-lab-docker. Автозапуск не включён, пользователь не добавлялся в группу docker; сокет доступен root. Проверки выполнялись от root внутри WSL. Windows Firewall не менялся; Docker создал стандартные сетевые правила Linux.

Для kind, kubectl и Buildx сверены SHA-256 издателей. Для Docker Engine официальный каталог не предоставлял checksum-sidecar: архив получен по HTTPS, локальный digest записан в /opt/ops-lab/installation-checksums.json; это не проверка подписи издателя. Статическая установка не обеспечивает автоматических обновлений безопасности и выбрана для лабораторной.

Linux venv тестов размещён в /opt/ops-lab/test-venv. Попытка установки большого количества schema-файлов в venv на /mnt/c оказалась слишком медленной; рабочее окружение создано на Linux FS.

Снимок без нагрузки, 2026-10-27T16:16:05+07:00:

```text
Mem:   total 3.5Gi, used 1.2Gi, available 2.3Gi
Swap:  total 4.0Gi, used 0B
5.2G   /var/lib/ops-lab-docker
359M   /opt/ops-lab
```

Размер /opt/ops-lab включает venv тестов. Кеш загрузок, исходный дистрибутив WSL и прочие файлы Windows сюда не входят. Это замер, не гарантия пикового потребления.

### Сетевые наблюдения

После рестарта узлов наблюдались временные DNS-ошибки. Проверены CoreDNS readiness, EndpointSlice, маршруты, kube-proxy и bridge sysctls; настройки сети не менялись. После стабилизации прошли прямые DNS-запросы к Service и обоим CoreDNS Pod, HTTP через ClusterIP и запрос по DNS-имени. Точная причина первоначального временного сбоя отдельно не установлена. Port-forward проверен отдельно и не использован как доказательство работы ClusterIP.

---

## История: первоначальная проверка до установки инструментов

Далее сохранены исходные результаты. Они отражают ранний этап и **не описывают текущее состояние** стенда.

### Выполнено первоначально

Среда: Windows 11, Git Bash. Проверки проекта запускались на Python 3.14.4 и отдельно на Python 3.13.13 через `uv run --no-project --python 3.13 --with-requirements requirements-dev.txt`.

```text
python -m unittest discover -s tests -v
Ran 13 tests
OK

python -m ruff check .
All checks passed!

python -m ruff format --check .
6 files already formatted

GitHub Actions workflow: actionlint passed
```

Workflow проверен `actionlint` v1.7.12 (без дополнительных shellcheck/pyflakes); это проверка конфигурации, а не выполнение CI.

Эти выдержки отражают результаты реальных локальных запусков, а не пример предполагаемого вывода.

Что подтверждают проверки:

- Реальный HTTP-сервер запускается на loopback со свободным портом; `/`, `/healthz`, `/readyz` и неизвестный URL возвращают ожидаемые ответы.
- Проверены Content-Type, Content-Length и обработка query string для probes.
- Namespace, Deployment и Service проходят строгую JSON Schema-проверку для Kubernetes 1.34.
- Автоматически проверяются две ноды в kind-конфигурации, две реплики, согласованность selector/labels/ports, probes, ресурсы и основные ограничения безопасности.
- Команды kubectl формируются с явными kubeconfig, контекстом и namespace; внесение ошибки требует специального флага; откат здорового образа отклоняется.

### Что было заблокировано первоначально

| Проверка | Статус и причина |
|---|---|
| Docker build и запуск контейнера | Не запускались: Docker отсутствует |
| Создание двух узлов kind | Не запускалось: нет Docker/kind/kubectl |
| Server-side dry-run и применение манифестов | Не запускались: нет кластера |
| HTTP через Kubernetes port-forward | Не запускался |
| Реальный ImagePullBackOff и rollout undo | Не воспроизводились |
| GitHub Actions | Workflow подготовлен, но на момент создания проекта не запускался |

Реальный `python scripts/lab.py doctor` вернул:

```text
ERROR: Missing tools: docker, kind, kubectl
```

Попытка запуска имеющегося дистрибутива WSL `archlinux` завершилась `HCS_E_HYPERV_NOT_INSTALLED`. Система сообщает о недоступности WSL2 в текущей конфигурации и необходимости Virtual Machine Platform/виртуализации. Это блокер среды, а не установленная ошибка проекта. Настройки Windows, BIOS и firewall не изменялись, перезагрузка не выполнялась.

### Первоначальный план проверки CI — выполнен

Репозиторий опубликован, jobs checks и kubernetes прошли; ссылка и проверенный SHA приведены в разделе GitHub Actions выше. Пользователю важно самостоятельно разобрать команды и причины состояний перед описанием проекта на собеседовании.
