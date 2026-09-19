"""Dependency-free flow diagrams; HTML is an offline, read-only inspector."""
import hashlib
import html
import json
from collections import defaultdict, deque

from .core import require, validate_flow

TEXT = {
    "zh-CN": {"evaluate": "Jev 判断", "filter": "候选筛选", "branch": "条件分支", "return": "返回结果",
              "default": "否则", "next": "继续", "inspect": "节点详情", "hint": "点击节点查看条件和提示词；绿色表示实际执行路径。拖动滚动条或调整缩放。",
              "title": "流程预览", "trace": "执行记录", "config": "配置", "none": "未附执行记录", "zoom": "缩放", "nodes": "个节点", "revision": "版本"},
    "en": {"evaluate": "Jev judgment", "filter": "Candidate filter", "branch": "Branch", "return": "Return",
           "default": "Otherwise", "next": "Next", "inspect": "Node details", "hint": "Click a node to inspect predicates and prompts. Green marks the executed path. Scroll or adjust zoom.",
           "title": "Flow preview", "trace": "Trace", "config": "Configuration", "none": "No trace attached", "zoom": "Zoom", "nodes": "nodes", "revision": "revision"},
}


def edges(flow, locale):
    for name, node in flow["nodes"].items():
        if node["type"] == "branch":
            for case in node["cases"]:
                left = case["left"].get("$ref") if isinstance(case["left"], dict) and "$ref" in case["left"] else json.dumps(case["left"], ensure_ascii=False)
                yield name, case["next"], f'{left} {case["op"]} {json.dumps(case["right"], ensure_ascii=False)}'
            yield name, node["default"], TEXT[locale]["default"]
        elif "next" in node:
            yield name, node["next"], TEXT[locale]["next"]


