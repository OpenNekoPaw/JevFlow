import json
import os
import subprocess
import time
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from jevflow import Flow, FlowError, GatewayClient


def success():
    return subprocess.CompletedProcess([],0,json.dumps({'answers':{'ok':{'type':'boolean','probability':0.8}},'timing':{'sdkMs':10,'bridgeMs':11}}),'')


def flow():
    question={'ok':{'type':'noul','instructions':'Ready?'}}
    return Flow({'version':1,'name':'deadline','start':'first','nodes':{
        'first':{'type':'evaluate','state':{},'questions':question,'next':'second'},
        'second':{'type':'evaluate','state':{'$ref':'nodes.first.ok'},'questions':question,'next':'done'},
        'done':{'type':'return','value':{'$ref':'nodes.second.ok.noul'}}}})


@patch.dict(os.environ, {'AI_GATEWAY_API_KEY':'private-test-value'})
class GatewayDeadlineTests(unittest.TestCase):
    def test_real_bridge_process_timeout_then_retry(self):
        # Local fault injection: the first subprocess hangs, the next succeeds.
        # No SDK, model, credentials or external network is used.
        with tempfile.TemporaryDirectory() as directory:
            bridge = Path(directory) / 'bridge.mjs'
            marker = str(Path(directory) / 'attempt')
            bridge.write_text("import {existsSync,writeFileSync,readFileSync} from 'node:fs';\n"
                + "const marker=" + json.dumps(marker) + ";\n"
                + "const request=JSON.parse(readFileSync(0,'utf8'));\n"
                + "if(!existsSync(marker)){writeFileSync(marker,'1');setTimeout(()=>{},10000);}\n"
                + "else console.log(JSON.stringify({answers:{ok:{type:'boolean',probability:0.8}}}));\n")
            result = GatewayClient(bridge=bridge, max_retries=1, attempt_timeout=0.6).evaluate(
                {}, {'ok': {'type': 'noul', 'instructions': 'Ready?'}}, timeout=3)
        self.assertEqual(result['answers']['ok']['noul'], 0.8)
        self.assertEqual([a['kind'] for a in result['transportAttempts']], ['timeout', 'success'])
        self.assertGreaterEqual(result['transportAttempts'][0]['elapsedMs'], 500)

    def test_timed_out_node_retries_without_repeating_completed_node(self):
        requests=[]
        def run(*args,**kwargs):
            requests.append(json.loads(kwargs['input']))
            if len(requests)==2: raise subprocess.TimeoutExpired('private-test-value',1)
            return success()
        trace={}
        with patch('jevflow.gateway.subprocess.run',side_effect=run),patch('jevflow.gateway.time.sleep'):
            result=flow().run({},GatewayClient(max_retries=1,attempt_timeout=8),trace,deadline_unix_ms=time.time()*1000+20000)
        self.assertEqual(result,0.8)
        self.assertEqual(len(requests),3)
        self.assertEqual(requests[1]['state'],requests[2]['state'])
        self.assertNotEqual(requests[0]['state'],requests[1]['state'])
        attempts=trace['steps'][1]['response']['transportAttempts']
        self.assertEqual([a['kind'] for a in attempts],['timeout','success'])
        self.assertLessEqual(attempts[0]['budgetMs'],8000)
        self.assertNotIn('private-test-value',json.dumps(trace))

    def test_failure_keeps_attempts_and_timing_on_failed_step(self):
        trace={}
        with patch('jevflow.gateway.subprocess.run',side_effect=subprocess.TimeoutExpired('secret',1)),patch('jevflow.gateway.time.sleep'):
            with self.assertRaises(FlowError) as caught:
                flow().run({},GatewayClient(max_retries=1,attempt_timeout=8),trace)
        self.assertTrue(caught.exception.retryable)
        step=trace['steps'][0]
        self.assertEqual(step['status'],'failed')
        self.assertIn('elapsedMs',step)
        self.assertIn('startedAt',step)
        self.assertEqual(len(step['transport']['transportAttempts']),2)
        self.assertNotIn('result',trace)
        self.assertNotIn('secret',json.dumps(trace))

    def test_absolute_deadline_limits_client_and_bridge(self):
        limit=time.time()*1000+1200
        captured=[]
        def run(*args,**kwargs):
            captured.append(kwargs);return success()
        with patch('jevflow.gateway.subprocess.run',side_effect=run):
            flow().run({},GatewayClient(attempt_timeout=8),deadline_unix_ms=limit)
        for invocation in captured:
            self.assertLessEqual(invocation['timeout'],1.2)
            payload=json.loads(invocation['input'])
            self.assertLessEqual(payload['deadlineUnixMs'],limit)
        with patch('jevflow.gateway.subprocess.run') as run:
            with self.assertRaisesRegex(FlowError,'timeout'):
                flow().run({},GatewayClient(),deadline_unix_ms=time.time()*1000-1)
        run.assert_not_called()

    def test_structured_bad_request_fails_without_retry(self):
        failed=subprocess.CompletedProcess([],1,json.dumps({'error':{'code':'http','httpStatus':400},'timing':{'sdkMs':12,'bridgeMs':13}}),'secret')
        with patch('jevflow.gateway.subprocess.run',return_value=failed) as run:
            with self.assertRaisesRegex(FlowError,'HTTP 400') as caught:
                flow().run({},GatewayClient(max_retries=2))
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(run.call_count,1)

    def test_exhausted_budget_never_launches_retry(self):
        with patch('jevflow.gateway.time.monotonic',side_effect=[0,0,0,2,2,2]),patch('jevflow.gateway.subprocess.run',side_effect=subprocess.TimeoutExpired('node',1)) as run:
            with self.assertRaises(FlowError):
                GatewayClient(max_retries=2).evaluate({}, {'ok':{'type':'noul','instructions':'Ready?'}},timeout=1)
        self.assertEqual(run.call_count,1)
