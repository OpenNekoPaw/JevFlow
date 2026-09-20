"""Build from an sdist and exercise the installed bridge without provider access."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec('build') and shutil.which('node'),
                     'Install build, setuptools>=61 and wheel, plus Node.js, for packaging tests')
class PackagingTests(unittest.TestCase):
    def test_sdist_wheel_gateway_and_viewer_work_outside_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, installed, sdk = root / 'source', root / 'installed', root / 'sdk'
            source.mkdir()
            for name in ('pyproject.toml', 'README.md'):
                shutil.copy2(ROOT / name, source / name)
            shutil.copytree(ROOT / 'jevflow', source / 'jevflow',
                            ignore=shutil.ignore_patterns('__pycache__'))
            built = subprocess.run([sys.executable, '-m', 'build', '--no-isolation', str(source)],
                                   capture_output=True, text=True, timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            self.assertTrue(list((source / 'dist').glob('*.tar.gz')))
            with zipfile.ZipFile(next((source / 'dist').glob('*.whl'))) as wheel:
                self.assertIn('jevflow/adapters/evaluate.mjs', wheel.namelist())
                wheel.extractall(installed)
            # A local SDK stub tests the actual packaged Node bridge and Python
            # conversion, never subprocess mocks or an external provider.
            ai = sdk / 'node_modules' / 'ai'
            ai.mkdir(parents=True)
            (sdk / 'package.json').write_text('{}')
            (ai / 'package.json').write_text('{"name":"ai","main":"index.cjs"}')
            (ai / 'index.cjs').write_text('''exports.experimental_evaluate = async ({questions, state}) => {
  if (questions.ready.type !== "boolean" || state !== "package smoke") throw Error("bad request");
  return {answers: {ready: {type: "boolean", probability: 0.8}}};
};
''')
            env = {**os.environ, 'PYTHONPATH': str(installed),
                   'JEVFLOW_GATEWAY_HOME': str(sdk), 'AI_GATEWAY_API_KEY': 'offline-test-placeholder'}
            script = '''import json
from pathlib import Path
import jevflow
from jevflow import Flow, GatewayClient
from jevflow.visualize import render
assert Path(jevflow.__file__).is_relative_to(Path(__import__('sys').argv[1]))
flow = {"version": 1, "name": "package", "mode": "single", "state": "package smoke",
        "questions": {"ready": {"type": "noul", "instructions": "Ready?"}}}
trace = {}
answer = Flow(flow).run({}, GatewayClient(), trace)
assert answer["ready"]["noul"] == 0.8, answer
assert trace["steps"][0]["response"]["transportAttempts"][0]["ok"]
assert "JevFlowViewer" in render(flow, trace=trace)
print(json.dumps(answer))
'''
            result = subprocess.run([sys.executable, '-c', script, str(installed)], cwd=root,
                                    env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)['ready']['noul'], 0.8)
