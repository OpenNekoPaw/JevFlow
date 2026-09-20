import copy
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from jevflow import Flow, FlowError, MockClient, validate_flow
from jevflow.visualize import render


def answer(value=0.7):
    return {'risk': {'type': 'noul', 'noul': value}}


def definition(count=4, concurrency=2):
    return {'version': 1, 'name': 'parallel', 'start': 'analyze',
            'limits': {'max_concurrency': concurrency}, 'nodes': {
        'analyze': {'type': 'parallel', 'branches': {
            'b'+str(i): {'state': {'$ref': 'input.b'+str(i)},
                         'questions': {'risk': {'type': 'noul', 'instructions': 'Assess risk.'}}}
            for i in range(count)}, 'next': 'done'},
        'done': {'type': 'return', 'value': {
            'b'+str(i): {'$ref': 'nodes.analyze.b'+str(i)+'.risk.noul'} for i in range(count)}}}}


def inputs(count=4):
    return {'b'+str(i): {'value': (i+1)/10} for i in range(count)}


class ParallelTests(unittest.TestCase):
    def test_overlap_concurrency_limit_and_scoped_state(self):
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        counts = {'active': 0, 'peak': 0}

        class Client:
            mode = 'test'
            def evaluate(self, state, questions, timeout, node_id):
                with lock:
                    counts['active'] += 1
                    counts['peak'] = max(counts['peak'], counts['active'])
                barrier.wait(timeout=2)  # Serial execution cannot pass this.
                result = answer(state['value'])
                state['value'] = -1
                with lock:
                    counts['active'] -= 1
                return {'answers': result}

        data = inputs()
        trace = {}
        result = Flow(definition()).run(data, Client(), trace)
        self.assertEqual(result, {k: v['value'] for k,v in data.items()})
        self.assertEqual(counts['peak'], 2)
        self.assertEqual(trace['calls'], 4)
        branches = trace['steps'][0]['branches']
        self.assertEqual(list(branches), ['b0','b1','b2','b3'])
        self.assertTrue(all(b['status']=='completed' for b in branches.values()))
        self.assertEqual(branches['b0']['request']['state'], {'value': 0.1})
        self.assertEqual(data, inputs())

    def test_all_inputs_and_budget_checked_before_calls(self):
        for kind in ['input', 'calls', 'steps']:
            flow = definition()
            data = inputs()
            if kind == 'input': data.pop('b3')
            if kind == 'calls': flow['limits']['max_calls'] = 3
            if kind == 'steps': flow['limits']['max_steps'] = 4
            trace = {}
            with self.subTest(kind=kind), self.assertRaises(FlowError):
                Flow(flow).run(data, MockClient({}), trace)
            self.assertEqual(trace['calls'], 0)
            self.assertEqual(trace['status'], 'failed')

    def test_failure_never_joins_or_launches_waiting_work(self):
        seen = []
        class Client:
            mode = 'test'
            def evaluate(self, node_id, **kwargs):
                seen.append(node_id)
                raise FlowError('provider failed')
        trace = {}
        with self.assertRaisesRegex(FlowError, 'provider failed'):
            Flow(definition(concurrency=1)).run(inputs(), Client(), trace)
        self.assertEqual(seen, ['analyze.b0'])
        self.assertNotIn('result', trace)
        self.assertEqual(len(trace['steps']), 1)
        self.assertEqual(trace['steps'][0]['branches']['b1']['status'], 'cancelled')

    def test_timeout_returns_without_late_trace_mutation(self):
        release = threading.Event()
        started = threading.Barrier(3)
        class Client:
            mode = 'test'
            def evaluate(self, timeout, **kwargs):
                self.last_timeout = timeout
                started.wait(timeout=3)
                release.wait(timeout=4)  # Deliberately violates the transport deadline.
                return {'answers': answer()}
        flow = definition(2)
        flow['limits']['timeout_seconds'] = 1
        trace = {}
        client = Client()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(Flow(flow).run, inputs(2), client, trace)
            try:
                started.wait(timeout=3)
                with self.assertRaisesRegex(FlowError, 'timeout'):
                    future.result(timeout=2)
                before = copy.deepcopy(trace)
                self.assertNotIn('result', trace)
                self.assertLessEqual(client.last_timeout, 1)
            finally:
                release.set()
        time.sleep(0.03)
        self.assertEqual(trace, before)
        self.assertTrue(all(b['status']=='abandoned' for b in trace['steps'][0]['branches'].values()))

    def test_singletons_and_downstream_dependency(self):
        flow = definition(2)
        flow['limits']['max_calls'] = 1
        for node in flow['nodes']['analyze']['branches'].values():
            node['questions'] = {'risk': {'type':'choice','instructions':'Pick.', 'criteria':{'safe':'Safe'}}}
        flow['nodes']['analyze']['next'] = 'combine'
        flow['nodes']['combine'] = {'type':'evaluate', 'state':{'left':{'$ref':'nodes.analyze.b0.risk.choice'},
            'right':{'$ref':'nodes.analyze.b1'}}, 'questions':{'risk':{'type':'noul','instructions':'Combine.'}}, 'next':'done'}
        flow['nodes']['done']['value'] = {'$ref':'nodes.combine.risk.noul'}
        trace = {}
        result = Flow(flow).run(inputs(2), MockClient({'combine':answer()}), trace)
        self.assertEqual(result, 0.7)
        self.assertEqual(trace['calls'], 1)
        self.assertEqual(trace['steps'][1]['request']['state']['left'], 'safe')
        self.assertEqual(trace['steps'][1]['request']['state']['right']['risk']['choice'], 'safe')

    def test_invalid_parallel_references_and_definitions(self):
        bad = []
        f=definition();f['nodes']['analyze']['branches']['b0']['state']={'$ref':'nodes.analyze.b1.risk.noul'};bad.append(f)
        f=definition();f['nodes']['done']['value']={'$ref':'nodes.analyze.missing.risk.noul'};bad.append(f)
        f=definition();f['nodes']['done']['value']={'$ref':'nodes.analyze.b0.missing.noul'};bad.append(f)
        f=definition();f['nodes']['done']['value']={'$ref':'nodes.analyze.b0.risk.choice'};bad.append(f)
        f=definition();f['limits']['max_concurrency']=0;bad.append(f)
        f=definition(1);bad.append(f)
        f=definition();f['nodes']['analyze']['branches']['b0']['next']='done';bad.append(f)
        for f in bad:
            with self.subTest(flow=f), self.assertRaises(FlowError): validate_flow(f)

    def test_update_during_parallel_run_keeps_snapshot(self):
        ready = threading.Barrier(3)
        release = threading.Event()
        class Client:
            mode='test'
            def evaluate(self, **kwargs):
                ready.wait(timeout=2);release.wait(timeout=2)
                return {'answers':answer()}
        flow=Flow(definition(2));trace={}
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(flow.run,inputs(2),Client(),trace)
            try:
                ready.wait(timeout=2)
                changed=flow.definition;changed['nodes']['done']['value']='new';flow.update(changed)
            finally:release.set()
            self.assertEqual(future.result(timeout=2),{'b0':0.7,'b1':0.7})
        self.assertNotEqual(trace['flow']['nodes']['done']['value'],'new')
        self.assertEqual(flow.run(inputs(2),MockClient({'analyze.b0':answer(),'analyze.b1':answer()})),'new')

    def test_parallel_visualization_expands_fork_join_and_branch_trace(self):
        flow=definition(2);trace={}
        Flow(flow).run(inputs(2),MockClient({'analyze.b0':answer(),'analyze.b1':answer()}),trace)
        diagram=render(flow,'mermaid')
        self.assertIn('analyze.b0',diagram)
        self.assertIn('analyze.b1',diagram)
        html=render(flow,trace=trace)
        self.assertIn('analyze.b0',html)
        self.assertIn('0.7',html)
        self.assertIn('"status": "completed"',html)
