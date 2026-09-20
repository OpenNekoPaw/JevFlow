"""Read-only graph contract shared by live monitoring and offline exports.

Python owns flow topology and immutable revision identity. Browser grouping and
layout are presentation-only; they never add executable nodes or change routing.
"""
import json


from .schema import snapshot_hash


TEXT = {
    "zh-CN": {"select": "分组选择", "parallel": "并行分析", "join": "全部完成后汇合", "evaluate": "Jev 判断", "filter": "候选筛选", "branch": "条件分支", "return": "返回结果",
              "default": "否则", "next": "继续", "inspect": "节点详情", "hint": "点击节点查看条件和提示词；绿色表示实际执行路径。拖动滚动条或调整缩放。",
              "title": "流程预览", "trace": "执行记录", "config": "配置", "none": "未附执行记录", "zoom": "缩放", "nodes": "个节点", "revision": "版本"},
    "en": {"select": "Batched selection", "parallel": "Parallel", "join": "Join all", "evaluate": "Jev judgment", "filter": "Candidate filter", "branch": "Branch", "return": "Return",
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
        elif node["type"] == "parallel":
            for branch in node["branches"]:
                yield name, name + "." + branch, TEXT[locale]["parallel"]
                yield name + "." + branch, name + ".@join", TEXT[locale]["join"]
            yield name + ".@join", node["next"], TEXT[locale]["next"]
        elif "next" in node:
            yield name, node["next"], TEXT[locale]["next"]


def expand_nodes(flow, locale="zh-CN"):
    """Shared topology for offline previews and the live monitor."""
    labels = TEXT[locale]
    diagram_nodes = dict(flow["nodes"])
    for name, node in flow["nodes"].items():
        if node["type"] == "parallel":
            for branch, definition in node["branches"].items():
                diagram_nodes[name + "." + branch] = {"type": "evaluate", **definition}
            diagram_nodes[name + ".@join"] = {"type": "join", "title": labels["join"]}
    return diagram_nodes


def graph_data(flow, locale=None):
    locale = locale or flow.get("locale", "zh-CN")
    return {"schemaVersion": 1, "flowHash": snapshot_hash(flow),
            "revision": flow.get("revision", "1"), "locale": locale,
            "nodes": expand_nodes(flow, locale), "edges": list(edges(flow, locale)), "start": flow["start"]}
