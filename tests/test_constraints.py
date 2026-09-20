import copy
import json
import unittest
from pathlib import Path
from jevflow import Flow, FlowError, MockClient, load_flow
from jevflow.visualize import render

ROOT=Path(__file__).resolve().parents[1]
class ConstraintTests(unittest.TestCase):
    def setUp(self):
        self.definition=load_flow(ROOT/'examples/constrained.yaml')
        self.flow=Flow(self.definition)
        self.inputs=json.loads((ROOT/'examples/constrained-input.json').read_text())
        self.mock=json.loads((ROOT/'examples/constrained-mock.json').read_text())

    def test_excluded_option_never_reaches_provider(self):
        trace={}
        self.assertEqual(self.flow.run(self.inputs,MockClient(self.mock),trace),{'candidate':'fast'})
        question=next(s for s in trace['steps'] if s['type']=='evaluate')['request']['questions']['action']
        self.assertEqual(set(question['criteria']),{'fast','standard'})
        self.assertEqual(trace['steps'][0]['excluded'],{'premium':[0]})
        self.assertEqual(trace['calls'],1)
        self.assertIn('premium',self.inputs['candidates'])

    def test_provider_cannot_reintroduce_excluded_option(self):
        self.mock['select']['action']={'type':'choice','choice':'premium','probabilities':{'fast':0.2,'standard':0.1,'premium':0.7}}
        with self.assertRaisesRegex(FlowError,'distribution labels'):
            self.flow.run(self.inputs,MockClient(self.mock))

    def test_singleton_and_empty_do_not_call_provider(self):
        for budget,expected in [(10,{'candidate':'standard'}),(1,{'candidate':None,'reason':'no_eligible_candidate'})]:
            with self.subTest(budget=budget):
                self.inputs['budget']=budget
                trace={}
                self.assertEqual(self.flow.run(self.inputs,MockClient({}),trace),expected)
                self.assertEqual(trace['calls'],0)

    def test_missing_fact_fails_closed(self):
        del self.inputs['candidates']['premium']['cost']
        trace={}
        with self.assertRaisesRegex(FlowError,'Missing value'):
            self.flow.run(self.inputs,MockClient({}),trace)
        self.assertEqual(trace['calls'],0)

    def test_empty_dynamic_choice_without_branch_fails(self):
        self.definition['nodes']['available']['cases'][0]['right']=-1
        self.inputs['budget']=0
        trace={}
        with self.assertRaisesRegex(FlowError,'1..255'):
            Flow(self.definition).run(self.inputs,MockClient({}),trace)
        self.assertEqual(trace['calls'],0)

    def test_filter_reference_must_dominate_consumer(self):
        self.definition['start']='available'
        self.definition['nodes']['available']['default']='eligible'
        with self.assertRaisesRegex(FlowError,'Unreachable|every path'):
            Flow(self.definition)

    def test_visualizer_trace_matches_snapshot_and_escapes_data(self):
        self.definition['title']='</script><img src=x onerror=alert(1)>'
        trace={}
        Flow(self.definition).run(self.inputs,MockClient(self.mock),trace)
        output=render(self.definition,trace=trace)
        self.assertIn('id="viewer"',output)
        self.assertIn('premium',output)
        self.assertNotIn('<img src=x',output)
        self.assertIn('候选筛选',output)
        self.assertIn('flowchart LR',render(self.definition,'mermaid'))
        self.assertIn('flowchart TD',render(self.definition,'mermaid',direction='TD'))
        other=copy.deepcopy(self.definition);other['revision']='2'
        with self.assertRaisesRegex(FlowError,'different flow snapshot'):
            render(other,trace=trace)
