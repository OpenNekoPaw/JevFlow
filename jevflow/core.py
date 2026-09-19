"""Four node types: filter, evaluate, branch, return. No expression evaluation."""

import copy
import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml


class FlowError(ValueError):
    pass


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
OPS = {"eq", "ne", "gt", "gte", "lt", "lte"}


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


def load_flow(path):
    try:
        flow = yaml.load(Path(path).read_text(encoding="utf-8"), Loader=UniqueLoader)
    except yaml.YAMLError as exc:
        raise FlowError("Invalid YAML: " + str(exc)) from exc
    return validate_flow(flow)


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
    fields(limits, set(), {"max_steps", "max_calls", "timeout_seconds"}, "limits")
    for key, value in limits.items():
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
        elif kind == "filter":
            fields(node, {"type", "items", "where", "next"}, {"title"}, name)
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
                    require(question in ("items", "criteria", "count"), "Unknown filter output: " + ref)
                    continue
                require(nodes[source]["type"] == "evaluate" and question in nodes[source]["questions"], "Unknown answer reference: " + ref)
                if len(parts) > 3:
                    kind = nodes[source]["questions"][question]["type"]
                    allowed = {"type", "noul"} if kind == "noul" else {kind, "type", "probabilities", "confidence"}
                    require(parts[3] in allowed, "Unknown answer field: " + ref)
    return flow


def resolve(value, context):
    if isinstance(value, dict):
        if "$ref" in value:
            ref = value["$ref"]
            result = context
            for part in ref.split("."):
                if isinstance(result, dict) and part in result:
                    result = result[part]
                elif isinstance(result, list) and part.isdigit() and int(part) < len(result):
                    result = result[int(part)]
                else:
                    raise FlowError("Missing value: " + ref)
            return copy.deepcopy(result)
        return {k: resolve(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, context) for v in value]
    return value


def compare(left, op, right):
    if op in ("eq", "ne"):
        equal = (type(left) is type(right) or (number(left) and number(right))) and left == right
        return equal if op == "eq" else not equal
    require(number(left) and number(right), "Ordered comparisons require finite numbers")
    return {"gt": left > right, "gte": left >= right, "lt": left < right, "lte": left <= right}[op]


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


def run_flow(flow, inputs, client, trace=None):
    """Execute one immutable flow snapshot using a duck-typed Jev client."""
    flow = validate_flow(copy.deepcopy(flow))
    json_value(inputs)
    trace = trace if trace is not None else {}
    started = time.monotonic()
    trace.update({"flow": flow, "flowHash": hashlib.sha256(json.dumps(flow, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                  "input": copy.deepcopy(inputs), "mode": client.mode, "startedAt": datetime.now(timezone.utc).isoformat(),
                  "status": "running", "calls": 0, "steps": []})
    context = {"input": copy.deepcopy(inputs), "nodes": {}}
    limits = {"max_steps": 32, "max_calls": 8, "timeout_seconds": 60, **flow.get("limits", {})}
    deadline = started + limits["timeout_seconds"]
    current = flow["start"]
    try:
        for index in range(limits["max_steps"]):
            require(time.monotonic() < deadline, "Flow timeout exceeded")
            node = flow["nodes"][current]
            step = {"node": current, "type": node["type"]}
            trace["steps"].append(step)
            if node["type"] == "evaluate":
                state = resolve(node["state"], context)
                require(isinstance(state, (str, dict, list)), "Jev state must be text, object, or array")
                questions = resolve(node["questions"], context)
                validate_questions(questions, singleton=True)
                forced = {key: {"type": "choice", "choice": next(iter(q["criteria"])),
                                "probabilities": {next(iter(q["criteria"])): 1.0}}
                          for key, q in questions.items() if q["type"] == "choice" and len(q["criteria"]) == 1}
                pending = {key: q for key, q in questions.items() if key not in forced}
                step["resolvedQuestions"] = copy.deepcopy(questions)
                step["forcedAnswers"] = copy.deepcopy(forced)
                step["request"] = {"state": state, "questions": copy.deepcopy(pending)}
                call_started = time.monotonic()
                if pending:
                    require(trace["calls"] < limits["max_calls"], "Jev call budget exceeded")
                    trace["calls"] += 1
                    response = client.evaluate(state=state, questions=pending,
                                               timeout=max(0.001, deadline - call_started), node_id=current)
                    require(isinstance(response, dict), "Invalid response envelope")
                    validate_answers(pending, response.get("answers"))
                else:
                    response = {"model": "none", "answers": {}, "source": "singleton"}
                step["elapsedMs"] = round((time.monotonic() - call_started) * 1000)
                step["response"] = copy.deepcopy(response)
                require(time.monotonic() < deadline, "Flow timeout exceeded")
                answers = {**response["answers"], **forced}
                validate_answers(questions, answers)
                context["nodes"][current] = copy.deepcopy(answers)
                step["answers"] = copy.deepcopy(answers)
                current = node["next"]
            elif node["type"] == "filter":
                items = resolve(node["items"], context)
                require(isinstance(items, dict), "filter items must be an ID-to-item mapping")
                kept, excluded = {}, {}
                for key, item in items.items():
                    require(isinstance(key, str) and key and isinstance(item, dict)
                            and isinstance(item.get("description"), str) and bool(item["description"].strip()),
                            "filter items need string IDs and descriptions")
                    checks = [compare(resolve({"$ref": "input." + p["field"]}, {"input": item}),
                                      p["op"], resolve(p["value"], context)) for p in node["where"]]
                    if all(checks):
                        kept[key] = item
                    else:
                        excluded[key] = [i for i, matched in enumerate(checks) if not matched]
                context["nodes"][current] = {"items": kept, "criteria": {k: v["description"] for k, v in kept.items()}, "count": len(kept)}
                step.update(inputCount=len(items), keptIds=list(kept), excluded=excluded)
                current = node["next"]
            elif node["type"] == "branch":
                target = node["default"]
                step["checks"] = []
                for case in node["cases"]:
                    left, right = resolve(case["left"], context), resolve(case["right"], context)
                    matched = compare(left, case["op"], right)
                    step["checks"].append({"left": left, "op": case["op"], "right": right, "matched": matched})
                    if matched:
                        target = case["next"]
                        break
                current = target
            else:
                result = resolve(node["value"], context)
                step["value"] = result
                trace.update(status="completed", result=result)
                return result
            step["next"] = current
        raise FlowError("Node step budget exceeded")
    except Exception as exc:
        trace.update(status="failed", error=str(exc))
        raise
    finally:
        trace["elapsedMs"] = round((time.monotonic() - started) * 1000)
