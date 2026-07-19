# Pipeline Dashboard

Минимальный real-time дашборд для kanban-доски Hermes Pipeline Plugin.
Читает `kanban.db` напрямую через SQLite — без subprocess, без внешних зависимостей.

## Принцип

- Прямое чтение `~/.hermes/kanban/boards/pipeline/kanban.db` через sqlite3
- Находит pipeline-задачи (parents с детьми через task_links)
- Показывает progress bar (done/total), статусы, assignee
- SSE real-time обновления
- Чистый Python stdlib — ноль зависимостей

## Запуск

```bash
cd ~/git/pipeline-dashboard
python3 server.py                # 0.0.0.0:8800
HOST=127.0.0.1 PORT=8801 python3 server.py  # кастомный порт
```

## API

| Endpoint | Описание |
|---|---|
| `GET /` | HTML-дашборд |
| `GET /api/tasks` | JSON-дерево pipeline-задач |
| `GET /api/events` | SSE (`full_update` при изменениях) |

## Поток данных

```
kanban.db ──sqlite3──→ server.py ──SSE──→ index.html
                         ↑
                   только SELECT,
                   ни одного subprocess
```
