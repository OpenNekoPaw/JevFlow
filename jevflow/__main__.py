"""JSON-output CLI usable by any agent with a shell/tool interface."""

import argparse
import json
import sys
import uuid
from pathlib import Path

from .client import JevClient, MockClient
from .core import FlowError, fields, load_flow, require, run_flow


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
    validate = commands.add_parser("validate", help="Validate YAML without model calls")
    validate.add_argument("flow")
    view = commands.add_parser("visualize", help="Export an offline HTML inspector or Mermaid graph")
    view.add_argument("flow")
    view.add_argument("--output", required=True)
    view.add_argument("--format", choices=["html", "mermaid"], default="html")
    view.add_argument("--locale", choices=["zh-CN", "en"])
    view.add_argument("--trace", help="Optional trace from the same flow snapshot")
    run = commands.add_parser("run", help="Execute a flow; live unless --mock is supplied")
    run.add_argument("flow")
    run.add_argument("--input", required=True, help="Input JSON file, or - for stdin")
    run.add_argument("--mock", help="Node answer JSON; disables all model calls")
    run.add_argument("--trace", help="Trace JSON path; defaults to .tmp/jevflow/<id>.json")
    run.add_argument("--model", default="jev-latest")
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
        flow = load_flow(args.flow)
        if args.command == "validate":
            emit({"valid": True, "name": flow["name"], "nodes": len(flow["nodes"])})
            return 0
        if args.command == "visualize":
            from .visualize import render
            output = Path(args.output)
            rendered = render(flow, args.format, read_json(args.trace) if args.trace else None, args.locale)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered, encoding="utf-8")
            emit({"status": "completed", "output": str(output.resolve()), "format": args.format})
            return 0
        if args.command == "run":
            inputs = json.load(sys.stdin) if args.input == "-" else read_json(args.input)
            client = MockClient(read_json(args.mock)) if args.mock else JevClient(model=args.model)
            trace = {}
            trace_path = Path(args.trace) if args.trace else new_trace_path()
            try:
                result = run_flow(flow, inputs, client, trace)
            except FlowError as exc:
                emit({"status": "failed", "error": str(exc), "trace": str(trace_path.resolve())})
                return 1
            finally:
                if trace:
                    write_json(trace_path, trace)
            emit({"status": "completed", "mode": client.mode, "result": result,
                  "calls": trace["calls"], "trace": str(trace_path.resolve())})
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
        emit({"status": "failed", "error": str(exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
