import threading
import time
import unittest
from jevflow import Flow, FlowError, summarize_traces
from jevflow.visualize import render

class Client:
    mode='mock'
    def __init__(self, fail=False):
        self.calls=0;self.active=0;self.peak=0;self.lock=threading.Lock();self.fail=fail
    def evaluate(self, *, state, questions, timeout, node_id):
        with self.lock:
            self.calls+=1;self.active+=1;self.peak=max(self.peak,self.active)
        try:
            time.sleep(.005)
            if self.fail:raise FlowError('temporary failure')
            labels=questions['selection']['criteria'];winner=max(labels,key=lambda k:int(k[1:]))
            return {'answers':{'selection':{'type':'choice','choice':winner,'probabilities':{k:int(k==winner) for k in labels}}}}
        finally:
            with self.lock:self.active-=1

def definition(size=32, **limits):
    return {'version':1,'name':'selection','start':'choose','limits':{'max_calls':40,'max_steps':50,'max_concurrency':2,**limits},'nodes':{
        'choose':{'type':'select','state':{'$ref':'input.state'},'criteria':{'$ref':'input.criteria'},'instructions':'Select the largest number.','batch_size':size,'next':'result'},
        'result':{'type':'return','value':{'candidate':{'$ref':'nodes.choose.choice'}}}}}

def inputs(count):return {'state':{'$ref':'literal data'},'criteria':{'c'+str(i):str(i) for i in range(count)}}

class SelectTests(unittest.TestCase):
    def test_large_set_reduces_and_preserves_concurrency_and_trace(self):
        client=Client();trace={};result=Flow(definition()).run(inputs(300),client,trace)
        self.assertEqual(result,{'candidate':'c299'});self.assertEqual(client.calls,11)
        self.assertLessEqual(client.peak,2);self.assertGreater(client.peak,1)
        self.assertEqual([r['inputCount'] for r in trace['steps'][0]['rounds']],[300,10])
        self.assertEqual(summarize_traces([trace])['counts']['evaluations.completed'],11)
        self.assertNotIn('probabilities',trace['steps'][0]);self.assertNotIn('serialNonEvaluate',summarize_traces([trace])['latency'])
        self.assertIn('select',render(definition(), format="mermaid", locale="en").lower())
        self.assertIn('choose',render(definition(), trace=trace))
    def test_singleton_never_calls_provider(self):
        client=Client();trace={};self.assertEqual(Flow(definition()).run(inputs(1),client,trace),{'candidate':'c0'})
        self.assertEqual(client.calls,0);self.assertEqual(trace['calls'],0)
    def test_empty_candidates_fail_without_calls(self):
        client=Client()
        with self.assertRaisesRegex(FlowError,'nonempty'):Flow(definition()).run(inputs(0),client)
        self.assertEqual(client.calls,0)
    def test_ordered_fallback_uses_first_nonempty_pool_and_reports_count(self):
        flow=definition();flow['nodes']['choose']['fallback_criteria']=[{'$ref':'input.safe'},{'$ref':'input.all'}]
        flow['nodes']['result']['value']['count']={'$ref':'nodes.choose.count'}
        for primary,safe,expected,index in [({'c1':'one'},{'c2':'two'},'c1',0),
                                           ({},{'c2':'two'},'c2',1),({},{},'c3',2)]:
            with self.subTest(index=index):
                client=Client();trace={}
                data={'state':{},'criteria':primary,'safe':safe,'all':{'c3':'three'}}
                self.assertEqual(Flow(flow).run(data,client,trace),{'candidate':expected,'count':1})
                self.assertEqual(client.calls,0)
                self.assertEqual(trace['steps'][0]['criteriaIndex'],index)
                self.assertEqual(trace['steps'][0]['inputCount'],1)
        with self.assertRaisesRegex(FlowError,'nonempty'):
            Flow(flow).run({'state':{},'criteria':{},'safe':{},'all':{}},Client())
    def test_fallback_never_masks_invalid_data_or_provider_failure(self):
        flow=definition();flow['nodes']['choose']['fallback_criteria']=[{'c9':'fallback'}]
        for bad in ([],None,{'c0':''}):
            client=Client()
            with self.assertRaises(FlowError):Flow(flow).run({'state':{},'criteria':bad},client)
            self.assertEqual(client.calls,0)
        client=Client(fail=True);trace={}
        with self.assertRaisesRegex(FlowError,'temporary failure'):
            Flow(flow).run(inputs(2),client,trace)
        self.assertEqual(client.calls,1);self.assertEqual(trace['steps'][0]['criteriaIndex'],0)
        self.assertNotIn('result',trace)
    def test_fallback_shape_references_and_selected_pool_budget_are_validated(self):
        for value in (None,{},[],[1]):
            flow=definition();flow['nodes']['choose']['fallback_criteria']=value
            with self.assertRaisesRegex(FlowError,'fallback_criteria'):Flow(flow)
        flow=definition();flow['nodes']['choose']['fallback_criteria']=[{'$ref':'nodes.result.choice'}]
        with self.assertRaisesRegex(FlowError,'not available'):Flow(flow)
        flow=definition(max_calls=1);flow['nodes']['choose']['fallback_criteria']=[{'$ref':'input.all'}]
        client=Client()
        with self.assertRaisesRegex(FlowError,'budget'):
            Flow(flow).run({'state':{},'criteria':{},'all':inputs(70)['criteria']},client)
        self.assertEqual(client.calls,0)
    def test_insufficient_budget_fails_before_any_request(self):
        for limits in ({'max_calls':10},{'max_steps':5}):
            client=Client()
            with self.assertRaisesRegex(FlowError,'budget'):Flow(definition(**limits)).run(inputs(300),client)
            self.assertEqual(client.calls,0)
    def test_failure_keeps_partial_trace_but_no_result(self):
        client=Client(fail=True);trace={}
        with self.assertRaises(FlowError):Flow(definition()).run(inputs(70),client,trace)
        self.assertEqual(trace['status'],'failed');self.assertNotIn('result',trace)
        self.assertEqual(len(trace['steps'][0]['rounds']),1)
        self.assertGreater(summarize_traces([trace])['counts']['logicalCalls'],0)
    def test_size_and_output_validation(self):
        for size in (1,256,True):
            with self.assertRaises(FlowError):Flow(definition(size))
        invalid=definition();invalid['nodes']['result']['value']={'$ref':'nodes.choose.probabilities'}
        with self.assertRaisesRegex(FlowError,'only choice'):Flow(invalid)
