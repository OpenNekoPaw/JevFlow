"""Optional local, read-only monitoring. No execution or MCP protocol in this module."""
import copy
import json
import secrets
import threading
import time
from collections import OrderedDict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .records import finish_requests, trace_run, update_request
from .preview import ConfigurationPreview, MAX_CONFIG_BYTES, preview_data


class RunMonitor:
    """Bounded run snapshots and a resumable, metadata-only event journal.

    Active runs are never evicted. Completed runs are bounded by max_history;
    the execution service separately bounds active runs and provider requests.
    """
    def __init__(self, max_history=100, max_events=2048):
        if max_history < 1 or max_events < 1:
            raise ValueError("Monitor limits must be positive")
        self.max_history = max_history
        self.runs = OrderedDict()
        self.events = deque(maxlen=max_events)
        self.sequence = 0
        self.condition = threading.Condition()

    def _event(self, run_id, kind):
        self.sequence += 1
        self.events.append({"id": self.sequence, "type": kind, "runId": run_id, "atMs": round(time.time()*1000)})
        self.condition.notify_all()

    def begin(self, run_id, path, mode, started_ms, context=None):
        with self.condition:
            self.runs[run_id] = {"runId": run_id, "flowPath": str(path), "mode": mode,
                "context": copy.deepcopy(context or {}), "status": "queued", "receivedAtMs": started_ms, "trace": {}, "requests": {}, "finished": False}
            self._event(run_id, "run_queued")

    def replay_event(self, run_id, event, truncated):
        with self.condition:
            run = self.runs.get(run_id)
            if run is None or run["finished"]:
                return
            journal = run.setdefault("replay", {"version": 1, "mode": "events", "events": [], "truncated": False})
            if event is not None:
                journal["events"].append(copy.deepcopy(event))
            journal["truncated"] = truncated

    def configured(self, run_id, definition, digest, deadline_ms):
        with self.condition:
            run = self.runs[run_id]
            run.update(flowHash=digest, revision=definition.get("revision"), name=definition.get("title", definition["name"]),
                       deadlineUnixMs=deadline_ms)
            run["trace"] = {"flow": copy.deepcopy(definition), "flowHash": digest, "steps": []}
            self._event(run_id, "flow_loaded")

    def started(self, run_id):
        with self.condition:
            run = self.runs[run_id]
            run.update(status="running", startedAtMs=round(time.time()*1000))
            run["queueMs"] = max(0, run["startedAtMs"]-run["receivedAtMs"])
            self._event(run_id, "run_started")

    def observe(self, run_id, trace):
        with self.condition:
            run = self.runs.get(run_id)
            if run is None or run["finished"]:
                return
            # _publish already owns a private copy. Consumers get their own copy.
            run["trace"] = trace
            run["mode"] = trace.get("mode", run["mode"])
            self._event(run_id, "flow_progress")

    def request(self, run_id, call_id, **update):
        with self.condition:
            run = self.runs.get(run_id)
            if run is None or run["finished"]:
                return  # Late transports cannot revive or modify terminal runs.
            update_request(run["requests"], call_id, update)
            self._event(run_id, "request_progress")

    def finish(self, run_id, report, trace):
        with self.condition:
            run = self.runs[run_id]
            run.update(status=report["status"], report=copy.deepcopy(report), trace={**run["trace"], **copy.deepcopy(trace)},
                       finished=True, finishedAtMs=round(time.time()*1000), replay=copy.deepcopy(trace.get("replay")))
            run["requests"] = finish_requests(run["requests"])
            self._event(run_id, "run_finished")
            completed = [key for key, value in self.runs.items() if value["finished"]]
            for key in completed[:-self.max_history]:
                del self.runs[key]

    def restore(self, directory):
        """Load only bounded recent JSON traces; never follow paths from HTTP input."""
        root = Path(directory).expanduser()
        paths = sorted(root.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:self.max_history]
        for path in reversed(paths):
            try:
                trace = json.loads(path.read_text())
                run = trace_run(trace, fallback_received_ms=round(path.stat().st_mtime*1000),
                                trace_path=path.resolve())
                with self.condition:
                    self.runs[run["runId"]] = run
            except (OSError, ValueError, TypeError):
                continue

    def snapshot(self):
        with self.condition:
            rows = []
            for run in reversed(list(self.runs.values())):
                row = {k: v for k, v in run.items() if k not in ("trace", "requests", "report", "replay")}
                row["calls"] = run["trace"].get("calls", 0)
                row["elapsedMs"] = run.get("report", {}).get("elapsedMs")
                rows.append(row)
            return {"cursor": self.sequence, "runs": copy.deepcopy(rows)}

    def get(self, run_id):
        with self.condition:
            return copy.deepcopy(self.runs.get(run_id))

    def since(self, cursor, timeout=10):
        with self.condition:
            if cursor == self.sequence:
                self.condition.wait_for(lambda: self.sequence != cursor, timeout)
            if cursor < 0 or cursor > self.sequence or (self.events and cursor < self.events[0]["id"]-1):
                return [{"id": self.sequence, "type": "reset"}]
            return [dict(e) for e in self.events if e["id"] > cursor]


