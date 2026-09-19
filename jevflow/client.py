"""Small synchronous client for the native TypeSafe System One HTTP API."""

import copy
import json
import os
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .core import FlowError, json_value, require, validate_answers, validate_questions


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class JevClient:
    mode = "live"

    def __init__(self, api_key=None, model="jev-latest",
                 endpoint="https://api.typesafe.ai/v1/systemone", timeout=30):
        self.api_key = api_key if api_key is not None else os.getenv("TYPESAFE_API_KEY", "")
        self.model = model
        self.endpoint = endpoint
        self.timeout = timeout
        self._opener = build_opener(NoRedirect())

    def evaluate(self, state, questions, timeout=None, node_id=None):
        """Return native answers, model and usage. No automatic retries.

        node_id is used by interchangeable mocks, never sent to Jev.
        """
        require(bool(self.api_key.strip()), "Set TYPESAFE_API_KEY for live calls")
        validate_questions(questions)
        require(isinstance(state, (str, dict, list)), "Jev state must be text, object, or array")
        json_value(state)
        payload = {"model": self.model, "state": state, "questions": questions}
        request = Request(self.endpoint, data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode(),
                          headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"},
                          method="POST")
        started = time.monotonic()
        try:
            with self._opener.open(request, timeout=min(self.timeout, timeout or self.timeout)) as response:
                result = json.load(response)
        except HTTPError as exc:
            exc.close()
            raise FlowError("Jev HTTP error " + str(exc.code) + "; no retry was made") from None
        except (TimeoutError, socket.timeout):
            raise FlowError("Jev request timed out; no retry was made") from None
        except URLError:
            raise FlowError("Jev connection failed; check endpoint and connectivity") from None
        except (ValueError, UnicodeError):
            raise FlowError("Jev returned invalid JSON") from None
        require(isinstance(result, dict), "Jev returned an invalid response envelope")
        validate_answers(questions, result.get("answers"))
        result["elapsedMs"] = round((time.monotonic() - started) * 1000)
        return result


class MockClient:
    mode = "mock"

    def __init__(self, answers):
        require(isinstance(answers, dict), "Mocks must map node IDs to answer mappings")
        self.answers = copy.deepcopy(answers)
        self.visits = {}

    def evaluate(self, state, questions, timeout=None, node_id=None):
        require(node_id in self.answers, "Missing mock for node: " + str(node_id))
        value = self.answers[node_id]
        if isinstance(value, list):
            index = self.visits.get(node_id, 0)
            require(index < len(value), "Mock sequence exhausted: " + node_id)
            self.visits[node_id] = index + 1
            value = value[index]
        return {"model": "mock", "answers": copy.deepcopy(value)}
