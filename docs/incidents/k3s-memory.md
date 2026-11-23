# OOMKilled

Pod с лимитом 32 MiB пытается выделить 96 MiB. Причину завершения проверяем в `.status.containerStatuses[0].state.terminated.reason`. После установки лимита 160 MiB повторяем ту же команду и проверяем Succeeded. В рабочем сервисе размер лимита выбирают по профилю потребления памяти.

Воспроизведение: `bash scripts/k3s/incidents.sh`. Протокол прогона: артефакт k3s-evidence, incidents.log.
