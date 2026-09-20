import copy
import json
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from jevflow import FlowError
from jevflow.client import MockClient
from jevflow.execution import ExecutionService
from jevflow.monitor import RunMonitor
from jevflow.replay import RunRecorder
from jevflow.visualize import render_replay

ROOT=Path(__file__).resolve().parents[1]


def states(journal):
    state=copy.deepcopy(journal['initial'])
    yield copy.deepcopy(state)
    for event in journal['events']:
        for patch in event['patches']:
            if not patch['path']:
                state=copy.deepcopy(patch['value']);continue
            parent=state
            for part in patch['path'][:-1]:parent=parent[part]
            key=patch['path'][-1]
            if patch.get('remove'):del parent[key]
            elif isinstance(parent,list) and key==len(parent):parent.append(copy.deepcopy(patch['value']))
            else:parent[key]=copy.deepcopy(patch['value'])
        yield copy.deepcopy(state)


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def run_parallel(self):
        mock=json.loads((ROOT/'examples/parallel-mock.json').read_text())
        inputs=json.loads((ROOT/'examples/parallel-input.json').read_text())
        seen=[]
        class Client(MockClient):
            def evaluate(self,state,*args,**kwargs):
                seen.append(state);time.sleep(.015)
                return super().evaluate(state,*args,**kwargs)
        monitor=RunMonitor()
        service=ExecutionService(self.root,max_concurrency=1,client_factory=lambda:Client(mock),monitor=monitor)
        report=service.run(ROOT/'examples/parallel.yaml',inputs,session_id='match-42',step_id='2',actor_id='seat-1')
        self.assertEqual(report['status'],'completed')
        return report,json.loads(Path(report['trace']).read_text()),monitor,seen

    def test_observed_parallel_queue_and_terminal_results_are_replayable(self):
        report,trace,monitor,seen=self.run_parallel()
        journal=trace['replay'];frames=list(states(journal))
        self.assertFalse(journal['truncated'])
        self.assertEqual(frames[-1]['trace']['steps'],trace['steps'])
        self.assertEqual(frames[-1]['report']['result'],report['result'])
        self.assertEqual(frames[-1]['requests'],monitor.get(report['runId'])['requests'])
        self.assertTrue(any(any(r['status']=='queued' for r in s['requests'].values()) for s in frames))
        self.assertTrue(any(any(r['status']=='running' for r in s['requests'].values()) for s in frames))
        self.assertNotIn('report',frames[0]);self.assertFalse(frames[0]['trace']['steps'])
        self.assertEqual([e['offsetMs'] for e in journal['events']],sorted(e['offsetMs'] for e in journal['events']))
        self.assertEqual(trace['context'],{'sessionId':'match-42','stepId':'2','actorId':'seat-1'})
        self.assertNotIn('match-42',json.dumps(seen))
        restored=RunMonitor();restored.restore(self.root)
        row=restored.snapshot()['runs'][0]
        self.assertEqual(row['context'],trace['context']);self.assertEqual(row['receivedAtMs'],trace['receivedAtMs'])
        self.assertNotIn('replay',row)
        self.assertEqual(restored.get(report['runId'])['replay'],journal)
        if shutil.which('node'):
            script="const {frame,count}=require('./jevflow/web/replay.js');const t=require(process.argv[1]);const r={trace:t,replay:t.replay,finished:true,status:t.status};console.log(JSON.stringify(frame(r,count(r))));"
            result=subprocess.run(['node','-e',script,report['trace']],cwd=ROOT,capture_output=True,text=True,check=True)
            replayed=json.loads(result.stdout)
            self.assertEqual(replayed['trace']['steps'],trace['steps']);self.assertEqual(replayed['report']['result'],report['result'])

    def test_load_failure_context_validation_and_late_events(self):
        service=ExecutionService(self.root)
        report=service.run(self.root/'missing.yaml',{},session_id='match')
        trace=json.loads(Path(report['trace']).read_text())
        self.assertEqual(list(states(trace['replay']))[-1]['status'],'failed')
        invalid=service.run(ROOT/'examples/parallel.yaml',{},session_id=7)
        self.assertEqual(invalid['status'],'failed');self.assertIn('context IDs',invalid['error']['message'])
        recorder=RunRecorder('run',time.monotonic());recorder.started_run()
        final=recorder.finish('cancelled',None,None,10)
        recorder.request('run','late',status='completed')
        self.assertEqual(recorder.journal,final)

    def test_recording_limits_are_explicit_and_do_not_stop_execution(self):
        recorder=RunRecorder('run',time.monotonic(),max_events=1)
        recorder.started_run();result=recorder.finish('completed',{'ok':1},None,1)
        self.assertTrue(result['truncated']);self.assertEqual(len(result['events']),1)
        recorder=RunRecorder('run',time.monotonic(),max_bytes=1)
        result=recorder.finish('completed',1,None,1)
        self.assertTrue(result['truncated']);self.assertEqual(result['events'],[])

    def test_multi_archive_preserves_versions_and_escapes_metadata(self):
        import yaml
        path=self.root/'flow.yaml'
        flow={'version':1,'name':'test','revision':'1','start':'end','nodes':{'end':{'type':'return','value':1}}}
        service=ExecutionService(self.root)
        traces=[]
        for revision in ('1','2'):
            flow['revision']=revision;path.write_text(yaml.safe_dump(flow))
            report=service.run(path,{},session_id='</script><img src=x>')
            traces.append(json.loads(Path(report['trace']).read_text()))
        html=render_replay(traces)
        self.assertNotIn('<img src=x>',html);self.assertNotRegex(html,r'<script[^>]+src=')
        payload=json.loads(re.search(r'<script type="application/json" id="archive">(.*?)</script>',html,re.S)[1])
        self.assertEqual([r['revision'] for r in payload],['1','2'])
        self.assertNotEqual(payload[0]['flowHash'],payload[1]['flowHash'])
        self.assertNotIn('input',payload[0]['trace'])
        output=self.root/'archive.html'
        cli=subprocess.run([__import__('sys').executable,'-m','jevflow','replay',*[str(self.root/(t['runId']+'.json')) for t in traces],'--output',str(output)],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(cli.returncode,0,cli.stderr);self.assertTrue(output.exists())

    def test_online_history_and_archive_share_names_times_and_versions(self):
        import yaml
        from jevflow.metrics import summarize_traces
        path = self.root / 'flow.yaml'
        path.write_text(yaml.safe_dump({'version': 1, 'name': 'internal-id', 'title': '展示名称',
            'start': 'done', 'nodes': {'done': {'type': 'return', 'value': 1}}}))
        monitor = RunMonitor()
        report = ExecutionService(self.root, monitor=monitor).run(path, {'private': 'input'})
        trace_path = Path(report['trace'])
        trace = json.loads(trace_path.read_text())
        # Old traces retain their actual start time even if the archive file was copied later.
        del trace['receivedAtMs']
        trace['startedAt'] = '2020-01-01T00:00:00Z'
        trace_path.write_text(json.dumps(trace))
        restored = RunMonitor(); restored.restore(self.root)
        online = restored.get(report['runId'])
        html = render_replay([trace])
        offline = json.loads(re.search(r'<script type="application/json" id="archive">(.*?)</script>', html, re.S)[1])[0]
        for field in ('name', 'receivedAtMs', 'revision', 'flowHash', 'context', 'status', 'replay'):
            self.assertEqual(online[field], offline[field], field)
        self.assertEqual(online['name'], monitor.get(report['runId'])['name'])
        self.assertEqual(online['name'], '展示名称')
        self.assertEqual(online['receivedAtMs'], 1577836800000)
        self.assertNotIn('input', offline['trace'])
        self.assertIn('input', online['trace'])
        self.assertEqual(offline['graph']['flowHash'], report['flowHash'])
        del trace['flowHash']  # Legacy metrics recompute the same snapshot identity.
        self.assertEqual(summarize_traces([trace])['groups'][0]['flowHash'], report['flowHash'])

    def test_mismatched_snapshot_is_rejected_by_both_history_readers(self):
        report, trace, _, _ = self.run_parallel()
        trace['flow']['revision'] = 'changed-after-run'
        Path(report['trace']).write_text(json.dumps(trace))
        restored = RunMonitor(); restored.restore(self.root)
        self.assertIsNone(restored.get(report['runId']))
        with self.assertRaisesRegex(FlowError, 'different flow snapshot'):
            render_replay([trace])

    def test_request_finalization_matches_monitor_and_replay_and_ignores_late_results(self):
        monitor = RunMonitor()
        monitor.begin('run', 'unused.yaml', 'mock', 0)
        recorder = RunRecorder('run', time.monotonic(), monitor)
        for status in ('queued', 'running', 'completed', 'failed', 'cancelled'):
            recorder.request('run', status, nodeId=status, status=status)
        attempts = [{'attempt': 1, 'status': 'running'}]
        recorder.transport('run', 'running', attempts)
        attempts.clear()
        journal = recorder.finish('cancelled', None, None, 10)
        monitor.finish('run', {'status': 'cancelled'}, {'replay': journal})
        final = list(states(journal))[-1]
        before = monitor.get('run')
        self.assertEqual(final['requests'], before['requests'])
        self.assertEqual({k: r['status'] for k, r in final['requests'].items()}, {
            'queued': 'cancelled', 'running': 'abandoned', 'completed': 'completed',
            'failed': 'failed', 'cancelled': 'cancelled'})
        self.assertEqual(final['requests']['running']['transportAttempts'], [{'attempt': 1, 'status': 'running'}])
        recorder.request('run', 'running', status='completed')
        recorder.transport('run', 'running', [])
        self.assertEqual(recorder.journal, journal)
        self.assertEqual(monitor.get('run'), before)
