import contextlib
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path

from jevflow import FlowError, latency_summary, summarize_traces
from jevflow.__main__ import main
from jevflow.client import MockClient
from jevflow.execution import ExecutionService


def trace(status='completed', mode='mock'):
    return {'flow': {'name': 'test'}, 'status': status, 'mode': mode, 'calls': 1,
            'elapsedMs': 120, 'input': {'private': 'secret-value'}, 'steps': [
                {'type': 'evaluate', 'node': 'judge', 'status': status, 'elapsedMs': 100}]}


class MetricsTests(unittest.TestCase):
    def test_service_cancellation_and_pre_execution_failures_can_be_aggregated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'flow.yaml'
            path.write_text('''version: 1
name: cancellation
start: judge
nodes:
  judge:
    type: evaluate
    state: evidence
    questions:
      ok: {type: noul, instructions: "Ready?"}
    next: done
  done: {type: return, value: true}
''')
            event = threading.Event()

            class CancellingClient(MockClient):
                def evaluate(self, *args, **kwargs):
                    event.set()
                    return super().evaluate(*args, **kwargs)

            service = ExecutionService(directory, client_factory=lambda: CancellingClient({
                'judge': {'ok': {'type': 'noul', 'noul': 0.8}}}))
            reports = [service.run(path, {}, cancel_event=event),
                       service.run(path, {}, mock={}, cancel_event=event),
                       service.run(Path(directory) / 'missing.yaml', {}, mock={}),
                       service.run(path, {}, mock={}, deadline_unix_ms=1)]
            traces = [json.loads(Path(report['trace']).read_text()) for report in reports]
            result = summarize_traces(traces)
            self.assertEqual(result['counts']['runs'], 4)
            self.assertEqual(result['counts']['cancelled'], 2)
            self.assertEqual(result['counts']['failed'], 2)
            self.assertEqual(result['counts']['logicalCalls'], 1)
            self.assertEqual(result['latency']['flow']['n'], 1)
            self.assertEqual(result['latency']['total']['n'], 4)
            unloaded = next(group for group in result['groups'] if group['flowHash'] is None)
            self.assertEqual(unloaded['counts']['runs'], 3)
            self.assertEqual(unloaded['mode'], 'mock')
            self.assertEqual(unloaded['latency']['flow'], {'n': 0})
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['stats', *[r['trace'] for r in reports]]), 0)

    def test_incomplete_success_and_non_trace_json_are_rejected(self):
        for value in ({'status': 'completed', 'runId': 'x'}, {'status': 'failed'},
                      {'status': 'cancelled', 'runId': 'x', 'steps': 'invalid'}):
            with self.subTest(value=value), self.assertRaises(FlowError):
                summarize_traces([value])

    def test_failed_attempts_and_modes_are_retained_without_private_data(self):
        failed = trace('failed', 'live-gateway')
        failed['steps'][0]['transport'] = {'transportAttempts': [
            {'attempt': 1, 'ok': False, 'kind': 'timeout', 'elapsedMs': 50},
            {'attempt': 2, 'ok': False, 'kind': 'http', 'elapsedMs': 40}]}
        report = summarize_traces([trace(), failed])
        self.assertEqual(report['counts']['failed'], 1)
        self.assertEqual(report['counts']['logicalCalls'], 2)
        self.assertEqual(report['counts']['transportRetries'], 1)
        self.assertEqual(report['counts']['transportTimeouts'], 1)
        self.assertEqual(report['latency']['flow']['n'], 2)
        self.assertEqual(report['latency']['sdk'], {'n': 0})
        self.assertEqual(len(report['groups']), 2)
        self.assertNotIn('secret-value', json.dumps(report))

    def test_parallel_wall_time_is_not_sum_of_branches(self):
        value = trace()
        value['calls'] = 2
        value['steps'] = [{'type': 'parallel', 'node': 'analyze', 'branches': {
            key: {'node': 'analyze.' + key, 'type': 'evaluate', 'status': 'completed', 'elapsedMs': 100}
            for key in ('a', 'b')}}]
        report = summarize_traces([value])
        self.assertEqual(report['latency']['flow']['maxMs'], 120)
        self.assertEqual(report['latency']['evaluate']['n'], 2)
        self.assertNotIn('serialNonEvaluate', report['latency'])

    def test_missing_abandoned_time_is_not_zero_or_success(self):
        value = trace('failed')
        value['steps'] = [{'type': 'parallel', 'node': 'p', 'branches': {
            'a': {'node': 'p.a', 'status': 'abandoned'}}}]
        report = summarize_traces([value])
        self.assertEqual(report['counts']['evaluations.abandoned'], 1)
        self.assertEqual(report['nodes']['p.a'], {'n': 0})

    def test_quantiles_ignore_only_missing_values(self):
        self.assertEqual(latency_summary([None, 0, 10, 20])['p50Ms'], 10)
        self.assertEqual(latency_summary(range(1, 101))['p95Ms'], 95)
        with self.assertRaises(FlowError):
            latency_summary([float('nan')])

    def test_cli_reads_traces_and_writes_aggregate(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'trace.json'
            output = Path(directory) / 'stats.json'
            source.write_text(json.dumps(trace()))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['stats', str(source), str(source), '--output', str(output)]), 0)
            self.assertEqual(json.loads(output.read_text())['counts']['runs'], 1)
