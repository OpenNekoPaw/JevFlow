import copy
import json
import os
import re
import subprocess
import time
from pathlib import Path
from .core import FlowError, require


class GatewayClient:
    """Optional Gateway transport. Requires Node and the separately installed AI SDK."""
    mode = "live-gateway"

    def __init__(self, bridge=None, max_retries=0):
        self.bridge = Path(bridge).resolve() if bridge else Path(__file__).resolve().parents[1] / "adapters/ai-gateway/evaluate.mjs"
        self.max_retries = max_retries

    def evaluate(self, state, questions, timeout=None, node_id=None):
        require(bool(os.getenv("AI_GATEWAY_API_KEY")), "Missing AI_GATEWAY_API_KEY")
        gateway_questions = copy.deepcopy(questions)
        for question in gateway_questions.values():
            if question["type"] == "noul":
                question["type"] = "boolean"
        started = time.monotonic()
        attempts = []
        for attempt in range(self.max_retries + 1):
            remaining = None if timeout is None else timeout - (time.monotonic() - started)
            require(remaining is None or remaining > 0, "Gateway test call timed out")
            try:
                process = subprocess.run(["node", str(self.bridge), "-"],
                                         input=json.dumps({"state": state, "questions": gateway_questions}),
                                         text=True, capture_output=True, timeout=remaining, check=False)
            except subprocess.TimeoutExpired:
                raise FlowError("Gateway test call timed out") from None
            match = re.search(r"HTTP (\d{3})", process.stderr)
            status = int(match[1]) if match else None
            attempts.append({"attempt": attempt + 1, "ok": process.returncode == 0, "httpStatus": status})
            if process.returncode == 0:
                break
            transient = (status is not None and 500 <= status < 600) or (
                status is None and "Jev request failed" in process.stderr)
            if transient and attempt < self.max_retries:
                time.sleep(0.5)
                continue
            raise FlowError("Gateway test bridge failed" + (" (HTTP " + str(status) + ")" if status else "")
                            + "; attempts=" + str(len(attempts)))
        response = json.loads(process.stdout)
        response["transportAttempts"] = attempts
        for answer in response["answers"].values():
            if answer["type"] == "boolean":
                answer["type"] = "noul"
                answer["noul"] = answer.pop("probability")
        return response

