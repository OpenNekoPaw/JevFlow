"""Flow definitions, typed validation and stable snapshot identity; no execution."""
import hashlib
import json
import math
import re
from pathlib import Path

import yaml

from .control import FlowError


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate YAML keys rather than silently replacing a node."""


def _mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str):
            raise FlowError("YAML mapping keys must be strings; quote true/false and numbers")
        if key in result:
            raise FlowError("Duplicate YAML key: " + key)
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "contains", "not_contains"}


def require(condition, message):
    if not condition:
        raise FlowError(message)


def fields(value, required, optional, where):
    require(isinstance(value, dict), where + " must be a mapping")
    require(required <= value.keys(), where + " missing fields: " + str(sorted(required - value.keys())))
    require(value.keys() <= required | optional, where + " has unknown fields: " + str(sorted(value.keys() - required - optional)))


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def json_value(value):
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise FlowError("Values must be finite JSON data; quote YAML dates and avoid recursive aliases") from exc


def _parse_yaml(source):
    try:
        flow = yaml.load(source, Loader=UniqueLoader)
    except yaml.YAMLError as exc:
        raise FlowError("Invalid YAML: " + str(exc)) from exc
    return flow


def _read_flow(path):
    """Read untrusted YAML; callers must validate before storing or executing it."""
    return _parse_yaml(Path(path).read_text(encoding="utf-8"))


def parse_flow(source):
    """Validate YAML content using the same rules as a file on disk."""
    return validate_flow(_parse_yaml(source))


def load_flow(path):
    return validate_flow(_read_flow(path))


def validate_questions(questions, dynamic=False, singleton=False):
    require(isinstance(questions, dict) and bool(questions), "questions must be a nonempty mapping")
    for name, question in questions.items():
        require(isinstance(name, str) and bool(NAME.fullmatch(name)), "Invalid question ID")
        fields(question, {"type", "instructions"}, {"criteria"}, "question " + name)
        kind = question["type"]
        require(kind in ("choice", "noul", "score"), "Question type must be choice, noul, or score")
        require(isinstance(question["instructions"], str) and bool(question["instructions"].strip()), "instructions must be nonempty text")
        criteria = question.get("criteria")
        if kind == "choice":
            if dynamic and isinstance(criteria, dict) and "$ref" in criteria:
                list(references(criteria))
                continue
            require(isinstance(criteria, dict) and (1 if singleton else 2) <= len(criteria) <= 255,
                    "choice needs " + ("1" if singleton else "2") + "..255 options")
            require(all(isinstance(k, str) and k and isinstance(v, str) and v.strip() for k, v in criteria.items()), "choice options need string IDs and descriptions")
        elif kind == "score":
            require(isinstance(criteria, list) and len(criteria) >= 2, "score needs at least two ordered levels")
            require(all(isinstance(v, str) and v.strip() for v in criteria), "score levels must be text")
        elif criteria is not None:
            fields(criteria, {"true", "false"}, set(), "noul criteria")
            require(all(isinstance(v, str) and v.strip() for v in criteria.values()), "noul criteria must be text")


def references(value):
    if isinstance(value, dict):
        if "$ref" in value:
            fields(value, {"$ref"}, set(), "reference")
            require(isinstance(value["$ref"], str), "$ref must be a dotted path")
            yield value["$ref"]
        else:
            for item in value.values():
                yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


def validate_flow(flow):
    json_value(flow)
    require(isinstance(flow, dict), "flow must be a mapping")
    mode = flow.get("mode", "flow")
    require(mode in ("single", "flow"), "mode must be single or flow")
    if mode == "single":
        fields(flow, {"version", "name", "mode", "state", "questions"}, {"revision", "limits", "result", "title", "locale"}, "single")
        validate_questions(flow["questions"], dynamic=True, singleton=True)
        flow = {**{k: flow[k] for k in ("version", "name", "revision", "limits", "title", "locale") if k in flow},
                "start": "evaluate", "nodes": {
                    "evaluate": {"type": "evaluate", "state": flow["state"],
                                 "questions": flow["questions"], "next": "result"},
                    "result": {"type": "return", "value": flow.get("result", {
                        key: {"$ref": "nodes.evaluate." + key} for key in flow["questions"]})}}}
    elif "mode" in flow:
        flow = {k: v for k, v in flow.items() if k != "mode"}
    fields(flow, {"version", "name", "start", "nodes"}, {"revision", "limits", "title", "locale"}, "flow")
    require(flow.get("locale", "en") in ("en", "zh-CN"), "locale must be en or zh-CN")
    require(isinstance(flow.get("title", ""), str), "title must be text")
    require(type(flow["version"]) is int and flow["version"] == 1, "version must be 1")
    require(isinstance(flow["name"], str) and bool(flow["name"].strip()), "name must be text")
    require(isinstance(flow.get("revision", "1"), str), "revision must be a quoted string")
    limits = flow.get("limits", {})
    fields(limits, set(), {"max_steps", "max_calls", "timeout_seconds", "max_concurrency"}, "limits")
    for key, value in limits.items():
        if key == "max_calls" and value is None:
            continue
        require(type(value) is int and value > 0, "limits must be positive integers: " + key)
    nodes = flow["nodes"]
    require(isinstance(nodes, dict) and bool(nodes), "nodes must be nonempty")
    require(isinstance(flow["start"], str) and flow["start"] in nodes, "start must name an existing node")
    edges = {}
    node_refs = {}
    for name, node in nodes.items():
        require(isinstance(name, str) and bool(NAME.fullmatch(name)), "Invalid node ID")
        require(isinstance(node, dict), "Node must be a mapping: " + name)
        require(isinstance(node.get("title", ""), str), "node title must be text")
        kind = node.get("type")
        if kind == "evaluate":
            fields(node, {"type", "state", "questions", "next"}, {"title"}, name)
            validate_questions(node["questions"], dynamic=True, singleton=True)
            edges[name] = [node["next"]]
            ref_values = [node["state"], node["questions"]]
        elif kind == "select":
            fields(node, {"type", "state", "criteria", "instructions", "next"}, {"title", "batch_size", "fallback_criteria"}, name)
            require(isinstance(node["instructions"], str) and bool(node["instructions"].strip()), "select needs instructions")
            size = node.get("batch_size", 255)
            require(type(size) is int and 2 <= size <= 255, "select batch_size must be 2..255")
            require(isinstance(node["criteria"], dict), "select criteria must be a mapping or reference")
            if "fallback_criteria" in node:
                require(isinstance(node["fallback_criteria"], list) and bool(node["fallback_criteria"])
                        and all(isinstance(value, dict) for value in node["fallback_criteria"]),
                        "select fallback_criteria must be a nonempty list of mappings or references")
            edges[name] = [node["next"]]
            ref_values = [node["state"], node["criteria"], node.get("fallback_criteria", [])]
        elif kind == "parallel":
            fields(node, {"type", "branches", "next"}, {"title"}, name)
            branches = node["branches"]
            require(isinstance(branches, dict) and len(branches) >= 2, "parallel needs at least two branches")
            ref_values = []
            for branch, definition in branches.items():
                require(isinstance(branch, str) and bool(NAME.fullmatch(branch)), "Invalid parallel branch ID")
                fields(definition, {"state", "questions"}, {"title"}, name + "." + branch)
                require(isinstance(definition.get("title", ""), str), "branch title must be text")
                validate_questions(definition["questions"], dynamic=True, singleton=True)
                ref_values.extend([definition["state"], definition["questions"]])
            edges[name] = [node["next"]]
        elif kind == "filter":
            fields(node, {"type", "items", "where", "next"}, {"title", "order_by", "limit"}, name)
            if "order_by" in node:
                require(isinstance(node["order_by"], list) and bool(node["order_by"]), "filter order_by must be nonempty")
                for ordering in node["order_by"]:
                    fields(ordering, {"field", "direction"}, set(), "filter ordering")
                    require(isinstance(ordering["field"], str) and all(ordering["field"].split(".")), "Invalid ordering field")
                    require(ordering["direction"] in ("asc", "desc"), "ordering direction must be asc or desc")
            if "limit" in node:
                require("order_by" in node, "filter limit requires explicit order_by")
                require(type(node["limit"]) is int and node["limit"] > 0, "filter limit must be a positive integer")
            require(isinstance(node["where"], list) and bool(node["where"]), "filter needs predicates")
            for predicate in node["where"]:
                fields(predicate, {"field", "op", "value"}, set(), "filter predicate")
                require(isinstance(predicate["field"], str) and all(predicate["field"].split(".")), "Invalid filter field")
                require(isinstance(predicate["op"], str) and predicate["op"] in OPS, "Unknown comparison operator")
            edges[name] = [node["next"]]
            ref_values = [node["items"], [p["value"] for p in node["where"]]]
        elif kind == "branch":
            fields(node, {"type", "cases", "default"}, {"title"}, name)
            require(isinstance(node["cases"], list) and bool(node["cases"]), "cases must be a nonempty list")
            edges[name] = [node["default"]]
            ref_values = []
            for case in node["cases"]:
                fields(case, {"left", "op", "right", "next"}, set(), "branch case")
                require(isinstance(case["op"], str) and case["op"] in OPS, "Unknown comparison operator")
                edges[name].append(case["next"])
                ref_values.extend([case["left"], case["right"]])
        elif kind == "return":
            fields(node, {"type", "value"}, {"title"}, name)
            edges[name] = []
            ref_values = [node["value"]]
        else:
            raise FlowError("Unknown node type at " + name)
        require(all(isinstance(target, str) and target in nodes for target in edges[name]), "Unknown next node at " + name)
        node_refs[name] = list(references(ref_values))

    reached, pending = set(), [flow["start"]]
    while pending:
        name = pending.pop()
        if name not in reached:
            reached.add(name)
            pending.extend(edges[name])
    require(reached == set(nodes), "Unreachable nodes: " + str(sorted(set(nodes) - reached)))
    can_exit = {name for name in nodes if not edges[name]}
    while True:
        expanded = can_exit | {name for name, targets in edges.items() if any(t in can_exit for t in targets)}
        if expanded == can_exit:
            break
        can_exit = expanded
    require(can_exit == set(nodes), "Every node must have a path to return")

    # A referenced answer must be available on EVERY path reaching this node.
    predecessors = {name: set() for name in nodes}
    for name, targets in edges.items():
        for target in targets:
            predecessors[target].add(name)
    dominators = {name: ({name} if name == flow["start"] else set(nodes)) for name in nodes}
    changed = True
    while changed:
        changed = False
        for name in nodes:
            if name == flow["start"]:
                continue
            common = set.intersection(*(dominators[p] for p in predecessors[name]))
            updated = common | {name}
            if updated != dominators[name]:
                dominators[name] = updated
                changed = True
    for name, refs in node_refs.items():
        for ref in refs:
            parts = ref.split(".")
            require(all(parts) and parts[0] in ("input", "nodes"), "Invalid reference: " + ref)
            if parts[0] == "nodes":
                require(len(parts) >= 3, "Use nodes.<node>.<question>[.<field>]")
                source, question = parts[1:3]
                require(source != name and source in dominators[name], "Answer is not available on every path: " + ref)
                if nodes[source]["type"] == "filter":
                    require(question in ("items", "criteria", "count", "ids", "matchedCount"), "Unknown filter output: " + ref)
                    continue
                if nodes[source]["type"] == "select":
                    require(parts[2:] in (["choice"], ["count"]), "select exposes only choice and count, not global probabilities")
                    continue
                definition = nodes[source]
                field_index = 3
                if definition["type"] == "parallel":
                    require(question in definition["branches"], "Unknown parallel branch: " + ref)
                    definition = definition["branches"][question]
                    if len(parts) == 3:
                        continue  # The complete typed result of one branch.
                    question, field_index = parts[3], 4
                else:
                    require(definition["type"] == "evaluate", "Unknown answer reference: " + ref)
                require(question in definition["questions"], "Unknown answer reference: " + ref)
                if len(parts) > field_index:
                    kind = definition["questions"][question]["type"]
                    allowed = {"type", "noul"} if kind == "noul" else {kind, "type", "probabilities", "confidence"}
                    require(parts[field_index] in allowed, "Unknown answer field: " + ref)
    return flow


def validate_answers(questions, answers):
    require(isinstance(answers, dict) and answers.keys() == questions.keys(), "Response must answer exactly the requested questions")
    for key, question in questions.items():
        answer = answers[key]
        kind = question["type"]
        require(isinstance(answer, dict) and answer.get("type") == kind, "Answer type mismatch: " + key)
        if kind == "noul":
            value = answer.get("noul")
            require(number(value) and 0 <= value <= 1, "Invalid noul probability: " + key)
        else:
            criteria = question["criteria"]
            labels = set(criteria) if kind == "choice" else {str(i) for i in range(len(criteria))}
            probabilities = answer.get("probabilities")
            require(isinstance(probabilities, dict) and set(probabilities) == labels, "Invalid distribution labels: " + key)
            require(all(number(p) and 0 <= p <= 1 for p in probabilities.values()), "Invalid probability: " + key)
            require(abs(sum(probabilities.values()) - 1) <= 0.02, "Distribution must sum to one: " + key)
            value = answer.get(kind)
            if kind == "choice":
                require(isinstance(value, str) and value in labels, "Unknown choice: " + key)
                require(probabilities[value] >= max(probabilities.values()) - 1e-6, "Choice disagrees with distribution: " + key)
            else:
                require(number(value) and 0 <= value <= len(criteria) - 1, "Score out of range: " + key)
            if "confidence" in answer:
                require(number(answer["confidence"]) and 0 <= answer["confidence"] <= 1, "Invalid confidence: " + key)



def snapshot_hash(flow):
    """Fingerprint the ordered definition, including candidate and branch order."""
    return "v2:" + hashlib.sha256(json.dumps(flow, ensure_ascii=False).encode()).hexdigest()


def validate_trace_snapshot(trace, flow=None):
    """Return the current identity after checking a new or legacy trace.

    Legacy hashes discarded mapping order. They can only be read with their
    embedded snapshot; matching an external definition also checks its order.
    Never rewrite the caller's trace or accept a legacy hash as a v2 identity.
    """
    recorded = trace.get("flow")
    if recorded is not None:
        recorded = validate_flow(recorded)
    expected = flow if flow is not None else recorded
    require(isinstance(expected, dict), "Trace requires a flow snapshot")
    digest = snapshot_hash(expected)
    stored_hash = trace.get("flowHash")
    message = "Trace belongs to a different flow snapshot"
    if recorded is not None:
        require(snapshot_hash(recorded) == digest, message)
    if stored_hash != digest:
        require(recorded is not None, message)
        legacy = hashlib.sha256(json.dumps(recorded, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        require(stored_hash == legacy, message)
    return digest
