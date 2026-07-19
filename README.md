# Pipeline Dashboard

Real-time web dashboard for [Hermes Pipeline Plugin](https://hermes-agent.nousresearch.com/docs).
Shows agent states, kanban tasks, and pipeline flow — updated live via SSE.

![Pipeline Dashboard screenshot](./screenshot.png)

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 server.py
```

Open **http://localhost:8800**

## Features

- **Pipeline list** — sidebar with all 🔷 Pipeline runs, agent progress bars
- **Agent flow** — visual pipeline showing agents in sequence (🔍 → 🔧 → 👁 → 🧪)
- **Kanban board** — 6 columns: Todo, Ready, Running, Blocked, Done, Crashed
- **Real-time updates** — SSE pushes task changes to the browser with 2s polling
- **Dark theme** — matches the terminal dashboard aesthetic
- **Cached enrichment** — child task resolution cached; invalidates on status change
- **Heartbeat** — SSE `ping` events every 6s prevent connection drops

## Architecture

```
┌─────────────┐     SSE /api/events     ┌──────────────┐
│  Hermes CLI  │ ←───────poll─────────→  │  Dashboard   │
│  kanban list │     every 2 seconds     │  Server      │
│  kanban show │                         │  FastAPI     │
└─────────────┘                         └──────┬───────┘
                                               │
                                    GET /  → index.html
                                    GET /api/tasks     → JSON
                                    GET /api/refresh   → JSON (cached)
                                    GET /api/events    → SSE stream
                                    GET /favicon.ico   → 204
```

### Caching

The server caches enriched task data (resolved child objects)
in memory. The cache key includes each task's **id + title + status**,
so any status change invalidates the cache and triggers an SSE update.

## API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /` | HTML | Dashboard SPA (dark theme kanban) |
| `GET /api/tasks` | JSON | All tasks with full details, resolved children |
| `GET /api/refresh` | JSON | Force refresh (cached, ~0.3s) |
| `GET /api/events` | SSE | Real-time event stream: `task_update`, `task_removed`, `ping` |
| `GET /favicon.ico` | 204 | No favicon |

### SSE Events

```json
// task_update — sent when a task is created or changes
event: task_update
data: {"type":"task_update","task_id":"t_xxxx","data":{...},"timestamp":"..."}

// task_removed — sent when a task is deleted
event: task_removed
data: {"type":"task_removed","task_id":"t_xxxx","timestamp":"..."}

// ping — heartbeat every 6s to keep connection alive
event: ping
data: {"timestamp":"..."}
```

## Requirements

- Python 3.11+
- `fastapi`, `uvicorn`, `sse-starlette` (see `requirements.txt`)
- `hermes` CLI with `kanban` subcommand
- Pipeline Plugin with `pipeline` board created

## Configuration

| Argument | Default | Description |
|----------|---------|-------------|
| `--port` | 8800 | HTTP port |
| `--host` | 0.0.0.0 | Listen address |

```bash
python3 server.py --port 8888 --host 127.0.0.1
```

Or via run script:

```bash
./run.sh          # :8800
./run.sh 8888     # :8888
```

## Project Structure

```
pipeline-dashboard/
├── server.py              # FastAPI backend + SSE event stream
├── static/
│   └── index.html         # SPA kanban dashboard (vanilla JS)
├── AGENTS.md              # Hermes agent installation guide
├── README.md              # This file
├── requirements.txt
├── .gitignore
├── pyproject.toml
├── .github/workflows/ci.yml
└── run.sh
```

## Notes

- The `pipeline` kanban board must exist before data appears.
  Run `hermes kanban boards create pipeline` if needed.
- Enriched task data is cached in-memory (auto-invalidated on task list change).
- CSP headers: `default-src 'self'` with `'unsafe-inline'` for inline scripts.
- Task statuses tracked: `todo`, `ready`, `running`, `blocked`, `done`, `crashed`.