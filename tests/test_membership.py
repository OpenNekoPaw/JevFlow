import unittest
from jevflow import Flow, FlowError, MockClient
from jevflow.core import compare

class MembershipTests(unittest.TestCase):
    def test_membership_uses_existing_typed_equality(self):
        self.assertTrue(compare(['resource_a', 2], 'contains', 2.0))
        self.assertFalse(compare([True], 'contains', 1))
        self.assertTrue(compare([], 'not_contains', 'resource_a'))
        self.assertFalse(compare(['resource_ab'], 'contains', 'resource_a'))
        self.assertTrue(compare([{'x': 1}], 'contains', {'x': 1}))
        for invalid in ['resource_a', {'resource_a': True}, None, 1]:
            with self.assertRaises(FlowError):compare(invalid, 'not_contains', 'resource_a')

    def test_model_label_changes_scope_before_selection(self):
        definition={'version':1,'name':'membership','start':'tag','nodes':{
            'tag':{'type':'evaluate','state':{},'questions':{'resource':{'type':'choice','instructions':'Choose a resource to retain','criteria':{'a':'Retain a','none':'Release all'}}},'next':'filter'},
            'filter':{'type':'filter','items':{'spend':{'description':'spends a','resources':['a']},'save':{'description':'saves a','resources':[]}},'where':[{'field':'resources','op':'not_contains','value':{'$ref':'nodes.tag.resource.choice'}}],'next':'result'},
            'result':{'type':'return','value':{'$ref':'nodes.filter.criteria'}}}}
        for label,expected in [('a',{'save'}),('none',{'save','spend'})]:
            trace={};result=Flow(definition).run({},MockClient({'tag':{'resource':{'type':'choice','choice':label,'probabilities':{'a':int(label=='a'),'none':int(label=='none')}}}}),trace)
            self.assertEqual(set(result),expected)
            self.assertEqual(trace['steps'][1]['keptIds'],list(result))
        definition['nodes']['filter']['items']['spend']['resources']='a'
        with self.assertRaises(FlowError):Flow(definition).run({},MockClient({'tag':{'resource':{'type':'choice','choice':'none','probabilities':{'a':0,'none':1}}}}))
