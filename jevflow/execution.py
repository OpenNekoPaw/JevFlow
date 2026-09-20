"""One execution boundary for the CLI and MCP; no scenario-specific behavior."""
import json
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from threading import BoundedSemaphore, Event

from .client import JevClient, MockClient
from .control import FlowCancelled, check_cancelled
from .schema import load_flow, number, require, snapshot_hash
from .flow import Flow
from .gateway import GatewayClient
from .replay import RunRecorder


@contextmanager
def permit(semaphore, deadline, cancel_event):
    while True:
        check_cancelled(cancel_event)
        remaining = deadline - time.monotonic()
        require(remaining > 0, "Flow timeout exceeded while waiting for capacity")
        if semaphore.acquire(timeout=min(0.05, remaining)):
            break
    try:
        check_cancelled(cancel_event)
        require(time.monotonic() < deadline, "Flow timeout exceeded")
        yield
    finally:
        semaphore.release()


class BoundedClient:
    def __init__(self, client, semaphore, cancel_event, recorder=None, run_id=None):
        self.client, self.semaphore, self.cancel_event = client, semaphore, cancel_event
        self.mode = client.mode
        self.recorder, self.run_id = recorder, run_id

    def evaluate(self, state, questions, timeout=None, node_id=None):
        deadline = time.monotonic() + timeout
        call_id = uuid.uuid4().hex
        queued = time.monotonic()
        started = None
        def update(**fields):
            if self.recorder is not None:
                self.recorder.request(self.run_id, call_id, nodeId=node_id, **fields)
        update(status="queued", queuedAtMs=round(time.time()*1000))
        try:
            with permit(self.semaphore, deadline, self.cancel_event):
                started = time.monotonic()
                update(status="running", startedAtMs=round(time.time()*1000),
                       queueMs=round((started-queued)*1000))
                response = self.client.evaluate(state, questions, timeout=deadline-time.monotonic(), node_id=node_id)
                check_cancelled(self.cancel_event)
                update(status="completed", elapsedMs=round((time.monotonic()-started)*1000),
                       transportAttempts=response.get("transportAttempts", []))
                return response
        except Exception as error:
            update(status="cancelled" if isinstance(error, FlowCancelled) else "failed",
                   totalElapsedMs=round((time.monotonic()-queued)*1000),
                   elapsedMs=round((time.monotonic()-started)*1000) if started is not None else None,
                   queueMs=round(((started if started is not None else time.monotonic())-queued)*1000), error=str(error),
                   transportAttempts=getattr(error, "transport_diagnostics", {}).get("transportAttempts", []))
            raise



def validate_path(flow_path):
    definition = load_flow(flow_path)
    return {"valid": True, "name": definition["name"], "revision": definition.get("revision"),
            "nodes": len(definition["nodes"])}


