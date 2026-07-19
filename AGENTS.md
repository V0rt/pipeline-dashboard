# AGENTS.md — Installation instructions for Hermes agents

## Overview

Pipeline Dashboard — real-time web dashboard for the Hermes Pipeline Plugin.
Displays pipeline runs with agent flow, kanban board, and live SSE updates.

## Repository

- **URL**: https://github.com/V0rt/pipeline-dashboard
- **Local path**: `~/git/pipeline-dashboard/`

## Requirements

- Hermes CLI with `kanban` subcommand (Pipeline Plugin v2.2+)
- Python 3.11+
- Port 8800 available (configurable via `--port`)

## Quick Install (agent)

```bash
cd ~/git/pipeline-dashboard
python3 -m pip install -r requirements.txt
```

## Starting the Server

```bash
cd ~/git/pipeline-dashboard
python3 server.py
# Opens http://localhost:8800
```

Or via run script:

```bash
./run.sh                    # :8800
./run.sh 8888               # custom port
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Dashboard HTML |
| `GET /api/tasks` | All tasks with enriched child data |
| `GET /api/refresh` | Force refresh (cached) |
| `GET /api/events` | SSE stream for live updates |

## Verifying the Server is Running

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8800/
# Expected: 200
```

## Project Structure

```
pipeline-dashboard/
├── server.py            # FastAPI backend + SSE
├── static/
│   └── index.html       # SPA kanban dashboard
├── AGENTS.md            # This file — agent installation guide
├── requirements.txt
├── .gitignore
├── pyproject.toml
├── .github/workflows/ci.yml
└── run.sh
```

## Notes

- The kanban board must exist before data appears. Run a pipeline with `pipeline_save()` to populate the board.
- Enriched task data is cached in-memory (invalidated when task list changes).
- CSP headers: `default-src 'self'` — inline scripts require `'unsafe-inline'`.