class MonitorServer:
    """Loopback-only inspection with optional token authentication."""
    def __init__(self, monitor, port=0, preview_flow=None, require_token=False):
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("Web port must be between 0 and 65535")
        self.monitor = monitor
        self.preview = ConfigurationPreview(preview_flow)
        self.token = secrets.token_urlsafe(32) if require_token else None
        self.stopping = threading.Event()
        owner = self
        assets = Path(__file__).parent / "web"

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass  # Never log an SSE URL containing the monitor token.

            def write_headers(self, status, mime, length=None):
                self.send_response(status)
                self.send_header("Content-Type", mime)
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")
                if length is not None:
                    self.send_header("Content-Length", str(length))
                self.end_headers()

            def send(self, value, status=200, mime="application/json; charset=utf-8"):
                body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
                self.write_headers(status, mime, len(body))
                self.wfile.write(body)

            def do_GET(self):
                try:
                    self.handle_get()
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    pass

            def check_origin(self):
                self.connection.settimeout(15)
                port = owner.httpd.server_port
                host = self.headers.get("Host", "")
                allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
                if (host not in allowed or self.headers.get("Origin", "http://"+host) != "http://"+host
                        or self.headers.get("Sec-Fetch-Site") == "cross-site"):
                    self.send({"error": "Forbidden origin"}, 403); return False
                return True

            def authenticated(self, event_token=None):
                if owner.token is None:
                    return True
                auth = self.headers.get("Authorization", "")
                token = event_token or (auth[7:] if auth.startswith("Bearer ") else "")
                if not secrets.compare_digest(token.encode(), owner.token.encode()):
                    self.send({"error": "Monitor token required"}, 401); return False
                return True

            def do_POST(self):
                # Uploaded YAML is inspected in memory. Never accept a server path,
                # persist uploads, update an executing Flow or call a model.
                self.close_connection = True
                try:
                    if not self.check_origin() or not self.authenticated():
                        return
                    if urlsplit(self.path).path != "/api/preview":
                        self.send({"error": "Method not allowed"}, 405); return
                    if self.headers.get("Transfer-Encoding") or not self.headers.get("Content-Length"):
                        self.send({"error": "Content-Length required"}, 411); return
                    try:
                        size = int(self.headers["Content-Length"])
                    except ValueError:
                        self.send({"error": "Invalid Content-Length"}, 400); return
                    if size < 0 or size > MAX_CONFIG_BYTES:
                        self.send({"error": "YAML exceeds the 1 MiB preview limit"}, 413); return
                    if self.headers.get("Content-Type", "").split(";")[0].strip() not in ("text/yaml", "application/yaml", "text/plain"):
                        self.send({"error": "Send YAML text"}, 415); return
                    raw = self.rfile.read(size)
                    if len(raw) != size:
                        self.send({"error": "Incomplete YAML upload"}, 400); return
                    try:
                        data = preview_data(raw.decode("utf-8-sig"))
                    except (ValueError, TypeError, RecursionError) as exc:
                        self.send({"error": str(exc)}, 422); return
                    self.send(data)
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    pass

            def handle_get(self):
                if not self.check_origin():
                    return
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query)
                if parsed.path in ("/", "/monitor.js", "/monitor.css", "/viewer.js", "/viewer.css", "/dagre.min.js", "/replay.js", "/preview.js"):
                    name = "monitor.html" if parsed.path == "/" else parsed.path[1:]
                    mime = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}[Path(name).suffix]
                    self.send((assets/name).read_bytes(), mime=mime+"; charset=utf-8"); return
                if not self.authenticated(query.get("token", [None])[0] if parsed.path == "/api/events" else None):
                    return
                if parsed.path == "/api/config":
                    self.send(owner.preview.snapshot()); return
                if parsed.path == "/api/runs":
                    self.send(owner.monitor.snapshot()); return
                if parsed.path.startswith("/api/runs/"):
                    run = owner.monitor.get(parsed.path[len("/api/runs/"):])
                    if run is None:
                        self.send({"error": "Run not found or expired from monitor history"}, 404); return
                    if run.get("trace", {}).get("flow"):
                        from .topology import graph_data
                        flow = run["trace"]["flow"]
                        run["graph"] = graph_data(flow, "zh-CN")
                    if run.get("replay") and isinstance(run.get("trace"), dict):
                        run["trace"].pop("replay", None)  # Send the journal once.
                    self.send(run); return
                if parsed.path == "/api/events":
                    try:
                        cursor = int(self.headers.get("Last-Event-ID", query.get("after", ["0"])[0]))
                    except ValueError:
                        self.send({"error": "Invalid event cursor"}, 400); return
                    self.write_headers(200, "text/event-stream; charset=utf-8")
                    self.wfile.write(b": connected\n\n"); self.wfile.flush()
                    while not owner.stopping.is_set():
                        events = owner.monitor.since(cursor, timeout=1)
                        for event in events:
                            data = json.dumps(event, ensure_ascii=False)
                            self.wfile.write(f"id: {event['id']}\nevent: update\ndata: {data}\n\n".encode())
                            cursor = event["id"]
                        if not events:
                            self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                    return
                self.send({"error": "Not found"}, 404)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="jevflow-monitor")

    @property
    def url(self):
        base = f"http://127.0.0.1:{self.httpd.server_port}/"
        return base + ("#token=" + self.token if self.token is not None else "")

    def start(self):
        self.thread.start()
        return self

    def close(self):
        self.stopping.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
