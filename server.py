import http.server
import json
import os
import socket
import sqlite3
import threading
import time
import re
import urllib.parse

KANBAN_DB = os.path.expanduser("~/.hermes/kanban/boards/pipeline/kanban.db")
PORT = int(os.environ.get("PORT", 8800))
HOST = os.environ.get("HOST", "0.0.0.0")

# Global broadcast: set of (wfile, lock) tuples for active SSE clients
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
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT DISTINCT t.id, t.title, t.status, t.assignee, t.body,
               t.created_at, t.started_at, t.completed_at, t.priority
        FROM tasks t
        INNER JOIN task_links l ON t.id = l.parent_id
        WHERE t.status != 'archived'
        ORDER BY
            CASE t.status
                WHEN 'running' THEN 0
                WHEN 'ready' THEN 1
                WHEN 'blocked' THEN 2
                WHEN 'todo' THEN 3
                WHEN 'triage' THEN 4
                WHEN 'done' THEN 5
                ELSE 6
            END,
            t.created_at DESC
    """)
    parents = [dict(r) for r in cursor.fetchall()]

    for p in parents:
        cursor.execute("""
            SELECT t.id, t.title, t.status, t.assignee, t.priority,
                   t.body, t.started_at, t.completed_at,
                   t.last_heartbeat_at, t.current_run_id
            FROM tasks t
            INNER JOIN task_links l ON t.id = l.child_id
            WHERE l.parent_id = ?
            ORDER BY t.priority DESC, t.created_at ASC
        """, (p["id"],))
        children = [dict(r) for r in cursor.fetchall()]

        # Fetch latest run info for children
        child_ids = [c["id"] for c in children]
        if child_ids:
            placeholders = ",".join("?" * len(child_ids))
            cursor.execute(f"""
                SELECT r.task_id, r.status, r.started_at, r.ended_at,
                       r.worker_pid, r.max_runtime_seconds, r.summary,
                       t.last_heartbeat_at
                FROM task_runs r
                JOIN tasks t ON t.id = r.task_id
                WHERE r.task_id IN ({placeholders})
                  AND r.id IN (
                      SELECT MAX(id) FROM task_runs
                      WHERE task_id IN ({placeholders})
                      GROUP BY task_id
                  )
            """, child_ids + child_ids)
            runs = {r["task_id"]: dict(r) for r in cursor.fetchall()}
            for c in children:
                c["run"] = runs.get(c["id"])
                # Copy last_heartbeat_at from tasks if not in run
                if c.get("last_heartbeat_at") and (not c["run"] or not c["run"].get("last_heartbeat_at")):
                    if c["run"]:
                        c["run"]["last_heartbeat_at"] = c["last_heartbeat_at"]
        else:
            for c in children:
                c["run"] = None

        # Parse agent flow from body if present
        if p.get("body"):
            m = re.search(r'Агенты:\s*([^\n]+)', p["body"])
            if m:
                p["agent_flow"] = [a.strip() for a in m.group(1).split("→")]

        p["children"] = children
        total = len(children)
        done = sum(1 for c in children if c["status"] == "done")
        running = sum(1 for c in children if c["status"] == "running")
        p["progress"] = {"total": total, "done": done, "running": running, "pct": round(done / total * 100) if total else 0}

    conn.close()
    return parents


# ── Board Management Actions ──────────────────────────────────────────

def _notify_clients(tree=None):
    """Broadcast a full_update SSE event to all connected clients."""
    global _sse_clients
    if tree is None:
        tree = load_tree()
    msg = f"event: full_update\ndata: {json.dumps(tree, ensure_ascii=False, sort_keys=True)}\n\n"
    data = msg.encode()
    with _sse_lock:
        dead = set()
        for wfile, lock in _sse_clients:
            with lock:
                try:
                    wfile.write(data)
                    wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    dead.add((wfile, lock))
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
        static_dir = os.path.join(os.path.dirname(__file__), "static")
        filepath = os.path.normpath(os.path.join(static_dir, path.lstrip("/")))
        if not filepath.startswith(static_dir):
            self._send_json(403, {"error": "Forbidden"})
            return
        if not os.path.isfile(filepath):
            self._send_json(404, {"error": "Not found"})
            return
        ext = os.path.splitext(filepath)[1]
        mime = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
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

        # Register in the broadcast set
        with _sse_lock:
            _sse_clients.add((wfile, lock))

        last_tree = None
        try:
            while True:
                tree = load_tree()
                tree_json = json.dumps(tree, ensure_ascii=False, sort_keys=True)
                if tree_json != last_tree:
                    last_tree = tree_json
                    msg = f"event: full_update\ndata: {tree_json}\n\n"
                    with lock:
                        wfile.write(msg.encode())
                        wfile.flush()
                else:
                    # Still send a keepalive ping every 3rd cycle
                    now = time.monotonic()
                    if not hasattr(self, '_last_ping') or now - self._last_ping > 4.5:
                        self._last_ping = now
                        with lock:
                            wfile.write(b"event: ping\ndata: {}\n\n")
                            wfile.flush()
                time.sleep(1.5)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _sse_lock:
                _sse_clients.discard((wfile, lock))

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
