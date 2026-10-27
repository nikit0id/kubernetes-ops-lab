# Kubernetes Ops Lab

Учебный DevOps-проект: Python HTTP-сервис в локальном двухузловом Kubernetes-кластере на **kind**. Контейнеризация, probes, ресурсы, диагностика `ImagePullBackOff` и откат Deployment.

**Статус:** HTTP-тесты и статические проверки выполнены локально. Docker/Kubernetes и GitHub Actions пока **не запускались**: на исходной Windows-машине нет Docker, WSL2 не стартует. Подробности — [docs/verification.md](docs/verification.md). Это учебный стенд, не production-инфраструктура.

## Что реализовано

- Python-сервис без сторонних runtime-зависимостей: `/`, `/healthz`, `/readyz`.
- Docker-образ с непривилегированным пользователем.
- kind: один control-plane и один worker, версия node image закреплена digest.
- Namespace `ops-lab`, Deployment с двумя репликами, Service типа ClusterIP.
- HTTP readiness/liveness probes, requests/limits CPU и памяти.
- Изолированный kubeconfig, явный контекст `kind-ops-lab` во всех командах скрипта.
- Контролируемая ошибка образа → сбор диагностических данных → `kubectl rollout undo` → повторная HTTP-проверка.
- CI: lint, тесты, проверка схем манифестов, сборка Docker, запуск kind и сценарий восстановления. Успех CI можно утверждать только после реального зелёного запуска.

## Структура

```text
app/server.py             HTTP-сервис
Dockerfile                контейнер без root
kind/cluster.yaml         два узла
k8s/                      Namespace, Deployment, Service
scripts/lab.py            команды стенда
tests/                    HTTP, манифесты, защитные проверки
.github/workflows/ci.yml   проверки и end-to-end сценарий
docs/                     разбор инцидента и статус проверок
```

## 1. Запуск без Docker

Нужен Python 3.13+; команды выполнять из корня репозитория.

**Windows, Git Bash** (не PowerShell и не WSL):

```bash
python -m app.server
```

**Linux / работающий WSL, Bash:**

```bash
python3 -m app.server
```

Сервис слушает `0.0.0.0:8080`, для локальной проверки откройте `http://127.0.0.1:8080/`. Остановка — Ctrl+C. Поле `hostname` позволяет видеть, какой контейнер ответил.

### Тесты на Windows, Git Bash

Если доступен `uv`:

```bash
uv venv .venv
uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt
./.venv/Scripts/python.exe -m unittest discover -s tests -v
./.venv/Scripts/python.exe -m ruff check .
./.venv/Scripts/python.exe -m ruff format --check .
```

Альтернатива для обычной установки Python с pip:

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
```

### Тесты на Linux, Bash

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
```

Проверка схем Kubernetes выполняется тестами через `kubernetes-validate`, в строгом режиме для Kubernetes 1.34. Это **не замена** запуску настоящего API-сервера.

## 2. Запуск Kubernetes-стенда

Нужны работающий Docker Engine в режиме Linux containers, kind **v0.33.0**, kubectl **v1.34.11**, Python 3.13+. Все инструменты должны быть доступны **в одной среде**. Для Windows удобен Docker Desktop с работающим WSL2/гипервизором; здесь его установка не автоматизируется. Не смешивайте Windows kubectl и kubeconfig из другой машины/WSL.

