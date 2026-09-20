import copy
import io
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stderr

from jevflow import FlowError, load_flow
from jevflow.__main__ import main
from jevflow.execution import ExecutionService
from jevflow.monitor import MonitorServer, RunMonitor
from jevflow.preview import ConfigurationPreview, MAX_CONFIG_BYTES, preview_data
from jevflow.topology import graph_data


YAML = 'version: 1\nname: preview\nrevision: "1"\nstart: done\nnodes:\n  done: {type: return, value: 1}\n'


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'flow.yaml'
        self.path.write_text(YAML)

    def server(self):
        monitor = RunMonitor()
        server = MonitorServer(monitor, preview_flow=self.path, require_token=True).start()
        self.addCleanup(server.close)
        self.base = server.url.split('/#')[0]
        self.auth = {'Authorization': 'Bearer '+server.token}
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return server, monitor

    def request(self, path, body=None, headers=None):
        request = urllib.request.Request(self.base+path, data=body,
            headers=headers if headers is not None else {**self.auth, 'Content-Type': 'text/yaml'})
        with self.opener.open(request, timeout=3) as response:
            return json.load(response)

    def test_upload_uses_shared_validation_without_execution_or_file_writes(self):
        _, monitor = self.server()
        before = set(self.root.iterdir())
        with patch('jevflow.core._run_snapshot', side_effect=AssertionError('Preview must not execute')):
            data = self.request('/api/preview', YAML.encode())
        self.assertEqual(data['graph'], json.loads(json.dumps(graph_data(load_flow(self.path)))))
        self.assertEqual(data['status'], 'preview')
        self.assertIsNone(data['trace']); self.assertIsNone(data['replay'])
        self.assertFalse(data['finished'])
        self.assertEqual(monitor.snapshot()['runs'], [])
        self.assertEqual(set(self.root.iterdir()), before)
        self.assertEqual(self.path.read_text(), YAML)

    def test_preview_rejects_bad_yaml_and_does_not_interpret_paths(self):
        self.server()
        for source in ('name: a\nname: b', '!!python/object/apply:os.system [false]', 'bad: [', str(self.path), '{}', '[]'):
            with self.subTest(source=source), self.assertRaises(urllib.error.HTTPError) as caught:
                self.request('/api/preview', source.encode())
            self.assertEqual(caught.exception.code, 422)
            self.assertIn('error', json.load(caught.exception))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('/api/preview', b'\xff')
        self.assertEqual(caught.exception.code, 422)

    def test_preview_requires_auth_origin_and_bounded_payload(self):
        self.server()
        cases = [({'Content-Type': 'text/yaml'}, 401),
                 ({**self.auth, 'Content-Type': 'text/yaml', 'Origin': 'http://evil.invalid'}, 403),
                 ({**self.auth, 'Content-Type': 'application/json'}, 415),
                 ({**self.auth, 'Content-Type': 'text/yaml', 'Content-Length': str(MAX_CONFIG_BYTES+1)}, 413)]
        for headers, status in cases:
            with self.subTest(status=status), self.assertRaises(urllib.error.HTTPError) as caught:
                self.request('/api/preview', b'{}', headers)
            self.assertEqual(caught.exception.code, status)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('/api/config', headers={})
        self.assertEqual(caught.exception.code, 401)

    def test_watch_retains_valid_snapshot_recovers_and_does_not_change_history(self):
        server, monitor = self.server()
        report = ExecutionService(self.root, monitor=monitor).run(self.path, {}, mock={})
        run = copy.deepcopy(monitor.get(report['runId']))
        original = self.request('/api/config')
        self.path.write_text('bad: [')
        failed = self.request('/api/config')
        self.assertTrue(failed['stale']); self.assertTrue(failed['error'])
        self.assertEqual(original['data'], failed['data'])
        self.path.unlink()
        self.assertEqual(self.request('/api/config')['data'], original['data'])
        self.path.write_text(YAML.replace('"1"', '"2"'))
        changed = self.request('/api/config')
        self.assertFalse(changed['stale']); self.assertIsNone(changed['error'])
        self.assertNotEqual(original['data']['flowHash'], changed['data']['flowHash'])
        self.assertEqual(changed['data']['revision'], '2')
        self.assertEqual(run, monitor.get(report['runId']))
        # Browser-supplied paths cannot replace the startup-selected file.
        self.assertEqual(self.request('/api/config?path=/etc/passwd')['data'], changed['data'])
        self.request('/api/preview', YAML.encode())
        self.assertEqual(server.preview.snapshot()['data'], changed['data'])

    def test_missing_or_invalid_initial_file_and_size_limit(self):
        self.path.unlink()
        watch = ConfigurationPreview(self.path)
        self.assertIsNone(watch.snapshot()['data'])
        self.path.write_text('bad: [')
        self.assertIsNone(watch.snapshot()['data'])
        self.path.write_text(YAML)
        data = watch.snapshot()['data']
        self.path.write_text(' '*(MAX_CONFIG_BYTES+1))
        self.assertEqual(watch.snapshot()['data'], data)
        self.assertTrue(watch.snapshot()['stale'])
        with self.assertRaises(FlowError):
            preview_data(' '*(MAX_CONFIG_BYTES+1))
        self.assertFalse(ConfigurationPreview().snapshot()['configured'])

    def test_watch_option_requires_web_service(self):
        with redirect_stderr(io.StringIO()) as output:
            result = main(['serve', '--preview-flow', str(self.path)])
        self.assertEqual(result, 1)
        self.assertIn('--preview-flow requires --web-port', output.getvalue())
