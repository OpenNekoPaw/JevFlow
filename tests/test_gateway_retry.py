"""Transport failures must not become decisions or unbounded retries."""
import json
import os
import subprocess
import unittest
from threading import Event
from unittest.mock import patch

from jevflow import FlowError
from jevflow import GatewayClient as GatewayTestClient


class GatewayRetryTests(unittest.TestCase):
    def setUp(self):
        self.client = GatewayTestClient("/unused/bridge.js", max_retries=2)
        self.questions = {"ok": {"type": "noul", "instructions": "Does the item match?"}}
        self.ok = subprocess.CompletedProcess([], 0, json.dumps({"answers": {
            "ok": {"type": "boolean", "probability": 0.7}}}), "")

    @patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "test-placeholder"})
    def test_live_attempt_events_do_not_change_retry_result(self):
        events = []
        client = GatewayTestClient('/unused/bridge.js', max_retries=1,
            transport_observer=lambda node, attempts: events.append((node, attempts)))
        bad = subprocess.CompletedProcess([], 1, '', 'Jev request failed (HTTP 503).')
        with patch('jevflow.gateway.subprocess.run', side_effect=[bad, self.ok]), patch('jevflow.gateway.time.sleep'):
            result = client.evaluate({}, self.questions, timeout=10, node_id='analyze.enemies')
        self.assertEqual(len(events), 4)
        self.assertTrue(all(node == 'analyze.enemies' for node, _ in events))
        self.assertEqual(events[0][1][0]['status'], 'running')
        self.assertEqual(events[1][1][0]['httpStatus'], 503)
        self.assertEqual(len(events[2][1]), 2)
        self.assertTrue(events[-1][1][-1]['ok'])
        events[-1][1].clear()
        self.assertEqual(len(result['transportAttempts']), 2)

    @patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "test-placeholder"})
    def test_transient_retry_is_bounded_and_recorded(self):
        unavailable = subprocess.CompletedProcess([], 1, "", "Jev request failed (HTTP 503).")
        with patch("jevflow.gateway.subprocess.run", side_effect=[unavailable, self.ok]) as run, patch("jevflow.gateway.time.sleep"):
            response = self.client.evaluate({}, self.questions, timeout=10)
        self.assertEqual(response["answers"]["ok"], {"type": "noul", "noul": 0.7})
        self.assertEqual(len(response["transportAttempts"]), 2)
        self.assertEqual(response["transportAttempts"][0]["httpStatus"], 503)
        self.assertLessEqual(run.call_args.kwargs["timeout"], 10)
        payloads = [json.loads(call.kwargs['input']) for call in run.call_args_list]
        self.assertEqual(payloads[0]['state'], payloads[1]['state'])
        self.assertEqual(payloads[0]['questions'], payloads[1]['questions'])
        self.assertLessEqual(payloads[1]['deadlineUnixMs'], payloads[0]['deadlineUnixMs'] + 2)

    @patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "test-placeholder"})
    def test_unclassified_upstream_failure_can_recover(self):
        bad = subprocess.CompletedProcess([], 1, "", "Jev request failed. Check Gateway access, credits, and connectivity.")
        with patch("jevflow.gateway.subprocess.run", side_effect=[bad, self.ok]) as run, patch("jevflow.gateway.time.sleep"):
            response = self.client.evaluate({}, self.questions, timeout=10)
        self.assertEqual(run.call_count, 2)
        self.assertFalse(response["transportAttempts"][0]["ok"])

    @patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "test-placeholder"})
    def test_bad_request_not_retried(self):
        bad = subprocess.CompletedProcess([], 1, "", "Jev request failed (HTTP 400).")
        with patch("jevflow.gateway.subprocess.run", return_value=bad) as run:
            with self.assertRaisesRegex(FlowError, "HTTP 400"):
                self.client.evaluate({}, self.questions, timeout=10)
        self.assertEqual(run.call_count, 1)

    @patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "test-placeholder"})
    def test_exhaustion_and_deadline_fail_without_an_answer(self):
        bad = subprocess.CompletedProcess([], 1, "", "Jev request failed (HTTP 503).")
        with patch("jevflow.gateway.subprocess.run", return_value=bad) as run, patch("jevflow.gateway.time.sleep"):
            with self.assertRaisesRegex(FlowError, "attempts=3"):
                self.client.evaluate({}, self.questions, timeout=10)
        self.assertEqual(run.call_count, 3)
        with patch("jevflow.gateway.subprocess.run") as run:
            with self.assertRaisesRegex(FlowError, "timed out"):
                self.client.evaluate({}, self.questions, timeout=0)
        run.assert_not_called()

    @patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "test-placeholder"})
    def test_five_timeout_retries_are_capped_at_six_attempts(self):
        client = GatewayTestClient('/unused/bridge.js', max_retries=5, attempt_timeout=5)
        timeout = subprocess.TimeoutExpired('node', 5)
        with patch('jevflow.gateway.subprocess.run', side_effect=[timeout] * 5 + [self.ok]) as run, patch('jevflow.gateway.time.sleep'):
            response = client.evaluate({}, self.questions, timeout=40)
        self.assertEqual(run.call_count, 6)
        self.assertEqual(len(response['transportAttempts']), 6)
        self.assertTrue(response['transportAttempts'][-1]['ok'])
        self.assertTrue(all(call.kwargs['timeout'] <= 5 for call in run.call_args_list))
        with patch('jevflow.gateway.subprocess.run', side_effect=timeout) as run, patch('jevflow.gateway.time.sleep'):
            with self.assertRaisesRegex(FlowError, 'attempts=6'):
                client.evaluate({}, self.questions, timeout=40)
        self.assertEqual(run.call_count, 6)

    @patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "test-placeholder"})
    def test_cancellation_never_retries_completed_attempt(self):
        from jevflow.control import FlowCancelled
        event = Event()
        client = GatewayTestClient('/unused/bridge.js', max_retries=5, cancel_event=event)
        def cancelled_attempt(*args, **kwargs):
            event.set()
            return subprocess.CompletedProcess([], 1, '', 'Jev request failed (HTTP 503).')
        with patch('jevflow.gateway.subprocess.run', side_effect=cancelled_attempt) as run:
            with self.assertRaises(FlowCancelled) as caught:
                client.evaluate({}, self.questions, timeout=10)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(len(caught.exception.transport_diagnostics['transportAttempts']), 1)


if __name__ == "__main__":
    unittest.main()
