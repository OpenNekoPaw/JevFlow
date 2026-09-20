"""Optional MCP stdio transport. Execution remains in the shared service."""
import asyncio
import time
from functools import partial
from threading import Event
from typing import Any, Dict, Literal, Optional

from .execution import ExecutionService, validate_path


def create_server(service=None):
    from mcp.server.fastmcp import FastMCP
    from mcp.types import CallToolResult, TextContent, ToolAnnotations
    import anyio
    import json

    service = service or ExecutionService()
    server = FastMCP("jevflow", instructions="校验 YAML，或一次执行完整 Flow。策略由宿主 Agent 编写，外部动作由宿主执行。",
                     log_level="WARNING")

    def reply(value, failed=False):
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(value, ensure_ascii=False))],
                              structuredContent=value, isError=failed)

    @server.tool(name="validate_flow", description="校验本地 YAML 文件，不调用模型。flow_path 使用绝对路径。",
                 annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
    async def validate_flow(flow_path: str) -> CallToolResult:
        try:
            value = await anyio.to_thread.run_sync(validate_path, flow_path)
            return reply(value)
        except (OSError, ValueError) as exc:
            return reply({"valid": False, "error": str(exc)}, True)

    @server.tool(name="run_flow", description="按 YAML 执行完整决策，返回结果与本地 trace 路径。可能调用付费模型；mock 为离线答案映射。",
                 annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True))
    async def run_flow(flow_path: str, inputs: Any, provider: Literal["native", "gateway"] = "native",
                       model: str = "jev-latest", mock: Optional[Dict[str, Any]] = None,
                       deadline_unix_ms: Optional[float] = None, attempt_timeout: float = 5,
                       max_retries: int = 0, session_id: Optional[str] = None,
                       step_id: Optional[str] = None, actor_id: Optional[str] = None,
                       parent_run_id: Optional[str] = None) -> CallToolResult:
        event = Event()
        work = partial(service.run, flow_path, inputs, provider=provider, model=model, mock=mock,
                       deadline_unix_ms=deadline_unix_ms, attempt_timeout=attempt_timeout,
                       max_retries=max_retries, cancel_event=event, received_at=time.monotonic(),
                       session_id=session_id, step_id=step_id, actor_id=actor_id, parent_run_id=parent_run_id)
        future = asyncio.get_running_loop().run_in_executor(None, work)
        try:
            value = await asyncio.shield(future)
            return reply(value, value["status"] != "completed")
        except asyncio.CancelledError:
            event.set()
            # Keep cancellation tied to the worker. A synchronous network call may
            # finish at its attempt cap; no new nodes/retries or late success escape.
            with anyio.CancelScope(shield=True):
                await asyncio.shield(future)
            raise

    return server


def serve(trace_dir=".tmp/jevflow", max_runs=5, max_concurrency=5, web_port=None, monitor_history=100, preview_flow=None, web_auth=False):
    if preview_flow is not None and web_port is None:
        raise ValueError("--preview-flow requires --web-port")
    if web_auth and web_port is None:
        raise ValueError("--web-auth requires --web-port")
    monitor, web = None, None
    if web_port is not None:
        from .monitor import RunMonitor, MonitorServer
        monitor = RunMonitor(max_history=monitor_history)
        monitor.restore(trace_dir)
        web = MonitorServer(monitor, web_port, preview_flow=preview_flow, require_token=web_auth).start()
    try:
        if web is not None:
            import sys
            print("JevFlow monitor: " + web.url, file=sys.stderr, flush=True)
        service = ExecutionService(trace_dir, max_runs, max_concurrency, monitor=monitor)
        create_server(service).run(transport="stdio")
    finally:
        if web is not None:
            web.close()
