"""Execute typed decision flows with bounded parallel judgments."""
import copy
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone

from .control import FlowError, check_cancelled
# Keep the previous core imports available to existing Python callers.
from .schema import (fields, json_value, load_flow, number, references, require,
                     snapshot_hash, validate_answers, validate_flow, validate_questions)


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


def _prepare_evaluation(node, context, node_id):
    state = resolve(node["state"], context)
    require(isinstance(state, (str, dict, list)), "Jev state must be text, object, or array")
    questions = resolve(node["questions"], context)
    validate_questions(questions, singleton=True)
    forced = {key: {"type": "choice", "choice": next(iter(q["criteria"])),
                    "probabilities": {next(iter(q["criteria"])): 1.0}}
              for key, q in questions.items() if q["type"] == "choice" and len(q["criteria"]) == 1}
    pending = {key: q for key, q in questions.items() if key not in forced}
    return {"node": node_id, "type": "evaluate", "status": "pending",
            "resolvedQuestions": questions, "forcedAnswers": forced,
            "request": {"state": state, "questions": pending}}


def _execute_evaluation(prepared, client, deadline, cancel_event=None):
    # Workers never mutate the published trace or another branch's state.
    step = copy.deepcopy(prepared)
    started = time.monotonic()
    step["startedAt"] = datetime.now(timezone.utc).isoformat()
    try:
        check_cancelled(cancel_event)
        require(started < deadline, "Flow timeout exceeded")
        request = step["request"]
        if request["questions"]:
            response = client.evaluate(state=copy.deepcopy(request["state"]),
                                       questions=copy.deepcopy(request["questions"]),
                                       timeout=deadline - started, node_id=step["node"])
            require(isinstance(response, dict), "Invalid response envelope")
            validate_answers(request["questions"], response.get("answers"))
        else:
            response = {"model": "none", "answers": {}, "source": "singleton"}
        check_cancelled(cancel_event)
        require(time.monotonic() < deadline, "Flow timeout exceeded")
        answers = {**response["answers"], **step["forcedAnswers"]}
        validate_answers(step["resolvedQuestions"], answers)
        step.update(status="completed", response=copy.deepcopy(response), answers=copy.deepcopy(answers),
                    elapsedMs=round((time.monotonic() - started) * 1000))
        return step
    except Exception as error:
        step.update(status="failed", error=str(error), retryable=bool(getattr(error, "retryable", False)),
                    elapsedMs=round((time.monotonic() - started) * 1000),
                    budgetMs=max(0, round((deadline - started) * 1000)))
        if hasattr(error, "transport_diagnostics"):
            step["transport"] = copy.deepcopy(error.transport_diagnostics)
        error.evaluation_trace = step
        raise


def _record_failure(step, error):
    step.update(copy.deepcopy(getattr(error, "evaluation_trace", {})))
    step.update(status="failed", error=str(error))


def _publish(observer, trace):
    """Observability is optional and must never change decision semantics.

    Only the execution owner publishes trace snapshots; worker threads never
    mutate/read the shared trace. Observers must enqueue/store promptly.
    """
    if observer is not None:
        try:
            observer(copy.deepcopy(trace))
        except Exception:
            import logging
            logging.getLogger(__name__).warning("Flow observer failed", exc_info=True)


def _check_call_budget(limits, calls, additional):
    cap = limits["max_calls"]
    require(cap is None or calls + additional <= cap, "Jev call budget exceeded")


