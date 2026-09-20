"""Real stdio protocol tests; optional SDK, no provider credentials or network."""
import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HAS_MCP = importlib.util.find_spec("mcp") is not None


@unittest.skipUnless(HAS_MCP, 'Install jevflow[server] to test MCP')
class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_plugin_default_supports_two_simultaneous_servers(self):
        from contextlib import AsyncExitStack
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        config = json.loads((ROOT / '.mcp.json').read_text())['mcpServers']['jevflow']['args']
        port = config[config.index('--web-port') + 1]
        with tempfile.TemporaryDirectory() as directory:
            async with AsyncExitStack() as stack:
                sessions = []
                for i in range(2):
                    params = StdioServerParameters(command=sys.executable, args=[
                        str(ROOT / 'scripts/jevflow.py'), 'serve', '--web-port', port,
                        '--trace-dir', str(Path(directory) / str(i))])
                    read, write = await stack.enter_async_context(stdio_client(params))
                    session = await stack.enter_async_context(ClientSession(read, write))
                    await session.initialize()
                    sessions.append(session)
                for session in sessions:
                    response = await session.call_tool('run_flow', {
                        'flow_path': str(ROOT / 'examples/triage.yaml'),
                        'inputs': {'message': 'refund'},
                        'mock': json.loads((ROOT / 'examples/mock.json').read_text())})
                    self.assertFalse(response.isError)
                    self.assertEqual(response.structuredContent['result'], {'route': 'billing'})

    async def test_uncapped_calls_through_server_and_cli(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from test_unlimited_calls import chain
        from test_parallel import answer
        import yaml
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'uncapped.yaml';path.write_text(yaml.safe_dump(chain()))
            mock={f'n{i}':answer() for i in range(9)}
            mock_path=Path(temp)/'mock.json';mock_path.write_text(json.dumps(mock))
            params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'scripts/jevflow.py'),'serve','--trace-dir',temp])
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write) as session:
                    await session.initialize()
                    r=await session.call_tool('run_flow',{'flow_path':str(path),'inputs':{},'mock':mock})
                    self.assertFalse(r.isError);self.assertEqual(r.structuredContent['calls'],9)
                    self.assertTrue(r.structuredContent['result'])
            cli=subprocess.run([sys.executable,str(ROOT/'scripts/jevflow.py'),'run',str(path),'--input','-','--mock',str(mock_path),'--trace',str(Path(temp)/'cli.json')],input='{}',text=True,capture_output=True)
            self.assertEqual(cli.returncode,0,cli.stderr)
            self.assertEqual(json.loads(cli.stdout)['calls'],9)

    async def test_stdio_discovery_execution_errors_reload_and_cli_parity(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        import yaml
        with tempfile.TemporaryDirectory() as temp:
            params = StdioServerParameters(command=sys.executable,
                args=[str(ROOT/"scripts/jevflow.py"), "serve", "--trace-dir", temp, "--web-port", "0",
                      "--preview-flow", str(Path(temp)/"flow.yaml")])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertEqual({t.name for t in tools.tools}, {"validate_flow", "run_flow"})
                    path = Path(temp)/"flow.yaml"
                    path.write_text((ROOT/"examples/triage.yaml").read_text())
                    good = await session.call_tool("validate_flow", {"flow_path": str(path)})
                    self.assertFalse(good.isError)
                    mock = json.loads((ROOT/"examples/mock.json").read_text())
                    request = {"flow_path": str(path), "inputs": {"message": "refund"}, "mock": mock, "session_id": "match-test", "step_id": "2", "actor_id": "seat-0"}
                    results = await asyncio.gather(*(session.call_tool("run_flow", request) for _ in range(3)))
                    self.assertEqual(len({r.structuredContent["trace"] for r in results}), 3)
                    for result in results:
                        self.assertFalse(result.isError)
                        self.assertEqual(result.structuredContent["context"], {"sessionId":"match-test", "stepId":"2", "actorId":"seat-0"})
                        self.assertTrue(json.loads(Path(result.structuredContent["trace"]).read_text())["replay"]["events"])
                        self.assertEqual(result.structuredContent["result"], {"route": "billing"})
                    cli = subprocess.run([sys.executable, str(ROOT/"scripts/jevflow.py"), "run", str(path),
                        "--input", "-", "--mock", str(ROOT/"examples/mock.json"), "--trace", str(Path(temp)/"cli.json")],
                        input=json.dumps(request["inputs"]), capture_output=True, text=True)
                    report = json.loads(cli.stdout)
                    for key in ("result", "flowHash", "revision", "calls", "mode", "status"):
                        self.assertEqual(report[key], results[0].structuredContent[key])
                    expired = await session.call_tool("run_flow", {**request, "deadline_unix_ms": 1})
                    self.assertTrue(expired.isError)
                    self.assertEqual(expired.structuredContent["calls"], 0)
                    invalid = await session.call_tool("validate_flow", {"flow_path": str(Path(temp)/"missing")})
                    self.assertTrue(invalid.isError)
                    missing = await session.call_tool("run_flow", {**request, "mock": {}})
                    self.assertTrue(missing.isError)
                    updated = yaml.safe_load(path.read_text()); updated["revision"] = "2"
                    updated["nodes"]["billing"]["value"] = {"route": "changed"}
                    path.write_text(yaml.safe_dump(updated))
                    latest = await session.call_tool("run_flow", request)
                    self.assertEqual(latest.structuredContent["result"], {"route": "changed"})
                    self.assertNotEqual(latest.structuredContent["flowHash"], report["flowHash"])

    async def test_protocol_cancel_stops_worker_and_server_remains_usable(self):
        from mcp import ClientSession, StdioServerParameters, types
        from mcp.client.stdio import stdio_client
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp)/"entered"
            bootstrap = Path(temp)/"server.py"
            bootstrap.write_text('''import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from jevflow.execution import ExecutionService
from jevflow.server import create_server
from jevflow.client import MockClient
import json
class SlowClient(MockClient):
    def evaluate(self, *args, **kwargs):
        Path(sys.argv[3]).write_text(kwargs['node_id'])
        time.sleep(0.3)
        return super().evaluate(*args, **kwargs)
mock = json.loads((Path(sys.argv[1])/'examples/mock.json').read_text())
create_server(ExecutionService(sys.argv[2], client_factory=lambda: SlowClient(mock))).run(transport='stdio')
''')
            params = StdioServerParameters(command=sys.executable,
                args=[str(bootstrap), str(ROOT), temp, str(marker)])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    request_id = session._request_id
                    pending = asyncio.create_task(session.call_tool("run_flow", {
                        "flow_path": str(ROOT/"examples/triage.yaml"), "inputs": {"message": "refund"}}))
                    for _ in range(100):
                        if marker.exists(): break
                        await asyncio.sleep(0.02)
                    self.assertTrue(marker.exists())
                    await session.send_notification(types.ClientNotification(types.CancelledNotification(
                        method="notifications/cancelled", params=types.CancelledNotificationParams(requestId=request_id))))
                    response = await asyncio.wait_for(asyncio.gather(pending, return_exceptions=True), 3)
                    self.assertIsInstance(response[0], Exception)
                    for _ in range(100):
                        traces = list(Path(temp).glob('*.json'))
                        if traces: break
                        await asyncio.sleep(0.02)
                    self.assertEqual(len(traces), 1)
                    trace = json.loads(traces[0].read_text())
                    self.assertEqual(trace["status"], "cancelled")
                    self.assertNotIn("result", trace)
                    self.assertEqual(marker.read_text(), "classify")
                    good = await session.call_tool("validate_flow", {"flow_path": str(ROOT/"examples/triage.yaml")})
                    self.assertFalse(good.isError)


if __name__ == '__main__':
    unittest.main()
