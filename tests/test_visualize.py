import json
import io
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from jevflow import Flow, MockClient, load_flow
from jevflow.__main__ import main
from jevflow.topology import graph_data
from jevflow.visualize import render

ROOT=Path(__file__).resolve().parents[1]
class ViewerTests(unittest.TestCase):
    def test_yaml_configuration_can_be_viewed_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'preview.html'
            with patch('jevflow.core._run_snapshot', side_effect=AssertionError('Preview must not execute')), redirect_stdout(io.StringIO()):
                code = main(['visualize', str(ROOT/'examples/parallel.yaml'), '--output', str(output)])
            self.assertEqual(code, 0)
            page = output.read_text()
            data = json.loads(re.search(r'<script type="application/json" id="data">(.*?)</script>', page, re.S)[1])
            self.assertIsNone(data['trace'])
            self.assertIsNone(data['replay'])
            self.assertFalse(data['finished'])
            self.assertEqual(data['graph']['start'], load_flow(ROOT/'examples/parallel.yaml')['start'])
            self.assertTrue(any(n['type'] == 'join' for n in data['graph']['nodes'].values()))

    def test_offline_and_live_share_protocol_and_local_assets(self):
        flow=load_flow(ROOT/'examples/parallel.yaml')
        trace={};Flow(flow).run(json.loads((ROOT/'examples/parallel-input.json').read_text()),MockClient(json.loads((ROOT/'examples/parallel-mock.json').read_text())),trace)
        output=render(flow,trace=trace)
        payload=json.loads(re.search(r'<script type="application/json" id="data">(.*?)</script>',output,re.S)[1])
        self.assertEqual(payload['graph'],json.loads(json.dumps(graph_data(flow))))
        self.assertEqual(payload['graph']['flowHash'],trace['flowHash'])
        self.assertEqual(payload['graph']['schemaVersion'],1)
        self.assertNotIn('input',payload['trace'])
        self.assertNotRegex(output,r'<script[^>]+src=')
        for name in ('viewer.js','dagre.min.js','viewer.css'):
            self.assertIn((ROOT/'jevflow/web'/name).read_text().strip(),output)

    def test_embedded_node_strings_cannot_end_script(self):
        flow={'version':1,'name':'test','start':'done','nodes':{'done':{'type':'return','title':'</script><img src=x onerror=alert(1)>','value':1}}}
        output=render(flow)
        self.assertNotIn('<img src=x',output)
        payload=json.loads(re.search(r'<script type="application/json" id="data">(.*?)</script>',output,re.S)[1])
        self.assertEqual(payload['graph']['nodes']['done']['title'],flow['nodes']['done']['title'])