def _run_parallel(node, context, node_id, client, deadline, limits, trace, step, cancel_event=None, observer=None):
    # Resolve every branch against the same pre-fork snapshot before any calls.
    branches = {name: _prepare_evaluation(definition, context, node_id + "." + name)
                for name, definition in node["branches"].items()}
    step["branches"] = branches
    _publish(observer, trace)
    jobs = [name for name, record in branches.items() if record["request"]["questions"]]
    _check_call_budget(limits, trace["calls"], len(jobs))
    for name, record in branches.items():
        if not record["request"]["questions"]:
            branches[name] = _execute_evaluation(record, client, deadline, cancel_event)
    if not jobs:
        step["status"] = "completed"
        _publish(observer, trace)
        return {name: record["answers"] for name, record in branches.items()}
    executor = ThreadPoolExecutor(max_workers=min(limits["max_concurrency"], len(jobs)))
    active, cursor = {}, 0
    step["status"] = "running"
    try:
        while cursor < len(jobs) or active:
            check_cancelled(cancel_event)
            require(time.monotonic() < deadline, "Flow timeout exceeded")
            while cursor < len(jobs) and len(active) < limits["max_concurrency"]:
                check_cancelled(cancel_event)
                name = jobs[cursor]
                cursor += 1
                branches[name]["status"] = "running"
                branches[name]["startedAt"] = datetime.now(timezone.utc).isoformat()
                trace["calls"] += 1
                _publish(observer, trace)
                active[executor.submit(_execute_evaluation, branches[name], client, deadline, cancel_event)] = name
            remaining = max(0, deadline - time.monotonic())
            done, _ = wait(active, timeout=min(remaining, 0.05) if cancel_event is not None else remaining,
                           return_when=FIRST_COMPLETED)
            check_cancelled(cancel_event)
            if not done:
                require(time.monotonic() < deadline, "Flow timeout exceeded")
                continue
            errors = []
            for future in done:
                name = active.pop(future)
                try:
                    branches[name] = future.result()
                except Exception as exc:
                    _record_failure(branches[name], exc)
                    errors.append(exc)
            _publish(observer, trace)
            if errors:
                raise errors[0]
        step["status"] = "completed"
        _publish(observer, trace)
        return {name: record["answers"] for name, record in branches.items()}
    except Exception as exc:
        step.update(status="failed", error=str(exc))
        for future, name in active.items():
            if future.cancel():
                branches[name]["status"] = "cancelled"
            elif future.done():
                try:
                    branches[name] = future.result()
                except Exception as error:
                    _record_failure(branches[name], error)
            else:
                branches[name]["status"] = "abandoned"
        for name in jobs[cursor:]:
            branches[name]["status"] = "cancelled"
        raise
    finally:
        # In-flight synchronous transports cannot be forcibly interrupted. They
        # receive the shared deadline; late results never enter the run's trace.
        _publish(observer, trace)
        executor.shutdown(wait=False, cancel_futures=True)


def run_flow(flow, inputs, client, trace=None, *, deadline_unix_ms=None, cancel_event=None, observer=None):
    """Execute one immutable flow snapshot using a duck-typed Jev client."""
    return _run_snapshot(validate_flow(copy.deepcopy(flow)), inputs, client, trace,
                         deadline_unix_ms=deadline_unix_ms, cancel_event=cancel_event, observer=observer)


