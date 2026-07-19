# Pipeline Dashboard

Real-time web dashboard for [Hermes Pipeline Plugin](https://github.com/akrhin/hermes-pipeline-plugin).
Shows agent states, kanban tasks, and pipeline flow — updated live via SSE.

## Quick Start

```bash
cd ~/git/pipeline-dashboard
pip install -r requirements.txt
python3 server.py
```

Open http://localhost:8800

## Features

- **Pipeline list** — sidebar with all 🔷 Pipeline runs, agent progress
- **Agent flow** — visual pipeline showing agents in sequence (🔍 → 🔧 → 👁 → 🧪)
- **Kanban board** — 5 columns: Ready, Running, Blocked, Done, Crashed
- **Real-time updates** — SSE pushes task changes to the browser instantly
- **Dark theme** — matches the terminal dashboard aesthetic

## How it works

The server wraps `hermes kanban --board pipeline` CLI commands and exposes:

| Endpoint | Description |
|----------|-------------|
| `GET /api/tasks` | All tasks with full details |
| `GET /api/events` | SSE stream — real-time task updates |
| `GET /api/refresh` | Force full refresh (cached) |

## Requirements

- Python 3.11+
- `fastapi`, `uvicorn`, `sse-starlette` (in `requirements.txt`)
- `hermes` CLI with `kanban` subcommand
- Pipeline Plugin v2.2+ with `pipeline` board created

## Project structure

```
pipeline-dashboard/
├── server.py          # FastAPI backend + SSE
├── static/
│   └── index.html     # Single-page kanban dashboard
├── requirements.txt
└── README.md
```