Официальные инструкции: [kind](https://kind.sigs.k8s.io/docs/user/quick-start/), [kubectl](https://kubernetes.io/docs/tasks/tools/), [Docker Desktop для Windows](https://docs.docker.com/desktop/setup/install/windows-install/).

Команды ниже — **Windows Git Bash**. На Linux используйте `python3` вместо `python` либо `.venv/bin/python`.

```bash
python scripts/lab.py doctor
python scripts/lab.py up
```

`up` собирает `ops-lab:1.0.0`, создаёт кластер `ops-lab`, загружает образ в узлы kind, создаёт Namespace, проверяет манифесты через `--dry-run=server` и применяет их. Затем проверяет два Ready-узла, две доступные реплики и три HTTP-endpoint через временный port-forward. Если кластер с таким именем уже существует, `up` откажется менять его.

Kubeconfig записывается только в `.local/kubeconfig` и исключён из Git. Если создание кластера оборвалось, изучите ошибку: повторный `up` может обнаружить частично созданный кластер и намеренно остановиться.

Для просмотра в браузере:

```bash
python scripts/lab.py port-forward
```

Откройте `http://127.0.0.1:8080/`; Ctrl+C завершает port-forward. Если порт занят, освободите его либо используйте `smoke`, который выбирает свободный localhost-порт автоматически:

```bash
python scripts/lab.py smoke
python scripts/lab.py verify
```

**Важно:** `kubectl port-forward service/...` перенаправляет запросы к выбранному Pod. Эта проверка не доказывает балансировку Service и работу полного сетевого пути через ClusterIP.

## 3. Диагностика и откат

Только на выделенном учебном кластере; команда намеренно меняет образ приложения на несуществующий:

```bash
python scripts/lab.py break-image --confirm-lab-failure
python scripts/lab.py rollback
```

Первый шаг проверяет исправный стенд, задаёт образ `registry.invalid/ops-lab:missing` и ждёт **реальный** `ImagePullBackOff`, после чего выводит Pod describe, events и историю Deployment. Если команда завершилась ошибкой или была прервана после смены образа, выполните `diagnose` и `rollback`.

```bash
python scripts/lab.py diagnose
```

Откат выполняет `kubectl rollout undo`, ждёт восстановления и повторяет HTTP-проверки. Он разрешён только если Deployment использует именно ошибочный образ данного упражнения.

Разбор причины и команды вручную — [docs/incident-runbook.md](docs/incident-runbook.md).

## 4. GitHub Actions

После публикации в GitHub workflow запускается на push и pull request; также доступен ручной запуск. Job `checks` проверяет Python и манифесты. Job `kubernetes` собирает образ, поднимает два узла на временном Ubuntu runner, проверяет приложение, воспроизводит ошибку и выполняет откат. Образы в registry не публикуются, внешние серверы не используются.

Если Actions отключены для аккаунта/репозитория или runner недоступен, end-to-end проверка не произойдёт. До успешного запуска не добавляйте в резюме утверждение об успешно пройденном CI или проверенном развёртывании этого репозитория.

## Ограничения и объяснение решений

- Два узла — не HA: control-plane один. Оба Pod обычно размещаются на единственном worker. Две реплики не означают устойчивость к потере worker.
- Образ загружается через `kind load docker-image`; `IfNotPresent` и явный тег позволяют использовать локальную копию.
- `readinessProbe` влияет на готовность Pod принимать трафик, `livenessProbe` — на перезапуск контейнера. У этого приложения нет БД или внешних зависимостей, поэтому обе проверки простые.
- `maxUnavailable: 0`, `maxSurge: 1`: при ошибочном обновлении старые реплики должны продолжить обслуживать запросы. Это ошибка rollout, не обязательно полный простой сервиса; результат нужно подтвердить на стенде.
- Стандартный `http.server` подходит для этой лабораторной, но не выбран как production application server.
- Python base image закреплён minor-тегом, но не digest: последующие сборки могут получать обновлённые слои. Прямые dev-зависимости закреплены, транзитивные — нет.
- Нет Ingress, TLS, мониторинга и внешней БД: они не нужны для заявленной цели.

## Что уметь объяснить на собеседовании

1. Зачем нужны Deployment и Service, если контейнер уже запускается в Docker?
2. Чем readiness отличается от liveness?
3. Почему локальный Docker-образ нужно загрузить в kind?
4. Где искать причину `ImagePullBackOff` и почему рестарт сам по себе её не устраняет?
5. Что меняет `rollout undo`, и как убедиться, что приложение действительно восстановилось?
