"""Bounded observation journal. Playback never invokes a model or executor."""
import copy
import json
import time
from threading import RLock

from .records import finish_requests, update_request


def changes(before, after, path=()):
    if type(before) is type(after) and isinstance(after, dict):
        for name in before.keys() - after.keys():
            yield {"path": [*path, name], "remove": True}
        for name, value in after.items():
            if name not in before:
                yield {"path": [*path, name], "value": copy.deepcopy(value)}
            else:
                yield from changes(before[name], value, (*path, name))
    elif isinstance(before, list) and isinstance(after, list) and len(after) >= len(before):
        for i, value in enumerate(after):
            if i >= len(before):
                yield {"path": [*path, i], "value": copy.deepcopy(value)}
            else:
                yield from changes(before[i], value, (*path, i))
    elif before != after:
        yield {"path": list(path), "value": copy.deepcopy(after)}


class RunRecorder:
    def __init__(self, run_id, started, monitor=None, max_events=10000, max_bytes=8*1024*1024):
        self.run_id, self.started, self.monitor = run_id, started, monitor
        self.lock = RLock()
        self.max_events, self.max_bytes, self.bytes = max_events, max_bytes, 0
        self.closed = False
        self.state = {"status": "queued", "finished": False,
                      "trace": {"steps": [], "calls": 0, "status": "queued"}, "requests": {}}
        self.journal = {"version": 1, "mode": "events", "initial": copy.deepcopy(self.state),
                        "events": [], "truncated": False}

    def _commit(self, state, kind, node=None):
        patches = list(changes(self.state, state))
        self.state = copy.deepcopy(state)
        if not patches or self.journal["truncated"]:
            return
        events = self.journal["events"]
        event = {"seq": len(events)+1, "offsetMs": max(0, round((time.monotonic()-self.started)*1000)),
                 "kind": kind, "nodeId": node, "patches": patches}
        size = len(json.dumps(event, ensure_ascii=False).encode())
        if len(events) >= self.max_events or self.bytes+size > self.max_bytes:
            self.journal["truncated"] = True
            if self.monitor is not None:
                self.monitor.replay_event(self.run_id, None, True)
            return
        self.bytes += size
        events.append(event)
        if self.monitor is not None:
            self.monitor.replay_event(self.run_id, event, False)

    def started_run(self):
        with self.lock:
            self._commit({**self.state, "status": "running"}, "run_started")

    def observe(self, trace):
        with self.lock:
            if self.closed:
                return
            known = {k: v for k, v in trace.items() if k in ("steps", "calls", "status", "elapsedMs", "error", "result")}
            self._commit({**self.state, "trace": known}, "flow_progress", (trace.get("steps") or [{}])[-1].get("node"))
            if self.monitor is not None:
                self.monitor.observe(self.run_id, trace)

    def request(self, run_id, call_id, **fields):
        with self.lock:
            if self.closed:
                return
            requests = copy.deepcopy(self.state["requests"])
            update_request(requests, call_id, fields)
            record = requests[call_id]
            self._commit({**self.state, "requests": requests}, "request_progress", record.get("nodeId"))
            if self.monitor is not None:
                self.monitor.request(run_id, call_id, **fields)

    def transport(self, run_id, node_id, attempts):
        with self.lock:
            if self.closed:
                return
            for call in reversed(list(self.state["requests"].values())):
                if call.get("nodeId") == node_id and call["status"] == "running":
                    self.request(run_id, call["callId"], transportAttempts=attempts)
                    break

    def finish(self, status, result, error, elapsed_ms):
        with self.lock:
            requests = finish_requests(self.state["requests"])
            self._commit({**self.state, "status": status, "finished": True, "requests": requests,
                          "report": {"status": status, "result": result, "error": error, "elapsedMs": elapsed_ms}}, "run_finished")
            self.closed = True
            return copy.deepcopy(self.journal)
