"""
Pipeline Dashboard — FastAPI server with SSE real-time updates.
Wraps `hermes kanban --board pipeline` commands.
"""

import argparse
import asyncio
import json
import subprocess
from datetime import datetime
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

app = FastAPI(title="Pipeline Dashboard", version="0.1.0")

BOARD = "pipeline"
POLL_INTERVAL = 1.5  # seconds between polls
HERMES_CMD = ["hermes", "kanban", "--board", BOARD]

# ── Kanban CLI helpers ──────────────────────────────────────────────────────


def _kanban(*args: str) -> Any:
    """Run hermes kanban --board pipeline <args> --json, return parsed result."""
    cmd = [*HERMES_CMD, *args, "--json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return None
        out = r.stdout.strip()
        if not out:
            return None
        return json.loads(out)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return None


def list_tasks() -> list[dict]:
    """List all tasks on the pipeline board."""
    result = _kanban("list")
    if isinstance(result, list):
        return result
    return []


def show_task(task_id: str) -> dict | None:
    """Show full task details including parents, children, comments, events."""
    return _kanban("show", task_id)


# ── API endpoints ────────────────────────────────────────────────────────────


@app.get("/api/tasks")
async def get_tasks():
    """Return all tasks with full details (children, status)."""
    tasks = list_tasks()
    enriched = []
    for t in tasks:
        detail = show_task(t["id"])
        if detail:
            enriched.append(detail)
        else:
            enriched.append({"task": t, "children": [], "parents": [], "comments": [], "events": []})
    return JSONResponse(enriched)


@app.get("/api/events")
async def event_stream(request: Request):
    """SSE endpoint — streams task updates in real time."""

    async def event_generator():
        previous = {}
        while True:
            if await request.is_disconnected():
                break
            tasks = list_tasks()
            # Build a snapshot keyed by id
            snapshot = {}
            for t in tasks:
                detail = show_task(t["id"])
                if detail:
                    snapshot[t["id"]] = detail

            # Detect changes
            for tid, data in snapshot.items():
                prev = previous.get(tid)
                if prev != data:
                    event_data = json.dumps({
                        "type": "task_update",
                        "task_id": tid,
                        "data": data,
                        "timestamp": datetime.now().isoformat(),
                    })
                    yield {"event": "task_update", "data": event_data}

            # Detect deletions
            for tid in list(previous.keys()):
                if tid not in snapshot:
                    event_data = json.dumps({
                        "type": "task_removed",
                        "task_id": tid,
                        "timestamp": datetime.now().isoformat(),
                    })
                    yield {"event": "task_removed", "data": event_data}

            previous = snapshot
            await asyncio.sleep(POLL_INTERVAL)

    return EventSourceResponse(event_generator())


@app.get("/api/refresh")
async def refresh():
    """Force a full refresh of all tasks."""
    tasks = list_tasks()
    enriched = []
    for t in tasks:
        detail = show_task(t["id"])
        if detail:
            enriched.append(detail)
    return JSONResponse(enriched)


# ── Serve frontend ──────────────────────────────────────────────────────────


@app.get("/")
async def index():
    with open("static/index.html") as f:
        return HTMLResponse(f.read())


def main():
    """Entry point for CLI."""
    parser = argparse.ArgumentParser(description="Pipeline Dashboard")
    parser.add_argument("--port", type=int, default=8800, help="Port (default: 8800)")
    parser.add_argument("--host", default="0.0.0.0", help="Host (default: 0.0.0.0)")
    args = parser.parse_args()
    uvicorn.run("server:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()