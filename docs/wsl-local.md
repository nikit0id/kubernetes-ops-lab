# Повторный запуск на текущей машине через WSL

Этот раздел описывает уже настроенную локальную среду. Обычные команды проекта и требования к новой машине — в основном README.

## Что установлено

| Компонент | Путь |
|---|---|
| Docker Engine 29.9.2 | `/opt/ops-lab/docker-29.9.2/docker/` |
| Docker CLI | `/usr/local/bin/docker`, ссылка на бинарник в `/opt/ops-lab` |
| kind и kubectl | `/usr/local/bin/` |
| Buildx | `/usr/local/lib/docker/cli-plugins/docker-buildx` |
| Сервис Docker | `/etc/systemd/system/ops-lab-docker.service` |
| Данные Docker | `/var/lib/ops-lab-docker` |
| Linux venv тестов | `/opt/ops-lab/test-venv` |

Это ручная установка официальных бинарников для лабораторной; версии не обновляются автоматически. Для обычной долгосрочной установки стоит использовать пакетный менеджер с согласованным обновлением Arch. Официальная инструкция: <https://docs.docker.com/engine/install/binaries/>.

## Открыть Linux-сеанс

**Windows: Git Bash или PowerShell** (это команда Windows, не Linux):

```text
wsl.exe --distribution archlinux --user root
```

Откроется root-сеанс **внутри Arch WSL**. Не закрывайте его, пока работаете со стендом: systemd-сервисы сами по себе не гарантируют, что WSL останется запущен после выхода из всех сеансов.

## Запустить ранее созданный стенд

**Внутри Arch WSL, Bash:**

```bash
systemctl start ops-lab-docker.service
docker start ops-lab-control-plane ops-lab-worker
cd /mnt/c/Users/Helchion/kubernetes-ops-lab
python scripts/lab.py verify
```

После полной остановки WSL запуск Kubernetes API и системных компонентов занимает время. Если первые команды kubectl получили `connection refused`, дождитесь запуска API и повторите `verify`. Не запускайте `up` поверх уже существующего кластера: он намеренно откажется это делать.

Для браузера:

```bash
python scripts/lab.py port-forward
```

Откройте **<http://127.0.0.1:8080/> в Windows**. Port-forward занимает передний план терминала; Ctrl+C завершает только перенаправление порта. Если другой port-forward уже слушает 8080, не запускайте второй: используйте существующий либо завершите его.

## Повторить проверки кода

**Внутри Arch WSL, из каталога проекта:**

```bash
/opt/ops-lab/test-venv/bin/python -m unittest discover -s tests -v
/opt/ops-lab/test-venv/bin/python -m ruff check .
/opt/ops-lab/test-venv/bin/python -m ruff format --check .
```

Linux venv хранится на Linux-файловой системе, а не на `/mnt/c`: установка пакета с большим количеством schema-файлов на NTFS оказалась заметно медленнее.

## Дополнительная проверка DNS и Service

После готовности системных компонентов:

```bash
kubectl --kubeconfig .local/kubeconfig --context kind-ops-lab -n kube-system wait --for=condition=Ready pods -l k8s-app=kube-dns --timeout=120s
kubectl --kubeconfig .local/kubeconfig --context kind-ops-lab -n ops-lab exec deployment/ops-lab -- python -c 'import socket,urllib.request; h="ops-lab.ops-lab.svc.cluster.local"; print(socket.gethostbyname(h)); print(urllib.request.urlopen("http://"+h+":8080/",timeout=5).read().decode())'
```

При старте наблюдались временные DNS-ошибки; готовность CoreDNS сама по себе не заменяет финальный запрос. Если ошибка сохраняется, смотрите системные Pod, endpoints, логи и маршруты, а не меняйте firewall наугад.

## Остановка без удаления данных

Когда больше не нужны запущенные контейнеры, сначала завершите port-forward, затем **внутри WSL**:

```bash
systemctl stop ops-lab-docker.service
```

Это остановит Docker и контейнеры, но не удалит образы и кластерные данные. Команды удаления кластера, образов, virtualenv или Docker data-root в ходе проверки не выполнялись. Автозапуск сервиса не включён; Windows Firewall не изменялся. Docker создал свои обычные сетевые правила внутри Linux.
