import copy
import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from jevflow import Flow, FlowError, MockClient, load_flow, run_flow, validate_flow
from jevflow.__main__ import main
from jevflow.core import compare, resolve


ROOT = Path(__file__).resolve().parents[1]


def simple_flow():
    return {"version": 1, "name": "simple", "start": "done", "nodes": {
        "done": {"type": "return", "value": {"revision": "old"}}}}


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.flow = load_flow(ROOT / "examples/triage.yaml")
        self.mock = json.loads((ROOT / "examples/mock.json").read_text())
        self.inputs = {"message": "I was charged twice."}

    def test_all_example_routes(self):
        for case in json.loads((ROOT / "examples/cases.json").read_text()):
            with self.subTest(case=case["name"]):
                trace = {}
                result = run_flow(self.flow, case["input"], MockClient(case["mock"]), trace)
                self.assertEqual(result, case["expected"])
                self.assertEqual(trace["status"], "completed")
                self.assertEqual(trace["mode"], "mock")
                self.assertIn("flowHash", trace)

    def test_single_uses_shared_runtime_and_update(self):
        single = yaml.safe_load((ROOT / "examples/single.yaml").read_text())
        original = copy.deepcopy(single)
        mock = json.loads((ROOT / "examples/single-mock.json").read_text())
        trace = {}
        self.assertEqual(run_flow(single, self.inputs, MockClient(mock), trace), {"route": "billing"})
        self.assertEqual(trace["calls"], 1)
        self.assertEqual([s["type"] for s in trace["steps"]], ["evaluate", "return"])
        self.assertEqual(single, original)
        flow = Flow(self.flow)
        flow.update(single)
        self.assertEqual(flow.run(self.inputs, MockClient(mock)), {"route": "billing"})
        del single["result"]
        self.assertEqual(Flow(single).run(self.inputs, MockClient(mock)), mock["evaluate"])
        single["mode"] = "typo"
        with self.assertRaisesRegex(FlowError, "mode"):
            flow.update(single)
        self.assertEqual(flow.run(self.inputs, MockClient(mock)), {"route": "billing"})

    def test_batch_and_dependent_state(self):
        trace = {}
        run_flow(self.flow, self.inputs, MockClient(self.mock), trace)
        calls = [step for step in trace["steps"] if step["type"] == "evaluate"]
        self.assertEqual(len(calls), 2)
        self.assertEqual(set(calls[0]["request"]["questions"]), {"intent", "urgent"})
        self.assertEqual(calls[1]["request"]["state"]["prior_intent"], "billing")
        self.assertEqual(self.inputs, {"message": "I was charged twice."})

    def test_missing_input_fails_before_call(self):
        trace = {}
        with self.assertRaisesRegex(FlowError, "Missing value"):
            run_flow(self.flow, {}, MockClient({}), trace)
        self.assertEqual(trace["status"], "failed")
        self.assertEqual(trace["calls"], 0)

    def test_missing_mock_does_not_fallback(self):
        with self.assertRaisesRegex(FlowError, "Missing mock"):
            run_flow(self.flow, self.inputs, MockClient({}))

    def test_malformed_model_answers_fail(self):
        for value in [float("nan"), -0.1, 1.1, True, "0.9"]:
            mock = copy.deepcopy(self.mock)
            mock["classify"]["urgent"]["noul"] = value
            with self.subTest(value=value), self.assertRaises(FlowError):
                run_flow(self.flow, self.inputs, MockClient(mock))
        mock = copy.deepcopy(self.mock)
        mock["classify"]["intent"]["choice"] = "technical"
        with self.assertRaisesRegex(FlowError, "disagrees"):
            run_flow(self.flow, self.inputs, MockClient(mock))

    def test_missing_response_question(self):
        del self.mock["classify"]["urgent"]
        with self.assertRaisesRegex(FlowError, "exactly"):
            run_flow(self.flow, self.inputs, MockClient(self.mock))

    def test_invalid_flow_edits(self):
        variants = []
        bad = copy.deepcopy(self.flow)
        bad["nodes"]["classify"]["next"] = "typo"
        variants.append(bad)
        bad = copy.deepcopy(self.flow)
        bad["nodes"]["unused"] = {"type": "return", "value": 0}
        variants.append(bad)
        bad = copy.deepcopy(self.flow)
        bad["nodes"]["classify"]["script"] = "print('unsupported')"
        variants.append(bad)
        bad = copy.deepcopy(self.flow)
        bad["nodes"]["technical"]["value"] = {"$ref": "nodes.assess_billing.specificity.score"}
        variants.append(bad)
        bad = copy.deepcopy(self.flow)
        bad["nodes"]["classify"]["state"] = {"$ref": "nodes.classify.intent.choice"}
        variants.append(bad)
        for bad in variants:
            with self.subTest(flow=bad), self.assertRaises(FlowError):
                validate_flow(bad)

    def test_yaml_duplicate_and_unsafe_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flow.yaml"
            for content in ["name: first\nname: second", "!!python/object/apply:os.system ['echo unsafe']",
                            "version: 1\nnodes: {true: anything}"]:
                path.write_text(content)
                with self.subTest(content=content), self.assertRaises(FlowError):
                    load_flow(path)

    def test_call_and_step_budgets(self):
        self.flow["limits"]["max_calls"] = 1
        trace = {}
        with self.assertRaisesRegex(FlowError, "call budget"):
            run_flow(self.flow, self.inputs, MockClient(self.mock), trace)
        self.assertEqual(trace["calls"], 1)
        looping = {"version": 1, "name": "loop", "start": "again", "limits": {"max_steps": 3}, "nodes": {
            "again": {"type": "branch", "cases": [{"left": True, "op": "eq", "right": True, "next": "again"}], "default": "done"},
            "done": {"type": "return", "value": 0}}}
        with self.assertRaisesRegex(FlowError, "step budget"):
            run_flow(looping, {}, MockClient({}))

    def test_loop_without_exit_rejected(self):
        flow = {"version": 1, "name": "loop", "start": "a", "nodes": {
            "a": {"type": "branch", "cases": [{"left": 1, "op": "eq", "right": 1, "next": "a"}], "default": "a"}}}
        with self.assertRaisesRegex(FlowError, "path to return"):
            validate_flow(flow)

    def test_repeated_evaluation_consumes_mock_sequence(self):
        flow = {"version": 1, "name": "loop", "start": "judge", "nodes": {
            "judge": {"type": "evaluate", "state": {"$ref": "input"},
                      "questions": {"ready": {"type": "noul", "instructions": "Is it ready?"}}, "next": "check"},
            "check": {"type": "branch", "cases": [{"left": {"$ref": "nodes.judge.ready.noul"}, "op": "gte", "right": 0.8, "next": "done"}], "default": "judge"},
            "done": {"type": "return", "value": {"$ref": "nodes.judge.ready.noul"}}}}
        client = MockClient({"judge": [{"ready": {"type": "noul", "noul": 0.2}}, {"ready": {"type": "noul", "noul": 0.9}}]})
        self.assertEqual(run_flow(flow, {}, client), 0.9)

    def test_refs_and_numeric_comparisons(self):
        self.assertEqual(resolve({"$ref": "input.items.0.name"}, {"input": {"items": [{"name": "x"}]}}), "x")
        self.assertFalse(compare(True, "eq", 1))
        with self.assertRaises(FlowError):
            compare("2", "gte", 1)

    def test_update_rejects_invalid_and_copies_inputs(self):
        flow = Flow(simple_flow())
        candidate = flow.definition
        candidate["nodes"]["done"]["value"] = {"revision": "new"}
        self.assertEqual(flow.run({}, MockClient({})), {"revision": "old"})
        flow.update(candidate)
        candidate["nodes"]["done"]["value"] = "mutated"
        self.assertEqual(flow.run({}, MockClient({})), {"revision": "new"})
        with self.assertRaises(FlowError):
            flow.update({})
        self.assertEqual(flow.run({}, MockClient({})), {"revision": "new"})
        trace = {}
        flow.run({}, MockClient({}), trace)
        trace["flow"]["nodes"].clear()
        self.assertEqual(flow.run({}, MockClient({})), {"revision": "new"})

    def test_reload_keeps_old_on_invalid_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flow.yaml"
            path.write_text(yaml.safe_dump(simple_flow()))
            flow = Flow.from_file(path)
            replacement = simple_flow()
            replacement["nodes"]["done"]["value"] = "updated"
            path.write_text(yaml.safe_dump(replacement))
            flow.reload()
            self.assertEqual(flow.run({}, MockClient({})), "updated")
            path.write_text("bad: [")
            with self.assertRaises(FlowError):
                flow.reload()
            self.assertEqual(flow.run({}, MockClient({})), "updated")

    def test_inflight_run_keeps_snapshot(self):
        entered, release = threading.Event(), threading.Event()
        base = MockClient(self.mock)

        class PausedClient:
            mode = "mock"

            def evaluate(self, **kwargs):
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("Test release timed out")
                return base.evaluate(**kwargs)

        flow = Flow(self.flow)
        outputs = []
        thread = threading.Thread(target=lambda: outputs.append(flow.run(self.inputs, PausedClient())))
        thread.start()
        try:
            self.assertTrue(entered.wait(3))
            candidate = flow.definition
            candidate["nodes"]["billing"]["value"] = {"route": "new_version"}
            flow.update(candidate)
        finally:
            release.set()
            thread.join(3)
        self.assertEqual(outputs, [{"route": "billing"}])
        self.assertEqual(flow.run(self.inputs, MockClient(self.mock)), {"route": "new_version"})

    def test_cli_failure_trace_and_offline_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp) / "trace.json"
            inputs = Path(tmp) / "input.json"
            inputs.write_text("{}")
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["run", str(ROOT / "examples/triage.yaml"), "--input", str(inputs),
                             "--mock", str(ROOT / "examples/mock.json"), "--trace", str(trace)])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.getvalue())["status"], "failed")
            self.assertEqual(json.loads(trace.read_text())["calls"], 0)
            with redirect_stdout(io.StringIO()):
                code = main(["test", str(ROOT / "examples/triage.yaml"), str(ROOT / "examples/cases.json"),
                             "--trace-dir", str(Path(tmp) / "cases")])
            self.assertEqual(code, 0)

    def test_concurrent_shared_flow_keeps_run_state_isolated(self):
        barrier = threading.Barrier(4)
        flow = Flow({"version": 1, "name": "concurrent", "start": "judge", "nodes": {
            "judge": {"type": "evaluate", "state": {"$ref": "input"}, "questions": {
                "ready": {"type": "noul", "instructions": "Is the item ready?"}}, "next": "done"},
            "done": {"type": "return", "value": {"id": {"$ref": "input.id"},
                "answer": {"$ref": "nodes.judge.ready.noul"}}}}})

        class ConcurrentClient:
            mode = "mock"

            def evaluate(self, state, **kwargs):
                barrier.wait(timeout=3)
                return {"answers": {"ready": {"type": "noul", "noul": state["probability"]}}}

        inputs = [{"id": i, "probability": (i + 1) / 10} for i in range(4)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda item: flow.run(item, ConcurrentClient()), inputs))
        self.assertEqual(results, [{"id": i, "answer": (i + 1) / 10} for i in range(4)])


if __name__ == "__main__":
    unittest.main()
