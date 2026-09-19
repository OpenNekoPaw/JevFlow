# jevflow v1 format

Top level: required `version: 1`, `name`, `start`, `nodes`; optional quoted `revision`, `limits`, display `title` and `locale` (`zh-CN` or `en`). Unknown fields, duplicate YAML keys, missing targets, unreachable nodes, and nodes with no path to a return are rejected.

```yaml
version: 1
name: request-router
revision: "1"
start: classify
limits: {max_steps: 12, max_calls: 2, timeout_seconds: 60}
nodes:
  classify:
    type: evaluate
    state: {$ref: input}
    questions:
      intent:
        type: choice
        instructions: Does this request ask to join a game?
        criteria:
          join: The user asks to participate in a game.
          other: Any other request or insufficient information.
    next: route
  route:
    type: branch
    cases:
      - left: {$ref: nodes.classify.intent.choice}
        op: eq
        right: join
        next: accepted
    default: fallback
  accepted:
    type: return
    value: {intent: join}
  fallback:
    type: return
    value: {intent: other}
```

## Nodes

`evaluate`: `state`, nonempty `questions`, `next`. All questions share the resolved state and are sent in one request. Each question uses `type`, `instructions` (nonempty text) and optional/required `criteria`:

| Type | Criteria | Result fields |
|---|---|---|
| `choice` | 1–255 string option IDs mapped to nonempty descriptions, or a `$ref` to that mapping | `choice`, `probabilities`, optional `confidence` |
| `noul` | Optional `{"true": "yes definition", "false": "no definition"}` | `noul`: probability of yes in [0, 1] |
| `score` | Ordered list of at least two nonempty descriptions | `score` in [0, levels−1], `probabilities`, optional `confidence` |

This MVP uses text instructions and descriptions, a subset of the native API. Noul has no separate confidence. Choice probabilities compare competing options, not game win probabilities. Score measures degree; a noul of 0.5 means uncertainty, not medium severity. Native extra fields such as score `legend` remain in response traces.

`branch`: nonempty ordered `cases`, each with `left`, `op`, `right`, `next`; mandatory `default`. First matching case wins. Operators: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`. Ordered comparisons require numbers; booleans are not numbers. Use consecutive branches for compound conditions.

`return`: `value` is JSON-compatible data, optionally with references. It ends the flow. An `agent_review` return is just data: the host decides whether to invoke an agent.

## References and state

A reference is exactly `{$ref: input.path}` or `{$ref: nodes.node_id.question_id.field}`. It can appear anywhere inside evaluate `state` and dynamic criteria, filter `items` and predicate values, branch operands, or return `value`. No string interpolation, expression execution, or dynamic question generation. Nested dictionaries and arrays are supported; list indices use dotted integers, e.g. `input.messages.0.text`. Dictionary keys containing dots cannot be addressed by this path syntax.

References to prior answers must be available on every path to the consumer. Validation checks this before execution, including merges. Missing input fields fail at runtime. Input is immutable; node answers are separate. In a cycle a node's latest completed answer replaces its prior answer, while trace preserves all visits.

Use quoted YAML keys for `"true"`, `"false"`, and numbers; quote dates. Values must serialize as finite JSON. Do not use recursive YAML aliases. All limits are positive integers; exceeding a budget fails rather than taking an arbitrary fallback.

## Mock runs and cases

Mock file: mapping from evaluate node ID to that node's answers (without the top-level `answers` envelope). An array of answer mappings supplies successive visits to a repeated node. Missing mocks fail without network access.

```json
{
  "classify": {
    "intent": {
      "type": "choice", "choice": "join",
      "probabilities": {"join": 0.9, "other": 0.1}, "confidence": 0.8
    }
  }
}
```

Test cases are a JSON array of objects with `name`, `input`, `expected`, and (for offline execution) `mock`. Expected output is compared to the complete returned value. CLI `test --live` ignores mock data; only `input` and selected YAML state enter model requests. Live test runs stop on execution/API error; wrong but valid results are scored and remaining cases continue.

Traces include `mode`, the full flow definition/hash, input, steps, model responses, timings and result or error. Logs from mock mode describe orchestration, not model quality. `Flow.update` changes memory only; saving a candidate YAML and calling `reload` is a separate host operation.

## Single-call shorthand

Use `mode: single`, `version: 1`, `name`, `state` and `questions` instead of `start` and `nodes`. Optional `revision` and `limits` work as in a flow. Optional `result` is a return-value template; answers are referenced as `nodes.evaluate.<question>.<field>`. Without `result`, all typed answers are returned. This compiles to the standard `evaluate` and `result` nodes, so mocks use the node ID `evaluate`. A single call may contain multiple independent questions.

```yaml
version: 1
name: sentiment
mode: single
state: {$ref: input}
questions:
  positive:
    type: noul
    instructions: Does this message express a positive sentiment?
result: {$ref: nodes.evaluate.positive.noul}
```

`mode: flow` is optional on the full node-based format. `validate_flow()` and `load_flow()` return the canonical node graph; `Flow.definition` exposes that graph. Single is a configuration convenience, not a second runtime.

## Candidate filter / 候选约束

Every node accepts an optional display `title`. A `filter` has `items`, nonempty `where`, and `next`. Items are a mapping of candidate ID to JSON object with a nonempty `description` string. Each predicate uses a dotted item `field`, `op` (`eq/ne/gt/gte/lt/lte`), and `value` (literal or `$ref`). All predicates must match. A missing fact is an error, not an implicit pass.

```yaml
eligible:
  type: filter
  title: 只保留预算内候选
  items: {$ref: input.candidates}
  where:
    - {field: cost, op: lte, value: {$ref: input.budget}}
  next: available
```

Outputs: `nodes.eligible.items`, `.criteria` (ID to description) and `.count`. Reference `.criteria` from the downstream choice to enforce the boundary. Trace records `inputCount`, `keptIds`, and `excluded` with failing zero-based predicate indices. No model call is made by the filter.

An empty resolved choice fails before any model call; use a count branch and explicit fallback. A singleton choice becomes a typed answer with probability 1 without calling Jev. Other questions in the same evaluate node still run. Trace separates `forcedAnswers`, `resolvedQuestions`, provider request/response and merged `answers`. The direct HTTP client's minimum remains two options; singleton handling belongs to the flow runtime.

A reference from a filter predicate can use a prior Jev answer, enabling `evaluate → filter → evaluate`. The answer must dominate the filter on all paths, just like other references. See `examples/constrained.yaml` for a complete runnable graph.
