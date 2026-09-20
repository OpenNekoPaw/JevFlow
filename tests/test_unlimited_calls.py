import copy
import time
import unittest
from jevflow import Flow,FlowError,MockClient,validate_flow
from test_parallel import definition as parallel_definition,inputs as parallel_inputs,answer
from test_select import definition as select_definition,inputs as select_inputs,Client

def chain():
    return {'version':1,'name':'nine','start':'n0','limits':{'max_calls':None},'nodes':{
        **{f'n{i}':{'type':'evaluate','state':{},'questions':{'risk':{'type':'noul','instructions':'Assess risk'}},'next':f'n{i+1}' if i<8 else 'done'} for i in range(9)},
        'done':{'type':'return','value':True}}}

class UnlimitedCallsTests(unittest.TestCase):
    def test_serial_explicit_null_exceeds_default_and_still_counts(self):
        trace={};f=chain();m=MockClient({f'n{i}':answer() for i in range(9)})
        self.assertTrue(Flow(f).run({},m,trace));self.assertEqual(trace['calls'],9)
        for cap in ['omitted',2]:
            limited=copy.deepcopy(f)
            if cap=='omitted':del limited['limits']['max_calls']
            else:limited['limits']['max_calls']=cap
            with self.assertRaisesRegex(FlowError,'call budget'):Flow(limited).run({},m)
    def test_parallel_and_batched_selection_exceed_default(self):
        f=parallel_definition(count=9);f['limits']['max_calls']=None;t={}
        Flow(f).run(parallel_inputs(9),MockClient({f'analyze.b{i}':answer() for i in range(9)}),t)
        self.assertEqual(t['calls'],9)
        t={};self.assertEqual(Flow(select_definition(max_calls=None)).run(select_inputs(300),Client(),t),{'candidate':'c299'})
        self.assertEqual(t['calls'],11)
    def test_null_does_not_disable_deadline_or_step_budget(self):
        f=chain();m=MockClient({f'n{i}':answer() for i in range(9)})
        with self.assertRaisesRegex(FlowError,'timeout'):Flow(f).run({},m,deadline_unix_ms=(time.time()-1)*1000)
        f['limits']['max_steps']=2
        with self.assertRaisesRegex(FlowError,'step budget'):Flow(f).run({},m)
    def test_only_call_cap_accepts_null(self):
        for field in ['max_steps','max_concurrency','timeout_seconds']:
            f=chain();f['limits'][field]=None
            with self.assertRaises(FlowError):validate_flow(f)
        for value in [0,-1,True,'unlimited']:
            f=chain();f['limits']['max_calls']=value
            with self.assertRaises(FlowError):validate_flow(f)
