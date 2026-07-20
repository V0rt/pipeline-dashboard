import http.server
import json
import os
import socket
import sqlite3
import threading
import re
import urllib.parse

KANBAN_DB = os.path.expanduser("~/.hermes/kanban/boards/pipeline/kanban.db")
PORT = int(os.environ.get("PORT", 8800))
HOST = os.environ.get("HOST", "0.0.0.0")

# Global broadcast: set of (wfile, lock, wake_event) tuples for active SSE clients
_sse_clients: set[tuple] = set()
_sse_lock = threading.Lock()

STATUS_LABELS = {"triage": "Triage", "todo": "Todo", "ready": "Ready", "running": "Running",
                 "blocked": "Blocked", "done": "Done", "archived": "Archived"}
STATUS_COLORS = {"triage": "#94a3b8", "todo": "#64748b", "ready": "#3b82f6", "running": "#f59e0b",
                 "blocked": "#ef4444", "done": "#22c55e", "archived": "#78716c"}

def get_db():
    conn = sqlite3.connect(KANBAN_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def load_tree():
    """Build a flat task list with agent children nested inside parents.
    Shows ALL non-archived tasks — pipeline parents, orphans, and children."""
    conn = get_db()
    try:
        cursor = conn.cursor()

        # --- All non-archived tasks, ordered by status + recency ---
        cursor.execute("""
            SELECT id, title, body, assignee, status,
                   created_at, started_at, completed_at, priority
            FROM tasks
            WHERE status != 'archived'
            ORDER BY
                CASE status
                    WHEN 'running' THEN 0
                    WHEN 'ready' THEN 1
                    WHEN 'blocked' THEN 2
                    WHEN 'todo' THEN 3
                    WHEN 'triage' THEN 4
                    WHEN 'done' THEN 5
                    ELSE 6
                END,
                created_at DESC
        """)
        all_tasks = [dict(r) for r in cursor.fetchall()]
        task_map = {t["id"]: t for t in all_tasks}

        # --- Collect parent→children links ---
        cursor.execute("""
            SELECT parent_id, child_id FROM task_links
        """)
        links = cursor.fetchall()
        children_of = {}  # parent_id -> [child_id, ...]
        parents_of = {}   # child_id -> parent_id
        for row in links:
            p, c = row["parent_id"], row["child_id"]
            children_of.setdefault(p, []).append(c)
            parents_of[c] = p

        # --- Build result tree ---
        result = []
        seen = set()

        for task in all_tasks:
            tid = task["id"]

            # Skip if this task is a child — it'll be nested under its parent
            if tid in parents_of and parents_of[tid] in task_map:
                continue

            if tid in seen:
                continue
            seen.add(tid)

            # Fetch agent children
            child_ids = children_of.get(tid, [])
            children = []
            for cid in child_ids:
                if cid in task_map:
                    children.append(task_map[cid])
                    seen.add(cid)

            if children:
                # ── Pipeline parent ──
                total = len(children)
                done = sum(1 for c in children if c["status"] == "done")
                running = sum(1 for c in children if c["status"] == "running")
                task["children"] = children
                task["progress"] = {
                    "total": total, "done": done, "running": running,
                    "pct": round(done / total * 100) if total else 0
                }

                # Parse agent flow from body
                if task.get("body"):
                    m = re.search(r'Агенты:\s*([^\n]+)', task["body"])
                    if m:
                        task["agent_flow"] = [a.strip() for a in m.group(1).split("→")]
            else:
                # ── Orphan task (no children) ──
                task["children"] = []
                task["progress"] = {"total": 0, "done": 0, "running": 0, "pct": 0}

            result.append(task)

        return result
    except sqlite3.Error:
        raise
    finally:
        conn.close()


# ── Board Management Actions ──────────────────────────────────────────

def _notify_clients(tree=None):
    """Broadcast a full_update SSE event to all connected clients.
    Wakes each SSE client's per-thread event so N-1 clients are not starved.
    """
    global _sse_clients
    if tree is None:
        tree = load_tree()
    msg = f"event: full_update\ndata: {json.dumps(tree, ensure_ascii=False, sort_keys=True)}\n\n"
    data = msg.encode()
    with _sse_lock:
        dead = set()
        for entry in _sse_clients:
            wfile, lock, wake_event = entry
            with lock:
                try:
                    wfile.write(data)
                    wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    dead.add(entry)
                    continue
            # Wake the per-thread event so this client's SSE handler re-checks
            wake_event.set()
        _sse_clients -= dead


def delete_task(task_id):
    """DELETE task row + all links, comments, events, runs, attachments."""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM task_links      WHERE parent_id=? OR child_id=?", (task_id, task_id))
        cur.execute("DELETE FROM task_comments    WHERE task_id=?", (task_id,))
        cur.execute("DELETE FROM task_events      WHERE task_id=?", (task_id,))
        cur.execute("DELETE FROM task_attachments WHERE task_id=?", (task_id,))
        cur.execute("DELETE FROM kanban_notify_subs WHERE task_id=?", (task_id,))
        cur.execute("DELETE FROM task_runs WHERE task_id=?", (task_id,))
        cur.execute("DELETE FROM tasks            WHERE id=?", (task_id,))
        conn.commit()
        _notify_clients()
        return {"ok": True, "action": "deleted", "id": task_id}
    except sqlite3.Error as e:
        return {"ok": False, "error": str(e)}
    finally:
        conn.close()


def archive_task(task_id):
    """Archive task (set status='archived'). Also archives all children."""
    conn = get_db()
    cur = conn.cursor()
    try:
        # Archive children first
        cur.execute("""
            UPDATE tasks SET status='archived', completed_at=unixepoch()
            WHERE id IN (SELECT child_id FROM task_links WHERE parent_id=?)
        """, (task_id,))
        cur.execute("UPDATE tasks SET status='archived', completed_at=unixepoch() WHERE id=?", (task_id,))
        conn.commit()
        _notify_clients()
        return {"ok": True, "action": "archived", "id": task_id}
    except sqlite3.Error as e:
        return {"ok": False, "error": str(e)}
    finally:
        conn.close()


def reset_task(task_id):
    """Reset task and its children to 'todo' status."""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE tasks SET status='todo', started_at=NULL, completed_at=NULL,
                             assignee=NULL, current_run_id=NULL
            WHERE id IN (SELECT child_id FROM task_links WHERE parent_id=?)
        """, (task_id,))
        cur.execute("""
            UPDATE tasks SET status='todo', started_at=NULL, completed_at=NULL,
                             assignee=NULL, current_run_id=NULL
            WHERE id=?
        """, (task_id,))
        conn.commit()
        _notify_clients()
        return {"ok": True, "action": "reset", "id": task_id}
    except sqlite3.Error as e:
        return {"ok": False, "error": str(e)}
    finally:
        conn.close()


def rerun_task(task_id):
    """Reset children to todo, keep parent body/agents. Use for fresh pipeline run."""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE tasks SET status='todo', started_at=NULL, completed_at=NULL,
                             assignee=NULL, current_run_id=NULL, result=NULL
            WHERE id IN (SELECT child_id FROM task_links WHERE parent_id=?)
        """, (task_id,))
        cur.execute("""
            UPDATE tasks SET status='ready', started_at=NULL, completed_at=NULL,
                             current_run_id=NULL
            WHERE id=?
        """, (task_id,))
        conn.commit()
        _notify_clients()
        return {"ok": True, "action": "rerun", "id": task_id}
    except sqlite3.Error as e:
        return {"ok": False, "error": str(e)}
    finally:
        conn.close()


