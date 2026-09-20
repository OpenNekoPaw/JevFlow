"""Run data shared by live monitoring, recording and offline history."""
import copy
from datetime import datetime

from .schema import require, validate_flow, validate_trace_snapshot


TERMINAL = frozenset({"completed", "failed", "cancelled"})


def update_request(requests, call_id, fields):
    """Merge one observation into an owned request map."""
    requests.setdefault(call_id, {"callId": call_id}).update(copy.deepcopy(fields))


def finish_requests(requests):
    """Freeze outstanding requests without claiming their transports stopped."""
    result = copy.deepcopy(requests)
    for request in result.values():
        if request["status"] not in TERMINAL:
            request["status"] = "cancelled" if request["status"] == "queued" else "abandoned"
    return result


def trace_run(trace, *, fallback_run_id=None, fallback_received_ms=0, trace_path=None, include_input=True):
    """Adapt a terminal trace to the shared run shape, independently of transport.

    Older traces may lack IDs or receipt times. Source-specific fallbacks apply
    only after recorded metadata; exporting may omit the top-level model input.
    """
    require(isinstance(trace, dict), "Replay traces must be objects")
    require(trace.get("status") in TERMINAL, "Archive replay requires terminal traces")
    run_id = trace.get("runId", fallback_run_id)
    require(isinstance(run_id, str) and bool(run_id), "Replay run IDs must be nonempty strings")
    flow = trace.get("flow")
    digest = None
    if flow is not None:
        flow = validate_flow(flow)
        digest = validate_trace_snapshot(trace, flow)
    received = trace.get("receivedAtMs")
    if received is None:
        try:
            received = round(datetime.fromisoformat(trace["startedAt"].replace("Z", "+00:00")).timestamp()*1000)
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            received = fallback_received_ms
    public_trace = copy.deepcopy({k: v for k, v in trace.items()
                                  if k != "replay" and (include_input or k != "input")})
    if flow is not None:
        public_trace.update(flow=copy.deepcopy(flow), flowHash=digest)
    report = {"runId": run_id, "status": trace["status"], "result": copy.deepcopy(trace.get("result")),
              "error": copy.deepcopy(trace.get("error")),
              "elapsedMs": trace.get("totalElapsedMs", trace.get("elapsedMs"))}
    if trace_path is not None:
        report["trace"] = str(trace_path)
    return {"runId": run_id, "context": copy.deepcopy(trace.get("context", {})), "receivedAtMs": received,
            "status": trace["status"], "finished": True, "historical": True, "mode": trace.get("mode"),
            "flowHash": digest, "revision": (flow or {}).get("revision"),
            "name": (flow or {}).get("title", (flow or {}).get("name", "执行记录")),
            "trace": public_trace, "replay": copy.deepcopy(trace.get("replay")), "requests": {}, "report": report}
