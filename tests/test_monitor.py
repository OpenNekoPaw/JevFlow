"""Local HTTP/SSE integration and real execution lifecycle; no model calls."""
import copy
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from jevflow import Flow, MockClient
from jevflow.execution import ExecutionService
from jevflow.monitor import MonitorServer, RunMonitor

ROOT = Path(__file__).resolve().parents[1]


def definition():
    return {"version": 1, "name": "监控测试", "revision": "1", "start": "analyze", "nodes": {
        "analyze": {"type": "parallel", "branches": {key: {"state": {}, "questions": {
            "ok": {"type": "noul", "instructions": "检查状态"}}} for key in ("a", "b")}, "next": "done"},
        "done": {"type": "return", "value": "old"}}}


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root/'flow.yaml'
        self.path.write_text(yaml.safe_dump(definition()))
        self.monitor = RunMonitor()

    def wait_for(self, predicate):
        end = time.monotonic()+3
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(0.005)
        self.fail('Timed out waiting for monitor state')

    def test_parallel_live_progress_queue_snapshot_version_and_terminal_freeze(self):
        entered, release = threading.Event(), threading.Event()
        class Client:
            mode = 'mock'
            def evaluate(self, *args, **kwargs):
                entered.set(); release.wait(3)
                return {'answers': {'ok': {'type': 'noul', 'noul': 1}}}
        service = ExecutionService(self.root, max_runs=1, max_concurrency=1,
                                   client_factory=Client, monitor=self.monitor)
        with ThreadPoolExecutor(2) as pool:
            future = pool.submit(service.run, self.path, {})
            self.assertTrue(entered.wait(2))
            try:
                run_id = self.monitor.snapshot()['runs'][0]['runId']
                self.wait_for(lambda: len(self.monitor.get(run_id)['requests']) == 2)
                running = self.monitor.get(run_id)
                self.assertEqual(running['status'], 'running')
                self.assertEqual({c['status'] for c in running['requests'].values()}, {'queued','running'})
                self.assertEqual(set(running['trace']['steps'][0]['branches']), {'a','b'})
                self.assertFalse((self.root/(run_id+'.json')).exists())  # Progress precedes trace persistence.
                changed = definition(); changed['revision']='2'; changed['nodes']['done']['value']='new'
                self.path.write_text(yaml.safe_dump(changed))
                second = pool.submit(service.run, self.path, {})
                self.wait_for(lambda: len(self.monitor.snapshot()['runs']) == 2)
                self.wait_for(lambda: any(r.get('revision') == '2' for r in self.monitor.snapshot()['runs']))
                rows = self.monitor.snapshot()['runs']
                self.assertEqual({r['status'] for r in rows}, {'queued','running'})
                self.assertNotEqual(rows[0]['flowHash'], rows[1]['flowHash'])
                release.set()
                old,new = future.result(3),second.result(3)
            finally:
                release.set()
        self.assertEqual((old['result'],new['result']), ('old','new'))
        snapshot = self.monitor.get(run_id)
        self.assertTrue(snapshot['finished'])
        self.monitor.request(run_id, 'late', status='completed')
        self.monitor.observe(run_id, {})
        self.assertEqual(snapshot, self.monitor.get(run_id))
        running['trace']['steps'].clear()
        self.assertTrue(self.monitor.get(run_id)['trace']['steps'])

    def test_fail_cancel_timeout_and_trace_write_failure_are_terminal(self):
        service = ExecutionService(self.root, monitor=self.monitor)
        failed = service.run(self.path, {}, mock={})
        self.assertEqual(self.monitor.get(failed['runId'])['status'],'failed')
        cancel = threading.Event(); cancel.set()
        cancelled = service.run(self.path, {}, cancel_event=cancel)
        self.assertEqual(self.monitor.get(cancelled['runId'])['status'],'cancelled')
        expired = service.run(self.path, {}, mock={}, deadline_unix_ms=1)
        self.assertEqual(self.monitor.get(expired['runId'])['trace']['flow']['revision'],'1')
        immediate={'version':1,'name':'return','start':'done','nodes':{'done':{'type':'return','value':1}}}
        self.path.write_text(yaml.safe_dump(immediate))
        write_failed=service.run(self.path, {}, trace_path=self.root)
        self.assertEqual(self.monitor.get(write_failed['runId'])['status'],'failed')
        self.assertEqual(write_failed['error']['code'],'trace_write_failed')

    def test_cancel_abandoned_requests_never_mutate_completed_monitor(self):
        release,entered,cancel = threading.Event(),threading.Event(),threading.Event()
        class Client:
            mode='mock'
            def evaluate(self, *args, **kwargs):
                entered.set();release.wait(3)
                return {'answers':{'ok':{'type':'noul','noul':1}}}
        service=ExecutionService(self.root,client_factory=Client,monitor=self.monitor)
        with ThreadPoolExecutor(1) as pool:
            task=pool.submit(service.run,self.path,{},cancel_event=cancel)
            self.assertTrue(entered.wait(2));cancel.set()
            try:
                report=task.result(2)
                before=self.monitor.get(report['runId'])
                self.assertEqual(before['status'],'cancelled')
                self.assertTrue(any(c['status']=='abandoned' for c in before['requests'].values()))
            finally:
                release.set()
        time.sleep(.08)
        self.assertEqual(before,self.monitor.get(report['runId']))

    def test_http_auth_graph_sse_reconnect_and_snapshot_reset(self):
        server=MonitorServer(self.monitor, require_token=True).start();self.addCleanup(server.close)
        base=server.url.split('/#')[0]
        auth={'Authorization':'Bearer '+server.token}
        def get(path,headers=None):
            return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(urllib.request.Request(base+path,headers=headers or auth),timeout=3)
        with get('/') as response:
            self.assertIn('JevFlow',response.read().decode())
        for path in ('/viewer.js', '/viewer.css', '/dagre.min.js', '/replay.js'):
            with get(path) as response:
                self.assertEqual(response.status, 200)
                self.assertGreater(len(response.read()), 100)
        for path in ('/api/runs','/api/events'):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                get(path,{'Authorization':'Bearer invalid'})
            self.assertEqual(caught.exception.code,401)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            get('/api/runs',{**auth,'Origin':'http://evil.invalid'})
        self.assertEqual(caught.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            get('/api/runs',{**auth,'Host':'evil.invalid'})
        self.assertEqual(caught.exception.code,403)
        with get('/api/events?token='+server.token+'&after=0') as stream:
            self.assertEqual(stream.readline().strip(),b': connected');stream.readline()
            self.monitor.begin('first',self.path,'mock',0)
            event_id=int(stream.readline().decode().split(':')[1])
            self.assertEqual(stream.readline().strip(),b'event: update')
            data=json.loads(stream.readline().decode()[6:]);self.assertEqual(data['runId'],'first')
        self.monitor.started('first')
        with get('/api/events?token='+server.token,{'Last-Event-ID':str(event_id)}) as stream:
            stream.readline();stream.readline()
            self.assertGreater(int(stream.readline().decode().split(':')[1]),event_id)
            stream.readline();self.assertEqual(json.loads(stream.readline().decode()[6:])['type'],'run_started')
        service=ExecutionService(self.root,monitor=self.monitor)
        answer={'ok':{'type':'noul','noul':1}}
        report=service.run(self.path,{},mock={'analyze.a':answer,'analyze.b':answer})
        with get('/api/runs/'+report['runId']) as response:
            run=json.load(response)
            self.assertIn('analyze.@join',run['graph']['nodes'])
            self.assertEqual(run['trace']['steps'][-1]['status'],'completed')
        with get('/api/runs') as response:
            listed=json.load(response)
            self.assertNotIn('trace',listed['runs'][0])
        for path in ('/api/runs/unknown','/api/runs/../../etc/passwd'):
            with self.assertRaises(urllib.error.HTTPError) as caught:get(path)
            self.assertEqual(caught.exception.code,404)
        post=urllib.request.Request(base+'/api/runs',data=b'{}',headers=auth)
        with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.build_opener(urllib.request.ProxyHandler({})).open(post)
        self.assertEqual(caught.exception.code,405)

    def test_default_web_access_without_token_keeps_origin_checks(self):
        server = MonitorServer(self.monitor, preview_flow=self.path).start()
        self.addCleanup(server.close)
        self.assertIsNone(server.token)
        self.assertNotIn('#', server.url)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def get(path, headers=None, data=None):
            return opener.open(urllib.request.Request(server.url.rstrip('/')+path,
                headers=headers or {}, data=data), timeout=3)
        with get('/api/runs') as response:
            self.assertEqual(json.load(response)['runs'], [])
        with get('/api/config') as response:
            self.assertTrue(json.load(response)['configured'])
        with get('/api/preview', {'Content-Type': 'text/yaml'}, self.path.read_bytes()) as response:
            self.assertEqual(json.load(response)['status'], 'preview')
        report = ExecutionService(self.root, monitor=self.monitor).run(self.path, {}, mock={})
        with get('/api/runs/'+report['runId']) as response:
            self.assertEqual(json.load(response)['runId'], report['runId'])
        with get('/api/events?after=0') as stream:
            self.assertEqual(stream.readline().strip(), b': connected')
            stream.readline()
            self.assertTrue(stream.readline().startswith(b'id:'))
        for headers in ({'Origin': 'http://evil.invalid'}, {'Host': 'evil.invalid'}, {'Sec-Fetch-Site': 'cross-site'}):
            for path in ('/api/runs', '/api/config', '/api/events'):
                with self.subTest(path=path, headers=headers), self.assertRaises(urllib.error.HTTPError) as caught:
                    get(path, headers)
                self.assertEqual(caught.exception.code, 403)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                get('/api/preview', {**headers, 'Content-Type': 'text/yaml'}, self.path.read_bytes())
            self.assertEqual(caught.exception.code, 403)

    def test_retention_cursor_and_restart_history(self):
        monitor=RunMonitor(max_history=1,max_events=2)
        monitor.begin('active',self.path,'mock',0)
        for name in ('old','new'):
            monitor.begin(name,self.path,'mock',0)
            monitor.finish(name,{'status':'completed'},{})
        self.assertEqual({r['runId'] for r in monitor.snapshot()['runs']},{'active','new'})
        self.assertEqual(monitor.since(0,0)[0]['type'],'reset')
        self.assertEqual(monitor.since(999,0)[0]['type'],'reset')
        self.assertEqual(monitor.since(monitor.sequence,0),[])
        (self.root/'broken.json').write_text('{')
        service=ExecutionService(self.root,monitor=self.monitor)
        report=service.run(self.path,{},mock={})
        restored=RunMonitor();restored.restore(self.root)
        self.assertEqual(restored.get(report['runId'])['status'],'failed')
        self.assertTrue(restored.get(report['runId'])['historical'])

    def test_observer_failure_and_mutation_do_not_change_decision(self):
        answers={'ok':{'type':'noul','noul':1}}
        def bad(snapshot):
            snapshot['flow']['nodes'].clear()
            raise RuntimeError('viewer disconnected')
        with self.assertLogs('jevflow.core',level='WARNING'):
            result=Flow(definition()).run({},MockClient({'analyze.a':answers,'analyze.b':answers}),observer=bad)
        self.assertEqual(result,'old')

    def test_cycles_have_distinct_visits_and_selector_streams_nested_progress(self):
        seen=[]
        flow={'version':1,'name':'loop','start':'judge','nodes':{
            'judge':{'type':'evaluate','state':{},'questions':{'ok':{'type':'noul','instructions':'ready?'}},'next':'check'},
            'check':{'type':'branch','cases':[{'left':{'$ref':'nodes.judge.ok.noul'},'op':'gte','right':.8,'next':'done'}],'default':'judge'},
            'done':{'type':'return','value':True}}}
        low={'ok':{'type':'noul','noul':.2}};high={'ok':{'type':'noul','noul':1}}
        Flow(flow).run({},MockClient({'judge':[low,high]}),observer=seen.append)
        self.assertEqual([s['node'] for s in seen[-1]['steps']],['judge','check','judge','check','done'])
        self.assertTrue(any(s['steps'][-1].get('status')=='running' for s in seen if s['steps']))
        flow={'version':1,'name':'select','start':'pick','nodes':{'pick':{'type':'select','state':{},'criteria':{'a':'A','b':'B','c':'C'},'instructions':'choose','batch_size':2,'next':'done'},'done':{'type':'return','value':{'$ref':'nodes.pick.choice'}}}}
        class Client:
            mode='mock'
            def evaluate(self,state,questions,**kwargs):
                keys=list(questions['selection']['criteria'])
                return {'answers':{'selection':{'type':'choice','choice':keys[0],'probabilities':{k:float(i==0) for i,k in enumerate(keys)}}}}
        seen=[];self.assertEqual(Flow(flow).run({},Client(),observer=seen.append),'a')
        self.assertTrue(any(t['steps'][0].get('rounds') for t in seen if t['steps']))
        self.assertEqual(seen[-1]['steps'][0]['status'],'completed')


if __name__=='__main__':unittest.main()
