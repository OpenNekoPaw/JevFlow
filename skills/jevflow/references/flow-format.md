# jevflow v1 format

Top level: required `version: 1`, `name`, `start`, `nodes`; optional quoted `revision` and `limits`. Unknown fields, duplicate YAML keys, missing targets, unreachable nodes, and nodes with no path to a return are rejected.

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
| `choice` | 2–255 string option IDs mapped to nonempty descriptions | `choice`, `probabilities`, optional `confidence` |
| `noul` | Optional `{"true": "yes definition", "false": "no definition"}` | `noul`: probability of yes in [0, 1] |
| `score` | Ordered list of at least two nonempty descriptions | `score` in [0, levels−1], `probabilities`, optional `confidence` |

This MVP uses text instructions and descriptions, a subset of the native API. Noul has no separate confidence. Choice probabilities compare competing options, not game win probabilities. Score measures degree; a noul of 0.5 means uncertainty, not medium severity. Native extra fields such as score `legend` remain in response traces.

`branch`: nonempty ordered `cases`, each with `left`, `op`, `right`, `next`; mandatory `default`. First matching case wins. Operators: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`. Ordered comparisons require numbers; booleans are not numbers. Use consecutive branches for compound conditions.

`return`: `value` is JSON-compatible data, optionally with references. It ends the flow. An `agent_review` return is just data: the host decides whether to invoke an agent.

## References and state

A reference is exactly `{$ref: input.path}` or `{$ref: nodes.node_id.question_id.field}`. It can appear anywhere inside an evaluate `state`, branch operands, or return `value`. No string interpolation, expression execution, or dynamic question generation. Nested dictionaries and arrays are supported; list indices use dotted integers, e.g. `input.messages.0.text`. Dictionary keys containing dots cannot be addressed by this path syntax.

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
