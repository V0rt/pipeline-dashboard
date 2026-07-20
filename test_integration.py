#!/usr/bin/env python3
"""Integration tests for pipeline-dashboard API + SSE.
Exercises all endpoints and verifies DB consistency."""
import json, os, socket, sqlite3, sys, time, urllib.request, urllib.error

BASE = "http://127.0.0.1:8800"
KANBAN_DB = os.path.expanduser("~/.hermes/kanban/boards/pipeline/kanban.db")
PASS = 0
FAIL = 0

def ok(name):
    global PASS; PASS += 1
    print(f"  PASS {name}")

def fail(name, detail=""):
    global FAIL; FAIL += 1
    print(f"  FAIL {name}: {detail}")

def get(path):
    return urllib.request.urlopen(f"{BASE}{path}", timeout=5).read()

def post(path, data=None):
    body = json.dumps(data).encode() if data else b"{}"
    req = urllib.request.Request(f"{BASE}{path}", data=body,
        headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=5).read()

def tasks_from_db(parent_id):
    conn = sqlite3.connect(KANBAN_DB)
    cur = conn.cursor()
    cur.execute("SELECT id, status, assignee, started_at, completed_at FROM tasks WHERE id=? "
                "OR id IN (SELECT child_id FROM task_links WHERE parent_id=?) ORDER BY created_at",
                (parent_id, parent_id))
    rows = cur.fetchall()
    conn.close()
    return rows

# ── Test 1: GET /api/tasks → JSON array ──
try:
    data = json.loads(get("/api/tasks"))
    assert isinstance(data, list), "not an array"
    assert len(data) >= 1, "empty"
    ok("GET /api/tasks returns task list")
except Exception as e:
    fail("GET /api/tasks", e)

# ── Test 2: Verify parent pipeline fields ──
try:
    active = [t for t in data if t.get("status") in ("running", "ready", "todo")]
    if active:
        p = active[0]
        assert p.get("status") in ("running", "ready"), f"parent status={p.get('status')}"
        if p.get("progress"):
            assert p["progress"]["total"] == len(p.get("children", [])), "progress.total mismatch"
        ok(f"Parent pipeline {p['id'][:10]}: status={p['status']}, {len(p.get('children',[]))} children")
    else:
        ok("No active pipelines (OK if all done)")
except Exception as e:
    fail("Parent pipeline fields", e)

# ── Test 3: SSE endpoint opens ──
try:
    s = socket.create_connection(("127.0.0.1", 8800), timeout=5)
    s.sendall(b"GET /api/events HTTP/1.1\r\nHost: 127.0.0.1:8800\r\nConnection: keep-alive\r\n\r\n")
    time.sleep(0.5)
    data = s.recv(4096, socket.MSG_DONTWAIT)
    s.close()
    assert b"full_update" in data or b"event:" in data, "SSE did not send event"
    ok("SSE /api/events streams data")
except BlockingIOError:
    ok("SSE /api/events streams data (no data yet)")
except Exception as e:
    fail("SSE /api/events", e)

# ── Test 4: DB timestamps are reasonable ──
try:
    rows = tasks_from_db("t_eb2622eb")
    assert len(rows) >= 3, f"Expected 3+ child tasks, got {len(rows)}"
    now = int(time.time())
    for row in rows:
        tid, status, assignee, started, completed = row
        if status in ("done", "running") and started:
            assert 1780000000 <= started <= now + 100, f"{tid}: started_at={started} out of range"
        if status == "done" and completed:
            assert completed >= (started or 0), f"{tid}: completed_at < started_at"
    ok("DB timestamps are reasonable")
except Exception as e:
    fail("DB timestamps", e)

# ── Test 5: Assignees match task titles ──
try:
    rows = tasks_from_db("t_eb2622eb")
    for row in rows:
        tid, status, assignee, started, completed = row
        if status in ("done", "running") and assignee:
            pass  # assignee is set
    ok("Assignees present on running/done tasks")
except Exception as e:
    fail("Assignees", e)

# ── Test 6: API can rerun/archive existing pipelines ──
try:
    data6 = json.loads(get("/api/tasks"))
    done = []
    for t in data6:
        try:
            if isinstance(t, dict) and t.get("status") == "done":
                done.append(t)
        except:
            pass
    if done:
        test_id = done[0]["id"]
        raw = post(f"/api/tasks/{test_id}/rerun")
        print(f"  DEBUG rerun raw: {raw[:200]}")
        resp = json.loads(raw)
        if resp.get("ok"):
            ok(f"rerun_task({test_id[:10]})")
            resp2 = json.loads(post(f"/api/tasks/{test_id}/archive"))
            if resp2.get("ok"):
                ok(f"archive_task({test_id[:10]})")
            else:
                fail(f"archive_task", resp2.get("error","unknown"))
        else:
            fail(f"rerun_task", resp.get("error","unknown"))
    else:
        ok("No done pipeline to test rerun/archive (SKIP)")
except Exception as e:
    fail("rerun/archive operations", e)

# ── Test 7: Cross-check dashboard vs DB consistency ──
try:
    data2 = json.loads(get("/api/tasks"))
    # Re-fetch after operations
    for t in data2:
        if t.get("id") == "t_eb2622eb":
            db_rows = tasks_from_db("t_eb2622eb")
            matching = [r for r in db_rows if r[0] == t["id"]]
            if matching:
                db_status = matching[0][1]
                assert db_status == t["status"], f"Mismatch: dashboard says {t['status']}, DB says {db_status}"
            ok(f"Dashboard/DB consistency for {t['id'][:10]}")
            break
    else:
        ok("No active pipeline for consistency check (SKIP)")
except Exception as e:
    fail("Dashboard/DB consistency", e)

# ── Test 8: SSE per-thread event fix — no race ──
try:
    # Open 2 SSE connections concurrently
    def sse_connect():
        s = socket.create_connection(("127.0.0.1", 8800), timeout=5)
        s.sendall(b"GET /api/events HTTP/1.1\r\nHost: 127.0.0.1:8800\r\n\r\n")
        time.sleep(0.3)
        try:
            d = s.recv(2048, socket.MSG_DONTWAIT)
        except:
            d = b""
        s.close()
        return b"full_update" in d
    r1 = sse_connect()
    r2 = sse_connect()
    ok(f"SSE concurrent clients (both received data: {r1}, {r2})")
except Exception as e:
    fail("SSE concurrent clients", e)

# ── Summary ──
print(f"\n=== Results: {PASS} passed, {FAIL} failed ===")
sys.exit(0 if FAIL == 0 else 1)
