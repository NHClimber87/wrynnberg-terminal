"""Serve Wrynnberg Terminal on 127.0.0.1 only.

  python3 -m terminal.serve [--port 8744] [--fetch]

GET  /                 the page
GET  /api/data         the dataset (JSON), rebuilt when a copied file changes
POST /api/refresh      copy changed files from G1 (read only), then rebuild
GET  /api/pins, POST /api/pins   pinned item ids, kept in DATA_DIR/pins.json
"""
import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from terminal import config, fetch, history, model  # noqa: E402

STATIC = os.path.join(config.ROOT, "static")
TYPES = {".LICENSE": "text/plain; charset=utf-8", ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}


def _read_json(path):
    with open(path) as fh:
        return json.load(fh)


FETCH_LOCK = threading.Lock()


def locked_fetch(fn, *a, **k):
    with FETCH_LOCK:
        return fn(*a, **k)


def watch_loop(data_dir, every, stop):
    """Copy new files from G1 soon after a /reload or logout. Read only on G1."""
    while not stop.wait(every):
        try:
            st = locked_fetch(fetch.watch_once, data_dir)
            if st and st.get("last_copied"):
                print("watch: copied", ", ".join(st["last_copied"]), flush=True)
        except Exception as e:  # keep watching: one bad step must not stop the thread
            print("watch error:", e, flush=True)


class State:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.lock = threading.Lock()
        self.key = None
        self.data = None
        self.series = {}
        self.watch = 0
        self.history = history.History(data_dir)

    def disk_key(self):
        key = []
        for acct in config.ACCOUNTS:
            for f in config.FILES:
                p = fetch.local_path(self.data_dir, acct, f)
                key.append(os.stat(p).st_mtime_ns if os.path.exists(p) else 0)
        for name in (fetch.STATUS, "pins.json"):
            sp = os.path.join(self.data_dir, name)
            key.append(os.stat(sp).st_mtime_ns if os.path.exists(sp) else 0)
        return tuple(key)

    def get(self):
        with self.lock:
            k = self.disk_key()
            if k != self.key or self.data is None:
                full = model.build_from_disk(self.data_dir)
                self.series = full.pop("series", {})
                self.data = json.dumps(full).encode()
                self.key = k
            return self.data

    def series_of(self, iid):
        self.get()
        s = self.series.get(iid)
        if s is None:
            return None
        h = self.history.overlay(iid, s["points"])
        return dict(s, history=h) if h else s

    def pins(self):
        try:
            return _read_json(os.path.join(self.data_dir, "pins.json"))
        except (OSError, ValueError):
            return {"add": [], "remove": []}


def make_handler(state):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/api/data":
                return self.send(200, state.get())
            if path == "/api/series":
                q = dict(p.split("=", 1) for p in (self.path.split("?", 1) + [""])[1].split("&") if "=" in p)
                try:
                    s = state.series_of(int(q.get("id", "")))
                except ValueError:
                    return self.send(400, b'{"error":"id"}')
                return self.send(200 if s else 404, json.dumps(s or {"error": "no data for this item"}).encode())
            if path == "/api/stamp":
                # Cheap check for new data: the page polls this and reloads when it changes.
                return self.send(200, json.dumps({"stamp": str(hash(state.disk_key())), "watch": state.watch}).encode())
            if path == "/api/pins":
                return self.send(200, json.dumps(state.pins()).encode())
            name = "index.html" if path == "/" else path.lstrip("/")
            full = os.path.realpath(os.path.join(STATIC, name))
            if not full.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(full):
                return self.send(404, b'{"error":"not found"}')
            with open(full, "rb") as fh:
                self.send(200, fh.read(), TYPES.get(os.path.splitext(full)[1], "application/octet-stream"))

        def do_POST(self):
            # Same-origin requests only; the page and the API share 127.0.0.1.
            origin = self.headers.get("Origin")
            if origin and not origin.startswith(("http://127.0.0.1", "http://localhost")):
                return self.send(403, b'{"error":"origin"}')
            if self.path == "/api/refresh":
                st = locked_fetch(fetch.fetch, state.data_dir)
                return self.send(200, json.dumps({k: st.get(k) for k in ("g1", "g1_error", "last_copied")}).encode())
            if self.path == "/api/pins":
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                    pins = {"add": sorted({int(i) for i in body.get("add", [])}),
                            "remove": sorted({int(i) for i in body.get("remove", [])})}
                except (ValueError, TypeError, AttributeError):
                    return self.send(400, b'{"error":"bad pins"}')
                fetch._write(os.path.join(state.data_dir, "pins.json"), pins)
                return self.send(200, json.dumps(pins).encode())
            return self.send(404, b'{"error":"not found"}')
    return H


def make_server(port, data_dir=config.DATA_DIR):
    state = State(data_dir)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    srv.state = state
    return srv


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8744)
    ap.add_argument("--fetch", action="store_true", help="copy from G1 before the first page load")
    ap.add_argument("--data", default=config.DATA_DIR)
    ap.add_argument("--watch", type=int, default=120, metavar="SECONDS",
                    help="look at the G1 file times this often and copy new files (0 = off)")
    a = ap.parse_args(argv)
    if a.fetch:
        st = fetch.fetch(a.data)
        print("G1:", st.get("g1"), st.get("g1_error") or "", "copied:", len(st.get("last_copied") or []))
    srv = make_server(a.port, a.data)
    if a.watch > 0:
        srv.state.watch = a.watch
        threading.Thread(target=watch_loop, args=(a.data, a.watch, threading.Event()), daemon=True).start()
        print(f"Watching G1 every {a.watch} s")
    print(f"Wrynnberg Terminal on http://127.0.0.1:{a.port}/")
    srv.serve_forever()


if __name__ == "__main__":
    main()
