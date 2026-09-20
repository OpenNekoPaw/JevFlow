import copy
import json
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
from unittest.mock import patch

import yaml

from jevflow.client import MockClient
from jevflow.execution import ExecutionService

ROOT = Path(__file__).resolve().parents[1]
QUESTION = {"ok": {"type": "noul", "instructions": "证据是否充分？"}}


def definition(parallel=False):
    start = {"type": "evaluate", "state": {"$ref": "input"}, "questions": QUESTION, "next": "end"}
    if parallel:
        start = {"type": "parallel", "branches": {
            str_id: {"state": {"$ref": "input"}, "questions": QUESTION}
            for str_id in ["a", "b", "c", "d"]}, "next": "end"}
    return {"version": 1, "name": "boundary", "revision": "1", "start": "start",
            "limits": {"max_calls": 8},
            "nodes": {"start": start, "end": {"type": "return", "value": {"$ref": "input"}}}}


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "flow.yaml"
        self.path.write_text(yaml.safe_dump(definition()))

    def test_cli_preserves_results_and_structured_failure(self):
        command = [sys.executable, str(ROOT/"scripts/jevflow.py"), "run", str(ROOT/"examples/triage.yaml"),
                   "--input", "-", "--mock", str(ROOT/"examples/mock.json"), "--trace", str(self.root/"trace.json"), "--session-id", "match-cli", "--step-id", "1"]
        output = subprocess.run(command, input='{"message":"refund"}', text=True, capture_output=True)
        self.assertEqual(output.returncode, 0, output.stderr)
        report = json.loads(output.stdout)
        self.assertEqual(report["result"], {"route": "billing"})
        self.assertEqual(report["calls"], 2)
        self.assertEqual(report["context"], {"sessionId":"match-cli", "stepId":"1"})
        command += ["--deadline-unix-ms", "1"]
        failed = subprocess.run(command, input='{}', text=True, capture_output=True)
        self.assertEqual(failed.returncode, 1)
        value = json.loads(failed.stdout)
        self.assertIsNone(value["result"])
        self.assertEqual(value["calls"], 0)
        self.assertTrue(value["error"]["message"])

    def test_queue_time_counts_and_cancelled_waiter_does_not_call(self):
        entered, release = Event(), Event()
        class Client:
            mode = "test"
            def evaluate(self, *args, **kwargs):
                entered.set()
                release.wait(2)
                return {"answers": {"ok": {"type": "noul", "noul": 1}}}
        service = ExecutionService(self.root, max_runs=1, client_factory=Client)
        with ThreadPoolExecutor(2) as pool:
            active = pool.submit(service.run, self.path, {"first": True})
            self.assertTrue(entered.wait(1))
            try:
                queued = service.run(self.path, {}, deadline_unix_ms=time.time()*1000+40)
                self.assertEqual(queued["status"], "failed")
                self.assertEqual(queued["calls"], 0)
                event = Event(); event.set()
                cancelled = service.run(self.path, {}, cancel_event=event)
                self.assertEqual(cancelled["status"], "cancelled")
                self.assertEqual(cancelled["calls"], 0)
            finally:
                release.set()
            self.assertEqual(active.result()["status"], "completed")

    def test_global_call_limit_across_parallel_runs_and_input_isolation(self):
        self.path.write_text(yaml.safe_dump(definition(True)))
        lock = Lock()
        counts = {"active": 0, "peak": 0, "calls": 0}
        class Client:
            mode = "test"
            def evaluate(self, state, questions, **kwargs):
                with lock:
                    counts["active"] += 1; counts["calls"] += 1
                    counts["peak"] = max(counts["peak"], counts["active"])
                state["changed"] = True
                time.sleep(0.025)
                with lock: counts["active"] -= 1
                return {"answers": {"ok": {"type": "noul", "noul": 1}}}
        service = ExecutionService(self.root, max_concurrency=2, client_factory=Client)
        inputs = [{"seat": i} for i in range(2)]
        with ThreadPoolExecutor(2) as pool:
            outputs = list(pool.map(lambda value: service.run(self.path, value), inputs))
        self.assertEqual(counts, {"active": 0, "peak": 2, "calls": 8})
        self.assertEqual([r["result"] for r in outputs], inputs)
        self.assertNotEqual(outputs[0]["trace"], outputs[1]["trace"])

    def test_cancelled_run_stops_before_next_node(self):
        flow = definition()
        flow["nodes"]["start"]["next"] = "second"
        flow["nodes"]["second"] = copy.deepcopy(flow["nodes"]["start"])
        flow["nodes"]["second"]["next"] = "end"
        self.path.write_text(yaml.safe_dump(flow))
        entered, release, cancel = Event(), Event(), Event()
        calls = []
        class Client:
            mode = "test"
            def evaluate(self, *args, **kwargs):
                calls.append(kwargs["node_id"]); entered.set(); release.wait(2)
                return {"answers": {"ok": {"type": "noul", "noul": 1}}}
        service = ExecutionService(self.root, client_factory=Client)
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(service.run, self.path, {}, cancel_event=cancel)
            self.assertTrue(entered.wait(1)); cancel.set(); release.set()
            report = pending.result()
        self.assertEqual(report["status"], "cancelled")
        self.assertIsNone(report["result"])
        self.assertEqual(calls, ["start"])
        self.assertEqual(json.loads(Path(report["trace"]).read_text())["status"], "cancelled")

    def test_file_update_only_affects_next_run(self):
        entered, release = Event(), Event()
        class Client:
            mode = "test"
            def evaluate(self, *args, **kwargs):
                entered.set(); release.wait(2)
                return {"answers": {"ok": {"type": "noul", "noul": 1}}}
        service = ExecutionService(self.root, client_factory=Client)
        with ThreadPoolExecutor(1) as pool:
            active = pool.submit(service.run, self.path, {"old": True})
            self.assertTrue(entered.wait(1))
            updated = definition(); updated["revision"] = "2"
            updated["nodes"]["end"]["value"] = {"new": True}
            self.path.write_text(yaml.safe_dump(updated)); release.set()
            old = active.result()
        new = service.run(self.path, {})
        self.assertEqual(old["result"], {"old": True})
        self.assertEqual(new["result"], {"new": True})
        self.assertNotEqual(old["flowHash"], new["flowHash"])
        self.assertEqual((old["revision"], new["revision"]), ("1", "2"))

    def test_gateway_options_are_shared_without_network(self):
        client = MockClient({"start": {"ok": {"type": "noul", "noul": 1}}})
        with patch("jevflow.execution.GatewayClient", return_value=client) as factory:
            report = ExecutionService(self.root).run(self.path, {}, provider="gateway", max_retries=5, attempt_timeout=3)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(factory.call_args.kwargs["max_retries"], 5)
        self.assertEqual(factory.call_args.kwargs["attempt_timeout"], 3)

    def test_invalid_settings_and_missing_files_do_not_fallback(self):
        service = ExecutionService(self.root)
        for options in ({"provider": "invalid"}, {"attempt_timeout": -1}, {"deadline_unix_ms": float("nan")},
                        {"max_retries": 5}, {"mock": {}}):
            result = service.run(self.path, {}, **options)
            self.assertEqual(result["status"], "failed")
            self.assertIsNone(result["result"])
        self.assertEqual(service.run(self.root/"missing.yaml", {})["status"], "failed")

    def test_executor_queue_age_is_part_of_budget(self):
        flow = definition(); flow["limits"]["timeout_seconds"] = 1
        self.path.write_text(yaml.safe_dump(flow))
        with patch("jevflow.execution.JevClient") as client:
            result = ExecutionService(self.root).run(self.path, {}, received_at=time.monotonic()-2)
        self.assertEqual(result["status"], "failed")
        client.assert_not_called()

    def test_parallel_cancellation_retains_actual_inflight_slot(self):
        self.path.write_text(yaml.safe_dump(definition(True)))
        entered, release, cancel = Event(), Event(), Event()
        calls = []
        class Client:
            mode = "test"
            def evaluate(self, *args, **kwargs):
                calls.append(kwargs["node_id"]); entered.set(); release.wait(2)
                return {"answers": {"ok": {"type": "noul", "noul": 1}}}
        service = ExecutionService(self.root, max_concurrency=1, client_factory=Client)
        with ThreadPoolExecutor(1) as pool:
            active = pool.submit(service.run, self.path, {}, cancel_event=cancel)
            self.assertTrue(entered.wait(1)); cancel.set()
            try:
                report = active.result(timeout=1)
                self.assertEqual(report["status"], "cancelled")
                before = Path(report["trace"]).read_bytes()
                # The cancelled synchronous transport still owns the only slot.
                next_run = service.run(self.path, {}, deadline_unix_ms=time.time()*1000+50)
                self.assertEqual(next_run["status"], "failed")
                self.assertEqual(len(calls), 1)
            finally:
                release.set()
        time.sleep(0.08)
        self.assertEqual(Path(report["trace"]).read_bytes(), before)