def render(flow, format="html", trace=None, locale=None):
    flow = validate_flow(flow)
    locale = locale or flow.get("locale", "zh-CN")
    require(locale in TEXT, "Unsupported diagram locale")
    require(format in ("html", "mermaid"), "format must be html or mermaid")
    labels = TEXT[locale]
    links = list(edges(flow, locale))
    aliases = {name: f'n{i}' for i, name in enumerate(flow["nodes"])}
    if format == "mermaid":
        rows = ["flowchart TD"]
        for name, node in flow["nodes"].items():
            title = html.escape(node.get("title", name), quote=True).replace("\n", " ")
            rows.append(f'  {aliases[name]}["{title} · {labels[node["type"]]}"]')
        for source, target, label in links:
            safe = html.escape(label, quote=True).replace("|", "&#124;").replace("\n", " ")
            rows.append(f'  {aliases[source]} -->|"{safe}"| {aliases[target]}')
        return "\n".join(rows) + "\n"

    visits = defaultdict(list)
    active_edges = set()
    if trace is not None:
        digest = hashlib.sha256(json.dumps(flow, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        require(trace.get("flowHash") == digest, "Trace belongs to a different flow snapshot")
        for step in trace.get("steps", []):
            visits[step["node"]].append(step)
            if "next" in step:
                active_edges.add((step["node"], step["next"]))
    depth = {flow["start"]: 0}
    queue = deque([flow["start"]])
    while queue:
        source = queue.popleft()
        for a, b, _ in links:
            if a == source and b not in depth:
                depth[b] = depth[a] + 1
                queue.append(b)
    levels = defaultdict(list)
    for name in flow["nodes"]:
        levels[depth[name]].append(name)
    columns = max(map(len, levels.values()))
    width, height = max(760, columns * 300 + 60), len(levels) * 150 + 60
    positions = {}
    for level, names in levels.items():
        for index, name in enumerate(names):
            positions[name] = ((width - len(names) * 300) / 2 + index * 300 + 25, level * 150 + 35)
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{labels["title"]}">',
           '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0L8 4L0 8Z" fill="context-stroke"/></marker></defs>']
    for a, b, label in links:
        x1, y1 = positions[a]; x2, y2 = positions[b]
        x1 += 125; x2 += 125; y1 += 74
        color = "#059669" if (a, b) in active_edges else "#94a3b8"
        middle = (y1 + y2) / 2
        svg.append(f'<path d="M{x1},{y1} C{x1},{middle} {x2},{middle} {x2},{y2}" fill="none" stroke="{color}" stroke-width="2" marker-end="url(#arrow)"><title>{html.escape(label)}</title></path>')
        short = label if len(label) < 34 else label[:31] + "…"
        svg.append(f'<text x="{(x1+x2)/2+7}" y="{middle-5}" class="edge-label">{html.escape(short)}</text>')
    colors = {"evaluate": "#e0e7ff", "filter": "#fef3c7", "branch": "#e0f2fe", "return": "#f1f5f9"}
    for name, node in flow["nodes"].items():
        x, y = positions[name]
        title = node.get("title", name)
        suffix = f' · {len(visits[name])}' if name in visits else ""
        stroke = "#059669" if name in visits else "#cbd5e1"
        svg.append(f'<g tabindex="0" role="button" aria-label="{html.escape(title, quote=True)}" data-node="{aliases[name]}" class="node" transform="translate({x},{y})"><rect width="250" height="74" rx="12" fill="{colors[node["type"]]}" stroke="{stroke}" stroke-width="2"/><text x="14" y="28" class="node-title">{html.escape(title[:21])}</text><text x="14" y="52" class="node-type">{html.escape(labels[node["type"]] + suffix)}</text><title>{html.escape(name)}</title></g>')
    svg.append("</svg>")
    # Embed only node/step details; flow inputs are not copied wholesale into the preview.
    data = {aliases[name]: {"id": name, "config": node, "trace": visits.get(name, [])} for name, node in flow["nodes"].items()}
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    title = html.escape(flow.get("title", flow["name"]))
    return f'''<!doctype html><html lang="{locale}"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>*{{box-sizing:border-box}}body{{margin:0;color:#172033;background:#f8fafc;font:14px system-ui,sans-serif}}header{{padding:20px 28px;background:white;border-bottom:1px solid #ddd}}h1{{margin:0 0 6px;font-size:23px}}p{{color:#64748b;margin:6px 0}}main{{display:grid;grid-template-columns:minmax(0,1fr) 360px;height:calc(100vh - 140px)}}#canvas{{overflow:auto;padding:12px}}aside{{padding:20px;background:white;border-left:1px solid #ddd;overflow:auto}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.65 ui-monospace,monospace}}.node{{cursor:pointer}}.node:hover rect,.node:focus rect{{stroke:#4f46e5;stroke-width:3}}.node-title{{font-size:15px;font-weight:600;fill:#172033}}.node-type{{font-size:12px;fill:#475569}}.edge-label{{font-size:10px;fill:#475569;paint-order:stroke;stroke:#f8fafc;stroke-width:4px}}h2{{font-size:16px}}button{{cursor:pointer}}@media(max-width:850px){{main{{display:block;height:auto}}#canvas{{height:65vh}}aside{{border-top:1px solid #ddd}}}}</style>
<header><h1>{title}</h1><p>{labels['hint']}</p><label>{labels['zoom']} <input id="zoom" aria-label="{labels['zoom']}" type="range" min="40" max="140" value="85"></label> <span>{html.escape(flow['name'])} · {len(flow['nodes'])} {labels['nodes']} · {labels['revision']} {html.escape(flow.get('revision','1'))}</span></header>
<main><div id="canvas">{''.join(svg)}</div><aside><h2>{labels['inspect']}</h2><p id="selected"></p><h2>{labels['config']}</h2><pre id="config"></pre><h2>{labels['trace']}</h2><pre id="trace"></pre></aside></main>
<script type="application/json" id="data">{payload}</script><script>
const data=JSON.parse(document.getElementById('data').textContent);
function select(id){{const n=data[id];document.getElementById('selected').textContent=n.id;document.getElementById('config').textContent=JSON.stringify(n.config,null,2);document.getElementById('trace').textContent=n.trace.length?JSON.stringify(n.trace,null,2):{json.dumps(labels['none'],ensure_ascii=False)};}}
document.querySelectorAll('[data-node]').forEach(n=>{{n.addEventListener('click',()=>select(n.dataset.node));n.addEventListener('keydown',e=>{{if(e.key==='Enter'||e.key===' '){{e.preventDefault();select(n.dataset.node);}}}});}});
const svg=document.querySelector('svg'),zoom=document.getElementById('zoom');function resize(){{svg.style.width=({width}*zoom.value/100)+'px';svg.style.height=({height}*zoom.value/100)+'px';}}zoom.addEventListener('input',resize);resize();const canvas=document.getElementById('canvas');canvas.scrollLeft=Math.max(0,({width}*zoom.value/100-canvas.clientWidth)/2);select({json.dumps(aliases[flow['start']])});
</script></html>'''
