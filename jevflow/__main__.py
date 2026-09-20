"""JSON-output CLI usable by any agent with a shell/tool interface."""

import argparse
import json
import sys
import uuid
from pathlib import Path

from .client import JevClient, MockClient
from .control import FlowError
from .core import run_flow
from .schema import fields, load_flow, require


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def emit(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False))


def new_trace_path():
    return Path(".tmp/jevflow") / (uuid.uuid4().hex + ".json")


def parser():
    cli = argparse.ArgumentParser(prog="jevflow", description="Validate, run and test YAML decision flows")
    commands = cli.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Run a persistent local MCP server over stdio (install jevflow[server])")
    serve.add_argument("--trace-dir", default=".tmp/jevflow")
    serve.add_argument("--max-runs", type=int, default=5)
    serve.add_argument("--max-concurrency", type=int, default=5, help="Server-wide provider call limit")
    serve.add_argument("--web-port", type=int, help="Enable read-only loopback Web monitor (0 chooses a free port)")
    serve.add_argument("--web-auth", action="store_true", help="Require a generated Web token (default: no token on loopback)")
    serve.add_argument("--monitor-history", type=int, default=100, help="Completed runs retained in monitor memory")
    serve.add_argument("--preview-flow", help="Watch a YAML configuration for Web preview; requires --web-port")
    replay = commands.add_parser("replay", help="Export one or more saved traces as an offline playback archive")
    replay.add_argument("traces", nargs="+")
    replay.add_argument("--output", required=True)
    stats = commands.add_parser("stats", help="Summarize local execution traces without model calls")
    stats.add_argument("traces", nargs="+", help="Flow trace JSON files (shell globs allowed)")
    stats.add_argument("--output", help="Optional aggregate JSON output")
    validate = commands.add_parser("validate", help="Validate YAML without model calls")
    validate.add_argument("flow")
    view = commands.add_parser("visualize", help="Export an offline HTML inspector or Mermaid graph")
    view.add_argument("flow")
    view.add_argument("--output", required=True)
    view.add_argument("--format", choices=["html", "mermaid"], default="html")
    view.add_argument("--locale", choices=["zh-CN", "en"])
    view.add_argument("--direction", choices=["LR", "TD"], default="LR")
    view.add_argument("--trace", help="Optional trace from the same flow snapshot")
    run = commands.add_parser("run", help="Execute a flow; live unless --mock is supplied")
    run.add_argument("flow")
    run.add_argument("--input", required=True, help="Input JSON file, or - for stdin")
    run.add_argument("--mock", help="Node answer JSON; disables all model calls")
    for field in ("session-id", "step-id", "actor-id", "parent-run-id"):
        run.add_argument("--"+field, help="Optional execution correlation metadata")
    run.add_argument("--trace", help="Trace JSON path; defaults to .tmp/jevflow/<id>.json")
    run.add_argument("--model", default="jev-latest")
    run.add_argument("--provider", choices=["native", "gateway"], default="native")
    run.add_argument("--deadline-unix-ms", type=float)
    run.add_argument("--attempt-timeout", type=float, default=5)
    run.add_argument("--max-retries", type=int, default=0, help="Gateway retries; native requires 0")
    run.add_argument("--max-concurrency", type=int, default=5, help="Provider call limit; YAML may impose a lower limit")
    test = commands.add_parser("test", help="Run labeled cases; mock by default, --live evaluates Jev")
    test.add_argument("flow")
    test.add_argument("cases", help="JSON array of {name, input, expected, mock}")
    test.add_argument("--live", action="store_true")
    test.add_argument("--model", default="jev-latest")
    test.add_argument("--trace-dir", default=None)
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "serve":
            try:
                from .server import serve
                serve(args.trace_dir, args.max_runs, args.max_concurrency, args.web_port, args.monitor_history, args.preview_flow, args.web_auth)
            except ImportError:
                print('MCP mode requires Python 3.10+ and pip install "jevflow[server]"', file=sys.stderr)
                return 1
            return 0
        if args.command == "run":
            from .execution import ExecutionService
            inputs = json.load(sys.stdin) if args.input == "-" else read_json(args.input)
            report = ExecutionService(max_concurrency=args.max_concurrency).run(args.flow, inputs, provider=args.provider, model=args.model,
                mock=read_json(args.mock) if args.mock else None, trace_path=args.trace,
                deadline_unix_ms=args.deadline_unix_ms, attempt_timeout=args.attempt_timeout,
                max_retries=args.max_retries, session_id=args.session_id, step_id=args.step_id,
                actor_id=args.actor_id, parent_run_id=args.parent_run_id)
            emit(report)
            return 0 if report["status"] == "completed" else 1
        if args.command == "replay":
            from .visualize import render_replay
            output = Path(args.output)
            content = render_replay([read_json(path) for path in dict.fromkeys(args.traces)])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(content, encoding="utf-8")
            emit({"status": "completed", "output": str(output.resolve()), "runs": len(set(args.traces))})
            return 0
        if args.command == "stats":
            from .metrics import summarize_traces
            report = summarize_traces(read_json(path) for path in dict.fromkeys(args.traces))
            if args.output:
                write_json(args.output, report)
            emit(report)
            return 0
        flow = load_flow(args.flow)
        if args.command == "validate":
            emit({"valid": True, "name": flow["name"], "nodes": len(flow["nodes"])})
            return 0
        if args.command == "visualize":
            from .visualize import render
            output = Path(args.output)
            rendered = render(flow, args.format, read_json(args.trace) if args.trace else None, args.locale, args.direction)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered, encoding="utf-8")
            emit({"status": "completed", "output": str(output.resolve()), "format": args.format})
            return 0
        cases = read_json(args.cases)
        require(isinstance(cases, list) and bool(cases), "cases must be a nonempty array")
        for case in cases:
            fields(case, {"name", "input", "expected"}, {"mock"}, "test case")
            if not args.live:
                require(isinstance(case.get("mock"), dict), "Offline test case needs a mock mapping")
        directory = Path(args.trace_dir) if args.trace_dir else new_trace_path().with_suffix("")
        rows = []
        for index, case in enumerate(cases):
            client = JevClient(model=args.model) if args.live else MockClient(case["mock"])
            trace, row = {}, {"name": case["name"], "expected": case["expected"]}
            try:
                row["actual"] = run_flow(flow, case["input"], client, trace)
                row["passed"] = row["actual"] == case["expected"]
            except FlowError as exc:
                row.update(passed=False, error=str(exc))
            if trace:
                write_json(directory / (str(index) + ".json"), trace)
            rows.append(row)
            if "error" in row and args.live:
                break
        report = {"mode": "live" if args.live else "mock", "passed": sum(r["passed"] for r in rows),
                  "total": len(cases), "completed": len(rows), "cases": rows,
                  "traceDir": str(directory.resolve())}
        write_json(directory / "report.json", report)
        emit(report)
        return 0 if report["passed"] == report["total"] else 1
    except (FlowError, OSError, ValueError) as exc:
        if args.command == "serve":
            print(str(exc), file=sys.stderr)
        else:
            emit({"status": "failed", "error": str(exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
