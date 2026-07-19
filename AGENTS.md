# AGENTS.md — Hermes Agent Installation Guide

## Overview

Pipeline Dashboard — real-time web dashboard for the Hermes Pipeline Plugin.
Displays pipeline runs with agent flow, kanban board, and live SSE updates.

## Repository

- **URL**: https://github.com/V0rt/pipeline-dashboard
- **Local path**: `~/git/pipeline-dashboard/`

## Requirements

- Hermes CLI with `kanban` subcommand (Pipeline Plugin v2.2+)
- `hermes kanban boards create pipeline` (one-time setup)
- Python 3.11+
- Port 8800 available (configurable via `--port`)

## Quick Install

```bash
cd ~/git/pipeline-dashboard
pip install -r requirements.txt
```

## Starting the Server

```bash
cd ~/git/pipeline-dashboard
python3 server.py --port 8800
```

Or via run script:

```bash
./run.sh                    # :8800
./run.sh 8888               # custom port
```

## Verifying

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8800/
# → 200

curl -s http://localhost:8800/api/refresh | python3 -m json.tool | head -5
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Dashboard HTML |
| `GET /api/tasks` | All tasks with enriched child data |
| `GET /api/refresh` | Force refresh (cached, ~0.3s) |
| `GET /api/events` | SSE stream for live updates |

## Project Structure

```
pipeline-dashboard/
├── server.py              # FastAPI backend + SSE
├── static/
│   └── index.html         # SPA kanban dashboard
├── AGENTS.md              # This file
├── README.md              # User documentation
├── requirements.txt
├── .gitignore
├── pyproject.toml
├── .github/workflows/ci.yml
└── run.sh
```

## Notes

- The `pipeline` kanban board must exist via `hermes kanban boards create pipeline`.
- Task data is cached in-memory (invalidates on status change).
- CSP headers: `default-src 'self'` (inline scripts with `'unsafe-inline'`).
- Supports statuses: `todo`, `ready`, `running`, `blocked`, `done`, `crashed`.