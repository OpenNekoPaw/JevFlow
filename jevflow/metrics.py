"""Offline trace metrics, independent of any game, host or provider SDK."""
import math
from collections import Counter, defaultdict

from .schema import require, snapshot_hash


def latency_summary(values):
    """Nearest-rank milliseconds; missing values are omitted, never zero-filled."""
    values = list(values)
    require(all(v is None or (type(v) in (int, float) and math.isfinite(v) and v >= 0)
                for v in values), 'Latency values must be finite nonnegative numbers or null')
    ordered = sorted(v for v in values if v is not None)
    if not ordered:
        return {'n': 0}
    return {'n': len(ordered), 'meanMs': round(sum(ordered) / len(ordered), 1),
            'p50Ms': ordered[math.ceil(len(ordered) * .5) - 1],
            'p95Ms': ordered[math.ceil(len(ordered) * .95) - 1], 'maxMs': ordered[-1]}


def evaluation_steps(trace):
    """Yield evaluated or pending branch records without double-counting joins."""
    for step in trace['steps']:
        if step['type'] == 'evaluate':
            yield step
        elif step['type'] == 'parallel':
            yield from step.get('branches', {}).values()
        elif step['type'] == 'select':
            yield from evaluation_steps({'steps': step.get('rounds', [])})


def trace_metrics(trace):
    """Extract only counters and timing samples; never export inputs or answers."""
    require(isinstance(trace, dict), 'Expected a Flow execution trace')
    require(trace.get('status') in ('completed', 'failed', 'running', 'cancelled'), 'Invalid trace status')
    has_execution = isinstance(trace.get('flow'), dict) and isinstance(trace.get('steps'), list)
    before_execution = (trace['status'] in ('failed', 'cancelled')
                        and isinstance(trace.get('runId'), str) and bool(trace['runId'])
                        and 'flow' not in trace and 'steps' not in trace)
    require(has_execution or before_execution, 'Expected a Flow execution trace')
    counts = Counter(runs=1, logicalCalls=trace.get('calls', 0))
    counts[trace['status']] += 1
    samples = defaultdict(list)
    samples['total'].append(trace.get('totalElapsedMs', trace.get('elapsedMs')))
    samples['flow'].append(trace.get('elapsedMs'))
    samples['flow.' + trace['status']].append(trace.get('elapsedMs'))
    nodes = defaultdict(list)
    steps = trace.get('steps', [])
    evaluations = list(evaluation_steps({'steps': steps}))
    for step in evaluations:
        status = step.get('status', 'unknown')
        counts['evaluations.' + status] += 1
        nodes[step['node']].append(step.get('elapsedMs'))
        samples['evaluate'].append(step.get('elapsedMs'))
        response = step.get('response') or step.get('transport', {})
        for attempt in response.get('transportAttempts', []):
            counts['observedTransportAttempts'] += 1
            counts['transportRetries'] += attempt.get('attempt', 1) > 1
            counts['transportFailures'] += not attempt.get('ok', False)
            counts['transportTimeouts'] += attempt.get('kind') == 'timeout'
            samples['transport'].append(attempt.get('elapsedMs'))
            samples['transport.' + attempt.get('kind', 'unknown')].append(attempt.get('elapsedMs'))
            samples['sdk'].append(attempt.get('sdkMs'))
            samples['nodeProcessOverhead'].append(attempt.get('processOverheadMs'))
    # Sum only non-overlapping serial evaluations with complete timing data.
    if (has_execution and not any(s['type'] in ('parallel', 'select') for s in steps)
            and trace.get('elapsedMs') is not None
            and all(s.get('elapsedMs') is not None for s in evaluations)):
        samples['serialNonEvaluate'].append(max(0, trace['elapsedMs'] - sum(s['elapsedMs'] for s in evaluations)))
    return {'counts': dict(counts), 'samples': dict(samples), 'nodes': dict(nodes)}


def summarize_traces(traces):
    """Aggregate observed timings, retaining failed runs and per-mode/config groups."""
    def empty():
        return {'counts': Counter(), 'samples': defaultdict(list), 'nodes': defaultdict(list)}

    def add(target, item):
        target['counts'].update(item['counts'])
        for field in ('samples', 'nodes'):
            for name, values in item[field].items():
                target[field][name].extend(values)

    def finish(target):
        return {'counts': dict(target['counts']),
                'latency': {k: latency_summary(v) for k, v in target['samples'].items()},
                'nodes': {k: latency_summary(v) for k, v in target['nodes'].items()}}

    total, groups = empty(), {}
    for trace in traces:
        item = trace_metrics(trace)
        add(total, item)
        flow = trace.get('flow')
        # Recompute from the ordered snapshot: legacy hashes conflated orders.
        # Pre-execution failures have no known configuration, never a fake hash.
        fingerprint = snapshot_hash(flow) if flow is not None else None
        key = (trace.get('mode', 'unknown'), fingerprint)
        if key not in groups:
            groups[key] = {'name': (flow or {}).get('name'), 'mode': key[0],
                           'flowHash': fingerprint, 'metrics': empty()}
        add(groups[key]['metrics'], item)
    return {'method': 'Nearest-rank milliseconds. Failed and cancelled runs are included; missing timings are not zero. '
            'Total time includes load and queueing; flow time is available only after execution starts. '
            'Runs without a loaded snapshot have a null configuration hash. '
            'Flow wall time is not the sum of overlapping parallel branches. SDK time includes network/service waiting. '
            'Abandoned requests may finish or incur usage outside the observed trace. Host deadlines, actions and task quality are not inferred.',
            **finish(total), 'groups': [{**{k: v for k, v in group.items() if k != 'metrics'},
                                        **finish(group['metrics'])} for group in groups.values()]}
