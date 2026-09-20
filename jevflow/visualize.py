"""Offline diagrams using the same bundled viewer as live monitoring."""
import html
import json
from pathlib import Path

from .schema import require, snapshot_hash, validate_flow, validate_trace_snapshot
from .topology import TEXT, graph_data
from .records import trace_run


def render(flow, format="html", trace=None, locale=None, direction="LR"):
    flow = validate_flow(flow)
    locale = locale or flow.get("locale", "zh-CN")
    require(locale in TEXT, "Unsupported diagram locale")
    require(format in ("html", "mermaid"), "format must be html or mermaid")
    require(direction in ("LR", "TD"), "direction must be LR or TD")
    labels = TEXT[locale]
    graph = graph_data(flow, locale)
    digest = snapshot_hash(flow)
    if trace is not None:
        validate_trace_snapshot(trace, flow)
    if format == "mermaid":
        aliases = {name: f'n{i}' for i, name in enumerate(graph["nodes"])}
        rows = [f"flowchart {direction}"]
        for name, node in graph["nodes"].items():
            title = html.escape(node.get("title", name), quote=True).replace("\n", " ")
            rows.append(f'  {aliases[name]}["{title} · {labels[node["type"]]}"]')
        for source, target, label in graph["edges"]:
            if label == labels["next"]:
                rows.append(f'  {aliases[source]} --> {aliases[target]}')
            else:
                safe = html.escape(label, quote=True).replace("|", "&#124;").replace("\n", " ")
                rows.append(f'  {aliases[source]} -->|"{safe}"| {aliases[target]}')
        return "\n".join(rows) + "\n"
    # Keep only execution steps; do not copy top-level input/evidence wholesale.
    data = {"graph": graph, "flowHash": digest,
            "trace": {"steps": trace.get("steps", [])} if trace is not None else None,
            "finished": bool(trace and trace.get("status") in ("completed", "failed", "cancelled")),
            "status": trace.get("status") if trace else None,
            "replay": trace.get("replay") if trace else None}
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    assets = Path(__file__).with_name("web")
    css = (assets / "viewer.css").read_text(encoding="utf-8")
    script = (assets / "dagre.min.js").read_text(encoding="utf-8") + "\n" + (assets / "replay.js").read_text(encoding="utf-8") + "\n" + (assets / "viewer.js").read_text(encoding="utf-8")
    title = html.escape(flow.get("title", flow["name"]))
    mode = labels['trace'] if trace else labels['title']
    revision = html.escape(flow.get('revision', '1'))
    return f'''<!doctype html><html lang="{locale}"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>body{{margin:0;padding:24px;background:#f5f7fa;color:#25334b;font:14px system-ui}}h1{{font-size:22px;margin:0 0 8px}}header p{{font:12px ui-monospace,monospace;color:#64748b;overflow-wrap:anywhere}}{css}</style>
<header><h1>{title}</h1><p>{mode} · {labels['revision']} {revision} · {digest}</p></header><main id="viewer"></main>
<script type="application/json" id="data">{payload}</script><script>{script}</script>
<script>new JevFlowViewer.Viewer(document.getElementById('viewer'),{{locale:{json.dumps(locale)},direction:{json.dumps(direction)}}}).setData(JSON.parse(document.getElementById('data').textContent));</script></html>'''


def render_replay(traces):
    """Export a portable multi-run archive using each trace's own flow snapshot."""
    require(bool(traces), "Replay needs at least one trace")
    runs, ids = [], set()
    for i, trace in enumerate(traces):
        run = trace_run(trace, fallback_run_id="archive-"+str(i+1), fallback_received_ms=i, include_input=False)
        require(run["runId"] not in ids, "Replay run IDs must be unique nonempty strings")
        ids.add(run["runId"])
        flow = run["trace"].get("flow")
        run["graph"] = graph_data(flow, "zh-CN") if flow else None
        runs.append(run)
    assets = Path(__file__).with_name("web")
    page = (assets / "monitor.html").read_text(encoding="utf-8").replace("JevFlow · 实时监控", "JevFlow · 执行回放")
    for name in ("monitor.css", "viewer.css"):
        page = page.replace(f'<link rel="stylesheet" href="/{name}" />', '<style>'+ (assets/name).read_text(encoding="utf-8")+'</style>')
    for name in ("dagre.min.js", "replay.js", "viewer.js", "preview.js", "monitor.js"):
        page = page.replace(f'<script src="/{name}" defer></script>', '')
    payload = json.dumps(runs, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    scripts = '<script type="application/json" id="archive">'+payload+'</script><script>globalThis.JEVFLOW_ARCHIVE=JSON.parse(document.getElementById("archive").textContent);</script>'
    for name in ("dagre.min.js", "replay.js", "viewer.js", "monitor.js"):
        scripts += '<script>'+(assets/name).read_text(encoding="utf-8")+'</script>'
    page = page.replace("运行监控", "执行回放")
    page = page.replace('实时事件来自同一执行器。结束后的记录固定保存；重新连接自动恢复当前状态。', '离线只读回放 · 每次执行使用自己的版本快照 · 按记录浏览，不调用模型。')
    return page.replace('</body>', scripts+'</body>')
