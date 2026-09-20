import copy
import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path

from jevflow import Flow, FlowError, MockClient, summarize_traces
from jevflow.monitor import RunMonitor
from jevflow.schema import snapshot_hash
from jevflow.visualize import render, render_replay


def ordered_flow():
    return {'version': 1, 'name': 'ordered', 'start': 'filter', 'nodes': {
        'filter': {'type': 'filter', 'items': {
            'a': {'description': 'A', 'rank': 1}, 'b': {'description': 'B', 'rank': 1}},
            'where': [{'field': 'rank', 'op': 'gte', 'value': 0}],
            'order_by': [{'field': 'rank', 'direction': 'asc'}], 'limit': 1, 'next': 'end'},
        'end': {'type': 'return', 'value': {'$ref': 'nodes.filter.ids'}}}}


def reversed_flow():
    flow = ordered_flow()
    flow['nodes']['filter']['items'] = dict(reversed(list(flow['nodes']['filter']['items'].items())))
    return flow


def legacy_trace(flow):
    trace = {}
    Flow(flow).run({}, MockClient({}), trace)
    trace['runId'] = 'legacy'
    trace['flowHash'] = hashlib.sha256(json.dumps(flow, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return trace


class SnapshotTests(unittest.TestCase):
    def test_behaviorally_distinct_orders_have_distinct_hashes(self):
        traces = []
        for flow, expected in ((ordered_flow(), ['a']), (reversed_flow(), ['b'])):
            trace = {}
            self.assertEqual(Flow(flow).run({}, MockClient({}), trace), expected)
            self.assertTrue(trace['flowHash'].startswith('v2:'))
            self.assertEqual(trace['flowHash'], snapshot_hash(json.loads(json.dumps(flow))))
            traces.append(trace)
        self.assertNotEqual(traces[0]['flowHash'], traces[1]['flowHash'])
        with self.assertRaisesRegex(FlowError, 'different flow snapshot'):
            render(reversed_flow(), trace=traces[0])
        self.assertEqual(len(summarize_traces(traces)['groups']), 2)

    def test_legacy_history_is_readable_without_weakening_external_matching(self):
        flow = ordered_flow()
        trace = legacy_trace(flow)
        original = copy.deepcopy(trace)
        digest = snapshot_hash(flow)
        self.assertIn(digest, render(flow, trace=trace))
        with self.assertRaisesRegex(FlowError, 'different flow snapshot'):
            render(reversed_flow(), trace=trace)
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'legacy.json').write_text(json.dumps(trace))
            monitor = RunMonitor()
            monitor.restore(directory)
            online = monitor.get('legacy')
        page = render_replay([trace])
        offline = json.loads(re.search(r'id="archive">(.*?)</script>', page, re.S)[1])[0]
        for run in (online, offline):
            self.assertEqual(run['flowHash'], digest)
            self.assertEqual(run['trace']['flowHash'], digest)
        self.assertEqual(offline['graph']['flowHash'], digest)
        self.assertEqual(trace, original)

    def test_legacy_metrics_split_orders_even_when_old_hashes_match(self):
        first, second = legacy_trace(ordered_flow()), legacy_trace(reversed_flow())
        self.assertEqual(first['flowHash'], second['flowHash'])
        self.assertEqual(len(summarize_traces([first, second])['groups']), 2)

    def test_legacy_needs_snapshot_and_corrupt_hashes_are_rejected(self):
        trace = legacy_trace(ordered_flow())
        del trace['flow']
        with self.assertRaisesRegex(FlowError, 'different flow snapshot'):
            render(ordered_flow(), trace=trace)
        for digest in ('v2:broken', 'broken'):
            trace = legacy_trace(ordered_flow())
            trace['flowHash'] = digest
            with self.assertRaisesRegex(FlowError, 'different flow snapshot'):
                render_replay([trace])
