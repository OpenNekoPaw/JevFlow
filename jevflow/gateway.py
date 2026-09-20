"""Optional Gateway transport with a shared deadline and bounded attempts."""
import copy
import json
import math
import os
import re
import subprocess
import time
from pathlib import Path
from .control import FlowCancelled, FlowError, check_cancelled
from .schema import require


class GatewayClient:
    mode = "live-gateway"

    def __init__(self, bridge=None, max_retries=0, attempt_timeout=None, cancel_event=None, transport_observer=None):
        self.bridge = Path(bridge).resolve() if bridge else Path(__file__).resolve().with_name("adapters") / "evaluate.mjs"
        require(type(max_retries) is int and max_retries >= 0, "max_retries must be a nonnegative integer")
        require(attempt_timeout is None or (type(attempt_timeout) in (int, float)
                and math.isfinite(attempt_timeout) and attempt_timeout > 0), "attempt_timeout must be positive")
        self.max_retries = max_retries
        self.attempt_timeout = attempt_timeout
        self.cancel_event = cancel_event
        self.transport_observer = transport_observer

    def evaluate(self, state, questions, timeout=None, node_id=None):
        require(bool(os.getenv("AI_GATEWAY_API_KEY")), "Missing AI_GATEWAY_API_KEY")
        gateway_questions = copy.deepcopy(questions)
        for question in gateway_questions.values():
            if question["type"] == "noul":
                question["type"] = "boolean"
        started = time.monotonic()
        budget = 30 if timeout is None else timeout
        deadline = started + budget
        attempts = []

        def fail(message, retryable):
            error = FlowError(message)
            error.retryable = retryable
            error.transport_diagnostics = {"transportAttempts": copy.deepcopy(attempts),
                                           "elapsedMs": round((time.monotonic() - started) * 1000)}
            raise error

        def publish(records):
            if self.transport_observer is not None:
                try:
                    self.transport_observer(node_id, copy.deepcopy(records))
                except Exception:
                    import logging
                    logging.getLogger(__name__).warning("Transport observer failed", exc_info=True)

        for attempt in range(self.max_retries + 1):
            check_cancelled(self.cancel_event)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                fail("Gateway call timed out; attempts=" + str(len(attempts)), True)
            duration = min(remaining, self.attempt_timeout or remaining)
            attempt_started = time.monotonic()
            record = {"attempt": attempt + 1, "ok": False, "httpStatus": None,
                      "budgetMs": max(1, int(duration * 1000)), "startedAtMs": round(time.time() * 1000)}
            # Leave a small window for the bridge to serialize a sanitized error.
            sdk_deadline = round(time.time() * 1000) + max(1, int(duration * 1000) - 100)
            publish(attempts + [{**record, "status": "running"}])
            response = None
            try:
                process = subprocess.run(["node", str(self.bridge), "-"],
                    input=json.dumps({"state": state, "questions": gateway_questions, "deadlineUnixMs": sdk_deadline}),
                    text=True, capture_output=True, timeout=duration, check=False)
                try:
                    response = json.loads(process.stdout)
                except (ValueError, TypeError):
                    response = None
                if isinstance(response, dict) and isinstance(response.get("timing"), dict):
                    # Do not copy arbitrary stderr, request data or provider error objects.
                    record.update({k: v for k, v in response["timing"].items()
                                   if k in ("sdkMs", "bridgeMs") and type(v) in (int, float) and math.isfinite(v)})
                if process.returncode == 0 and isinstance(response, dict) and "answers" in response:
                    record.update(ok=True, kind="success")
                elif isinstance(response, dict) and isinstance(response.get("error"), dict):
                    failure = response["error"]
                    code = failure.get("code")
                    status = failure.get("httpStatus")
                    record.update(kind=code if code in ("timeout", "network", "http") else "invalid_response",
                                  httpStatus=status if type(status) is int else None)
                else:
                    match = re.search(r"HTTP (\d{3})", process.stderr)
                    status = int(match[1]) if match else None
                    record.update(httpStatus=status, kind="http" if status else
                                  "network" if "Jev request failed" in process.stderr else "invalid_response")
            except subprocess.TimeoutExpired:
                record["kind"] = "timeout"
            except OSError:
                record["kind"] = "process_error"
            record["elapsedMs"] = round((time.monotonic() - attempt_started) * 1000)
            if "bridgeMs" in record:
                record["processOverheadMs"] = max(0, record["elapsedMs"] - record["bridgeMs"])
            attempts.append(record)
            publish(attempts)
            if self.cancel_event is not None and self.cancel_event.is_set():
                error = FlowCancelled("Flow cancelled")
                error.transport_diagnostics = {"transportAttempts": copy.deepcopy(attempts)}
                raise error
            if record["ok"]:
                response["transportAttempts"] = attempts
                for answer in response["answers"].values():
                    if answer["type"] == "boolean":
                        answer["type"] = "noul"
                        answer["noul"] = answer.pop("probability")
                return response
            status = record["httpStatus"]
            transient = record["kind"] in ("timeout", "network") or (status is not None and (status == 429 or 500 <= status < 600))
            # Backoff and another useful attempt must fit inside the original deadline.
            if transient and attempt < self.max_retries and deadline - time.monotonic() > 0.75:
                if self.cancel_event is None:
                    time.sleep(0.25)
                else:
                    self.cancel_event.wait(0.25)
                continue
            message = "Gateway call timed out" if record["kind"] == "timeout" else "Gateway bridge failed"
            fail(message + (" (HTTP " + str(status) + ")" if status else "") + "; attempts=" + str(len(attempts)), transient)
