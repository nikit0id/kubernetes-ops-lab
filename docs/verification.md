# Статус проверок

## Выполнено локально

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

## Не выполнено

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

## Как закрыть оставшиеся проверки

На машине с работающим Docker, kind и kubectl выполнить команды из README либо опубликовать репозиторий в GitHub и дождаться успешных jobs `checks` и `kubernetes`. Сохранить ссылку на реальный workflow run и обновить этот документ по его результатам.

До этого корректное описание проекта: **«Подготовил HTTP-сервис, Dockerfile, манифесты Kubernetes, тесты и сценарий диагностики/отката; локально проверил HTTP и схемы манифестов»**. Утверждать завершённое развёртывание и восстановление на основании одних конфигурационных файлов нельзя.
