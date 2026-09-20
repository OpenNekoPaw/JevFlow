import copy
import unittest
from jevflow import Flow, FlowError, MockClient

class OrderedFilterTests(unittest.TestCase):
    def setUp(self):
        self.definition={'version':1,'name':'bounded-candidates','start':'narrow','nodes':{
            'narrow':{'type':'filter','items':{'$ref':'input.items'},'where':[{'field':'eligible','op':'eq','value':True}],
                'order_by':[{'field':'cost','direction':'asc'},{'field':'benefit','direction':'desc'},{'field':'key','direction':'asc'}],
                'limit':1,'next':'result'},
            'result':{'type':'return','value':{'ids':{'$ref':'nodes.narrow.ids'},'beforeLimit':{'$ref':'nodes.narrow.matchedCount'},'count':{'$ref':'nodes.narrow.count'}}}}}
        self.items={k:{'description':k,'eligible':eligible,'cost':cost,'benefit':benefit,'key':k}
                    for k,eligible,cost,benefit in [('unsafe',False,0,99),('expensive',True,2,10),('weak',True,1,2),('best',True,1,3)]}
    def test_filters_before_lexicographic_order_and_never_calls_model(self):
        trace={};r=Flow(self.definition).run({'items':self.items},MockClient({}),trace)
        self.assertEqual(r,{'ids':['best'],'beforeLimit':3,'count':1})
        self.assertEqual(trace['calls'],0)
        self.assertEqual(trace['steps'][0]['excluded'],{'unsafe':[0]})
        self.assertEqual(trace['steps'][0]['truncatedIds'],['weak','expensive'])
        self.assertEqual(len(self.items),4)
    def test_input_permutation_does_not_change_explicit_tie_break(self):
        self.items['tie']={**self.items['best'],'key':'zzz'}
        for items in [self.items,dict(reversed(list(self.items.items())))]:
            self.assertEqual(Flow(self.definition).run({'items':items},MockClient({}))['ids'],['best'])
    def test_empty_ordered_pool_stays_empty(self):
        self.assertEqual(Flow(self.definition).run({'items':{}},MockClient({})),{'ids':[],'count':0,'beforeLimit':0})
    def test_invalid_ordering_rejected(self):
        for change in [{'order_by':[]},{'order_by':[{'field':'cost','direction':'up'}]},{'limit':0},{'limit':True}]:
            d=copy.deepcopy(self.definition);d['nodes']['narrow'].update(change)
            with self.subTest(change=change),self.assertRaises(FlowError):Flow(d)
        d=copy.deepcopy(self.definition);del d['nodes']['narrow']['order_by']
        with self.assertRaises(FlowError):Flow(d)
    def test_bad_sort_values_fail_closed(self):
        for bad in [None,True,{},'mixed']:
            items=copy.deepcopy(self.items);items['best']['cost']=bad
            with self.subTest(bad=bad),self.assertRaises(FlowError):Flow(self.definition).run({'items':items},MockClient({}))
        items=copy.deepcopy(self.items);del items['best']['cost']
        with self.assertRaises(FlowError):Flow(self.definition).run({'items':items},MockClient({}))
    def test_no_limit_preserves_all_sorted_ties(self):
        d=copy.deepcopy(self.definition);del d['nodes']['narrow']['limit']
        self.assertEqual(Flow(d).run({'items':self.items},MockClient({}))['ids'],['best','weak','expensive'])