class ExecutionService:
    """Bound runs and actual provider calls independently; isolated state per run."""
    def __init__(self, trace_dir=".tmp/jevflow", max_runs=5, max_concurrency=5, client_factory=None, monitor=None):
        for value in (max_runs, max_concurrency):
            require(type(value) is int and value > 0, "Concurrency limits must be positive integers")
        self.trace_dir = Path(trace_dir).expanduser().resolve()
        self.runs = BoundedSemaphore(max_runs)
        self.calls = BoundedSemaphore(max_concurrency)
        self.client_factory = client_factory
        self.monitor = monitor

    def run(self, flow_path, inputs, *, provider="native", model="jev-latest", mock=None,
            deadline_unix_ms=None, attempt_timeout=5, max_retries=0, trace_path=None, cancel_event=None,
            received_at=None, session_id=None, step_id=None, actor_id=None, parent_run_id=None):
        started = time.monotonic() if received_at is None else received_at
        run_id = uuid.uuid4().hex
        event = cancel_event if cancel_event is not None else Event()
        trace, result, error = {}, None, None
        supplied_context = {"sessionId": session_id, "stepId": step_id, "actorId": actor_id, "parentRunId": parent_run_id}
        context = {k: v for k, v in supplied_context.items() if isinstance(v, str) and v.strip() and len(v) <= 256}
        received_ms = round(time.time()*1000-(time.monotonic()-started)*1000)
        recorder = RunRecorder(run_id, started, self.monitor)
        mode = "mock" if mock is not None else ("live-gateway" if provider == "gateway" else "live")
        destination = Path(trace_path).expanduser().resolve() if trace_path else self.trace_dir / (run_id + ".json")
        if self.monitor is not None:
            self.monitor.begin(run_id, flow_path, mode, received_ms, context)
        try:
            require(all(v is None or k in context for k, v in supplied_context.items()), "Run context IDs must be nonempty strings of at most 256 characters")
            require(provider in ("native", "gateway"), "provider must be native or gateway")
            require(provider != "gateway" or model == "jev-latest",
                    "model is a native-client option; Gateway uses typesafe-ai/jev")
            require(number(attempt_timeout) and attempt_timeout > 0, "attempt_timeout must be positive")
            require(type(max_retries) is int and max_retries >= 0, "max_retries must be nonnegative")
            require(deadline_unix_ms is None or number(deadline_unix_ms), "deadline_unix_ms must be finite")
            check_cancelled(event)
            # Load exactly once before queuing; no shared mutable flow or mock visits.
            flow = Flow.from_file(flow_path)
            deadline = started + flow.definition.get("limits", {}).get("timeout_seconds", 60)
            if deadline_unix_ms is not None:
                deadline = min(deadline, time.monotonic() + deadline_unix_ms / 1000 - time.time())
            if self.monitor is not None:
                definition = flow.definition
                digest = snapshot_hash(definition)
                self.monitor.configured(run_id, definition, digest, (time.time()+deadline-time.monotonic())*1000)
            with permit(self.runs, deadline, event):
                recorder.started_run()
                if self.monitor is not None:
                    self.monitor.started(run_id)
                if mock is not None:
                    client = MockClient(mock)
                elif self.client_factory is not None:
                    client = self.client_factory()
                elif provider == "gateway":
                    client = GatewayClient(max_retries=max_retries, attempt_timeout=attempt_timeout, cancel_event=event,
                        transport_observer=lambda node, attempts: recorder.transport(run_id, node, attempts))
                else:
                    require(max_retries == 0, "Native client does not support retries; use max_retries=0")
                    client = JevClient(model=model, timeout=attempt_timeout)
                client = BoundedClient(client, self.calls, event, recorder, run_id)
                mode = client.mode
                effective_deadline = (time.time() + deadline - time.monotonic()) * 1000
                result = flow.run(inputs, client, trace,
                                  deadline_unix_ms=effective_deadline, cancel_event=event,
                                  observer=recorder.observe)
                check_cancelled(event)
        except Exception as exc:
            result = None
            error = {"code": "cancelled" if isinstance(exc, FlowCancelled) else "execution_failed",
                     "message": str(exc), "retryable": bool(getattr(exc, "retryable", False))}
        status = "cancelled" if error and error["code"] == "cancelled" else "failed" if error else "completed"
        # Include load/queue failures too, without manufacturing a successful Flow trace.
        trace.update(runId=run_id, context=context, mode=mode, receivedAtMs=received_ms, status=status, totalElapsedMs=round((time.monotonic()-started)*1000))
        if error:
            trace.pop("result", None)
            trace["error"] = error["message"]
        trace["replay"] = recorder.finish(status, result, error, trace["totalElapsedMs"])
        saved = None
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(trace, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
            saved = str(destination)
        except (OSError, ValueError) as exc:
            status, result = "failed", None
            error = {"code": "trace_write_failed", "message": str(exc), "retryable": False}
            trace.update(status=status, error=error["message"])
            trace["replay"] = recorder.finish(status, result, error, trace["totalElapsedMs"])
        report = {"status": status, "runId": run_id, "context": context, "mode": mode, "result": result, "error": error,
                "calls": trace.get("calls", 0), "elapsedMs": trace["totalElapsedMs"],
                "flowElapsedMs": trace.get("elapsedMs"), "flowHash": trace.get("flowHash"),
                "revision": trace.get("flow", {}).get("revision"), "trace": saved}

        if self.monitor is not None:
            self.monitor.finish(run_id, report, trace)
        return report