def reassign_task(task_id, assignee):
    """Assign a task to a user."""
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE tasks SET assignee=? WHERE id=?", (assignee, task_id))
        conn.commit()
        _notify_clients()
        return {"ok": True, "action": "reassigned", "id": task_id, "assignee": assignee}
    except sqlite3.Error as e:
        return {"ok": False, "error": str(e)}
    finally:
        conn.close()


class SSEHandler(http.server.BaseHTTPRequestHandler):
    def _send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, path):
        if path == "/" or path == "":
            path = "/index.html"
        # Try static/ dir first, then project root for assets like screenshot.png
        project_dir = os.path.dirname(__file__)
        static_dir = os.path.join(project_dir, "static")
        for base_dir in (static_dir, project_dir):
            filepath = os.path.normpath(os.path.join(base_dir, path.lstrip("/")))
            if filepath.startswith(base_dir) and os.path.isfile(filepath):
                break
        else:
            self._send_json(404, {"error": "Not found"})
            return
        ext = os.path.splitext(filepath)[1]
        mime = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
        }.get(ext, "application/octet-stream")
        with open(filepath, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/tasks":
            self._send_json(200, load_tree())
            return

        if path == "/api/events":
            self._handle_sse()
            return

        self._serve_static(path)

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/tasks/") and path.endswith("/delete"):
            task_id = path.split("/")[3]
            result = delete_task(task_id)
            self._send_json(200, result)
            return

        self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        data = json.loads(body) if body else {}

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/tasks/"):
            parts = path.split("/")
            task_id = parts[3] if len(parts) >= 4 else None

            if task_id and path.endswith("/archive"):
                result = archive_task(task_id)
                self._send_json(200, result)
                return

            if task_id and path.endswith("/reset"):
                result = reset_task(task_id)
                self._send_json(200, result)
                return

            if task_id and path.endswith("/rerun"):
                result = rerun_task(task_id)
                self._send_json(200, result)
                return

            if task_id and path.endswith("/reassign"):
                assignee = data.get("assignee", "")
                result = reassign_task(task_id, assignee)
                self._send_json(200, result)
                return

        self._send_json(404, {"error": "Not found"})

    def _handle_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        # TCP_NODELAY: don't buffer tiny SSE packets
        try:
            self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

        wfile = self.wfile
        lock = threading.Lock()
        # Per-thread notification event — each SSE client gets its own
        self._sse_wake = threading.Event()

        # Register in the broadcast set: store (wfile, lock, wake_event)
        with _sse_lock:
            _sse_clients.add((wfile, lock, self._sse_wake))

        # Send initial tree, then wait for events
        last_tree = None
        try:
            tree = load_tree()
            tree_json = json.dumps(tree, ensure_ascii=False, sort_keys=True)
            last_tree = tree_json
            msg = f"event: full_update\ndata: {tree_json}\n\n"
            with lock:
                wfile.write(msg.encode())
                wfile.flush()

            while True:
                # Wait for either our per-thread event or timeout (keepalive)
                self._sse_wake.wait(timeout=5)
                self._sse_wake.clear()
                tree = load_tree()
                tree_json = json.dumps(tree, ensure_ascii=False, sort_keys=True)
                if tree_json != last_tree:
                    last_tree = tree_json
                    msg = f"event: full_update\ndata: {tree_json}\n\n"
                    with lock:
                        wfile.write(msg.encode())
                        wfile.flush()
                else:
                    # Keepalive ping
                    with lock:
                        wfile.write(b"event: ping\ndata: {}\n\n")
                        wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _sse_lock:
                _sse_clients.discard((wfile, lock, self._sse_wake))

    def log_message(self, format, *args):
        pass


def main():
    server = http.server.ThreadingHTTPServer((HOST, PORT), SSEHandler)
    print(f"Pipeline Dashboard: http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
