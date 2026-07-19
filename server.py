"""Pipeline Dashboard — FastAPI server with SSE real-time updates.
Wraps `hermes kanban --board pipeline` commands.
"""

import argparse
import asyncio
import html as html_module
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse

app = FastAPI(title="Pipeline Dashboard", version="0.1.3")

BOARD = "pipeline"
POLL_INTERVAL = 2.0  # seconds between polls
HERMES_CMD = ["hermes", "kanban", "--board", BOARD]

# ── Enriched data cache ─────────────────────────────────────────────────────

_enriched_cache: list[dict] | None = None
_enriched_cache_key: str | None = None


def _build_cache_key(tasks: list[dict]) -> str:
    """Build a cache key from the current task list IDs + titles + statuses."""
    return json.dumps(
        [{"id": t.get("id"), "title": t.get("title"), "status": t.get("status")} for t in tasks],
        sort_keys=True,
    )


def _get_enriched_cached(tasks: list[dict]) -> list[dict]:
    """Return cached enriched data if the task list hasn't changed."""
    global _enriched_cache, _enriched_cache_key
    key = _build_cache_key(tasks)
    if _enriched_cache is not None and _enriched_cache_key == key:
        return _enriched_cache
    enriched = _do_enrich_tasks(tasks)
    _enriched_cache = enriched
    _enriched_cache_key = key
    return enriched


def esc(s: str) -> str:
    """HTML-escape a string (XSS guard)."""
    return html_module.escape(str(s), quote=True)


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


def enrich_task(detail: dict | None) -> dict | None:
    """Resolve child IDs → full child objects in a task detail."""
    if not detail or "task" not in detail:
        return detail
    children = detail.get("children", [])
    resolved = []
    for c in children:
        if isinstance(c, str):
            child_detail = show_task(c)
            if child_detail and "task" in child_detail:
                resolved.append(child_detail["task"])
            else:
                resolved.append({"id": c, "title": c, "status": "unknown"})
        elif isinstance(c, dict):
            resolved.append(c)
    detail["children"] = resolved
    return detail


def _do_enrich_tasks(tasks: list[dict]) -> list[dict]:
    """Enrich a list of task details (resolve child IDs) — no caching."""
    enriched = []
    for t in tasks:
        detail = show_task(t["id"])
        if detail:
            enriched.append(enrich_task(detail))
        else:
            enriched.append({"task": t, "children": [], "parents": [], "comments": [], "events": []})
    return enriched


# ── API endpoints ────────────────────────────────────────────────────────────


@app.get("/api/tasks")
async def get_tasks():
    """Return all tasks with full details and resolved children."""
    tasks = list_tasks()
    return JSONResponse(_get_enriched_cached(tasks))


@app.get("/api/events")
async def event_stream(request: Request):
    """SSE endpoint — streams task updates in real time."""

    async def event_generator():
        previous = {}
        poll_count = 0
        while True:
            if await request.is_disconnected():
                break
            tasks = list_tasks()
            # Use cached enrichment for the entire snapshot
            enriched = _get_enriched_cached(tasks)
            snapshot = {e["task"]["id"] if "task" in e and e["task"] else e.get("id"): e for e in enriched}

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

            for tid in list(previous.keys()):
                if tid not in snapshot:
                    yield {"event": "task_removed", "data": json.dumps({
                        "type": "task_removed", "task_id": tid,
                        "timestamp": datetime.now().isoformat(),
                    })}

            previous = snapshot
            poll_count += 1
            # Heartbeat ping every ~3 polls (~6s) to keep connection alive
            if poll_count % 3 == 0:
                yield {"event": "ping", "data": json.dumps({"timestamp": datetime.now().isoformat()})}
            await asyncio.sleep(POLL_INTERVAL)

    return EventSourceResponse(event_generator())


@app.get("/api/refresh")
async def refresh():
    """Force a full refresh of all tasks."""
    tasks = list_tasks()
    return JSONResponse(_get_enriched_cached(tasks))


# ── Serve frontend ──────────────────────────────────────────────────────────


_BASE_DIR = Path(__file__).resolve().parent
INDEX_HTML: str | None = None


def load_index() -> str:
    """Load index.html once, cache in memory."""
    global INDEX_HTML
    if INDEX_HTML is None:
        INDEX_HTML = (_BASE_DIR / "static" / "index.html").read_text()
    return INDEX_HTML


@app.get("/")
async def index():
    return HTMLResponse(
        load_index(),
        headers={
            "Content-Security-Policy": "default-src 'self'; style-src 'unsafe-inline' 'self'; script-src 'unsafe-inline' 'self'; img-src 'self' data:; connect-src 'self'",
            "Cache-Control": "no-cache, max-age=0",
        },
    )


@app.get("/favicon.ico")
async def favicon():
    return HTMLResponse(status_code=204)


def main():
    """Entry point for CLI."""
    parser = argparse.ArgumentParser(description="Pipeline Dashboard")
    parser.add_argument("--port", type=int, default=8800, help="Port (default: 8800)")
    parser.add_argument("--host", default="0.0.0.0", help="Host (default: 0.0.0.0)")
    args = parser.parse_args()
    uvicorn.run("server:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()