def _run_snapshot(flow, inputs, client, trace=None, *, deadline_unix_ms=None, cancel_event=None, observer=None):
    """Execute an owned, validated snapshot. Only internal snapshot owners call this."""
    json_value(inputs)
    trace = trace if trace is not None else {}
    started = time.monotonic()
    trace.update({"flow": flow, "flowHash": snapshot_hash(flow),
                  "input": copy.deepcopy(inputs), "mode": client.mode, "startedAt": datetime.now(timezone.utc).isoformat(),
                  "status": "running", "calls": 0, "steps": []})
    context = {"input": copy.deepcopy(inputs), "nodes": {}}
    limits = {"max_steps": 32, "max_calls": 8, "timeout_seconds": 60, "max_concurrency": 5, **flow.get("limits", {})}
    deadline = started + limits["timeout_seconds"]
    if deadline_unix_ms is not None:
        require(number(deadline_unix_ms), "deadline_unix_ms must be finite")
        deadline = min(deadline, time.monotonic() + (deadline_unix_ms / 1000 - time.time()))
        trace["deadlineUnixMs"] = deadline_unix_ms
    current = flow["start"]
    steps_used = 0
    _publish(observer, trace)
    try:
        while steps_used < limits["max_steps"]:
            check_cancelled(cancel_event)
            steps_used += 1
            require(time.monotonic() < deadline, "Flow timeout exceeded")
            node = flow["nodes"][current]
            step = {"node": current, "type": node["type"], "status": "running"}
            step["startedAt"] = datetime.now(timezone.utc).isoformat()
            step_started = time.monotonic()
            trace["steps"].append(step)
            _publish(observer, trace)
            if node["type"] == "evaluate":
                prepared = _prepare_evaluation(node, context, current)
                step.update(prepared)
                if prepared["request"]["questions"]:
                    _check_call_budget(limits, trace["calls"], 1)
                    trace["calls"] += 1
                step["status"] = "running"
                _publish(observer, trace)
                try:
                    step.update(_execute_evaluation(prepared, client, deadline, cancel_event))
                except Exception as error:
                    _record_failure(step, error)
                    raise
                context["nodes"][current] = copy.deepcopy(step["answers"])
                current = node["next"]
            elif node["type"] == "parallel":
                steps_used += len(node["branches"])
                require(steps_used <= limits["max_steps"], "Node step budget exceeded")
                answers = _run_parallel(node, context, current, client, deadline, limits, trace, step, cancel_event, observer)
                context["nodes"][current] = copy.deepcopy(answers)
                current = node["next"]
            elif node["type"] == "select":
                step["criteriaChecks"] = []
                for index, source in enumerate([node["criteria"], *node.get("fallback_criteria", [])]):
                    criteria = resolve(source, context)
                    require(isinstance(criteria, dict), "select criteria must resolve to a mapping")
                    require(all(isinstance(k, str) and k and isinstance(v, str) and v.strip()
                                for k, v in criteria.items()), "select criteria need string IDs and descriptions")
                    step["criteriaChecks"].append({"index": index, "count": len(criteria)})
                    if criteria:
                        break
                require(bool(criteria), "select requires nonempty criteria")
                step.update(criteriaIndex=index, inputCount=len(criteria))
                state = resolve(node["state"], context)
                require(isinstance(state, (str, dict, list)), "Jev state must be text, object, or array")
                size = node.get("batch_size", 255)
                # Bound every round before starting requests; ordering is stable.
                count, needed_steps, needed_calls = len(criteria), 0, 0
                while True:
                    sizes = [min(size, count - offset) for offset in range(0, count, size)]
                    needed_steps += len(sizes)
                    needed_calls += sum(n > 1 for n in sizes)
                    if len(sizes) == 1:
                        break
                    count = len(sizes)
                require(steps_used + needed_steps <= limits["max_steps"], "Node step budget exceeded")
                _check_call_budget(limits, trace["calls"], needed_calls)
                # Use a literal isolated context so $ref-shaped input data is never interpreted as code.
                step["rounds"] = []
                remaining = list(criteria)
                while True:
                    groups = [remaining[i:i + size] for i in range(0, len(remaining), size)]
                    batch_input = {"state": state, "groups": {"batch_" + str(i): {k: criteria[k] for k in group}
                                                             for i, group in enumerate(groups)}}
                    branches = {name: {"state": {"$ref": "input.state"}, "questions": {"selection": {
                        "type": "choice", "instructions": node["instructions"],
                        "criteria": {"$ref": "input.groups." + name}}}} for name in batch_input["groups"]}
                    round_id = current + ".round_" + str(len(step["rounds"]))
                    record = {"node": round_id, "type": "parallel", "inputCount": len(remaining)}
                    step["rounds"].append(record)
                    steps_used += len(groups)
                    answers = _run_parallel({"branches": branches}, {"input": batch_input}, round_id,
                                            client, deadline, limits, trace, record, cancel_event, observer)
                    remaining = [answers[name]["selection"]["choice"] for name in branches]
                    record["selectedIds"] = remaining[:]
                    if len(remaining) == 1:
                        break
                context["nodes"][current] = {"choice": remaining[0], "count": len(criteria)}
                step["choice"] = remaining[0]
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
                matched_count = len(kept)
                if "order_by" in node:
                    # Stable lexicographic ordering, wholly specified by the flow.
                    # Validate each column before sorting; no coercion or missing-value defaults.
                    columns = []
                    for ordering in node["order_by"]:
                        values = {k: resolve({"$ref": "input." + ordering["field"]}, {"input": v}) for k, v in kept.items()}
                        require(all(number(v) for v in values.values()) or all(isinstance(v, str) for v in values.values()),
                                "ordering fields must be uniformly finite numbers or strings")
                        columns.append((ordering, values))
                    ordered = list(kept)
                    for ordering, values in reversed(columns):
                        ordered.sort(key=values.__getitem__, reverse=ordering["direction"] == "desc")
                    step["orderedIds"] = ordered[:]
                    step["orderBy"] = copy.deepcopy(node["order_by"])
                    if "limit" in node:
                        step["truncatedIds"] = ordered[node["limit"]:]
                        ordered = ordered[:node["limit"]]
                    kept = {k: kept[k] for k in ordered}
                context["nodes"][current] = {"items": kept, "criteria": {k: v["description"] for k, v in kept.items()},
                                             "count": len(kept), "ids": list(kept), "matchedCount": matched_count}
                step.update(inputCount=len(items), matchedCount=matched_count, keptIds=list(kept), excluded=excluded)
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
                step.update(value=result, status="completed", elapsedMs=round((time.monotonic()-step_started)*1000))
                trace.update(status="completed", result=result)
                return result
            step.update(status="completed", next=current, elapsedMs=round((time.monotonic()-step_started)*1000))
            _publish(observer, trace)
        raise FlowError("Node step budget exceeded")
    except Exception as exc:
        trace.update(status="failed", error=str(exc))
        if trace["steps"] and trace["steps"][-1].get("status") != "completed":
            trace["steps"][-1].update(status="failed", error=str(exc))
        raise
    finally:
        trace["elapsedMs"] = round((time.monotonic() - started) * 1000)
        _publish(observer, trace)
