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

`branch`: nonempty ordered `cases`, each with `left`, `op`, `right`, `next`; mandatory `default`. First matching case wins. Operators: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `contains`, `not_contains`. Membership operators require an array on the left and compare complete elements using the same equality semantics as `eq`; they do not match substrings or dictionary keys. Ordered comparisons require numbers; booleans are not numbers. Use consecutive branches for compound conditions.

`return`: `value` is JSON-compatible data, optionally with references. It ends the flow. An `agent_review` return is just data: the host decides whether to invoke an agent.

## References and state

A reference is exactly `{$ref: input.path}` or `{$ref: nodes.node_id.question_id.field}`; parallel outputs add a branch component as described below. It can appear anywhere inside evaluate `state` and dynamic criteria (including parallel branches), filter `items` and predicate values, branch operands, or return `value`. No string interpolation, expression execution, or dynamic question generation. Nested dictionaries and arrays are supported; list indices use dotted integers, e.g. `input.messages.0.text`. Dictionary keys containing dots cannot be addressed by this path syntax.

References to prior answers must be available on every path to the consumer. Validation checks this before execution, including merges. Missing input fields fail at runtime. Input is immutable; node answers are separate. In a cycle a node's latest completed answer replaces its prior answer, while trace preserves all visits.

Use quoted YAML keys for `"true"`, `"false"`, and numbers; quote dates. Values must serialize as finite JSON. Do not use recursive YAML aliases. Limits are positive integers, except explicit `max_calls: null` disables the model-call count cap (omitting it still defaults to 8); exceeding a budget fails rather than taking an arbitrary fallback.

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

Every node accepts an optional display `title`. A `filter` has `items`, nonempty `where`, and `next`. Items are a mapping of candidate ID to JSON object with a nonempty `description` string. Each predicate uses a dotted item `field`, `op` (`eq/ne/gt/gte/lt/lte/contains/not_contains`), and `value` (literal or `$ref`). All predicates must match. A missing fact is an error, not an implicit pass.

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

### Deterministic narrowing / 确定性收敛

A filter optionally accepts `order_by` and `limit`:

```yaml
resolve:
  type: filter
  items: {$ref: nodes.eligible.items}
  where: [{field: description, op: ne, value: ''}]
  order_by:
    - {field: cost, direction: asc}
    - {field: benefit, direction: desc}
    - {field: stableKey, direction: asc}
  limit: 1
  next: result
result:
  type: return
  value: {candidate: {$ref: nodes.resolve.ids.0}}
```

Predicates run first; remaining items are ordered lexicographically by the listed fields.
Each field must resolve to uniformly finite numbers or strings; missing or mixed types fail.
Equal keys preserve input order, so specify a stable final key when ties must be independent
of input order. `limit` is a positive integer and requires `order_by`. Empty inputs stay empty:
check `.count` before reading `.ids.0` if emptiness is possible. `.ids` contains retained IDs
in order; `.matchedCount` counts predicate matches before truncation. Trace additionally records
`orderBy`, `orderedIds`, and `truncatedIds`, separately from predicate exclusions. This is exact
execution of a configured comparison, not model ranking or proof of optimal task outcome.

## Parallel judgments / 并行判断

`parallel`: required `branches` (at least two named branches) and `next`; optional `title`. Each branch contains required `state` and `questions`, and optional `title`. IDs use the same identifier rules as node IDs. Branches are independent Jev judgments, not nested flows; no branch `type`, `next`, custom join policy or implicit dependency scheduling is supported.

```yaml
analyze:
  type: parallel
  title: 独立分析同一局面
  branches:
    environment:
      state: {$ref: input.environment}
      questions:
        danger: {type: noul, instructions: 当前环境是否危险？}
    enemies:
      state: {$ref: input.enemies}
      questions:
        threat: {type: noul, instructions: 可见敌人是否构成迫近威胁？}
  next: combine
```

Use `nodes.analyze.environment.danger.noul` for an individual field, `nodes.analyze.environment.danger` for the typed answer, or `nodes.analyze.environment` for all answers of that branch. Consumers must be reached through the parallel node on every path. Branches can reference inputs and earlier dominating nodes; references to the parallel node itself or its siblings are rejected. Every branch resolves from the same pre-fork context, with private copies passed to the client. Only a complete successful result is stored under `nodes.analyze`.

For mocks, use qualified IDs such as `"analyze.environment"` and `"analyze.enemies"`; arrays still supply successive visits. Traces retain each branch under the parallel step's `branches` mapping, with requests, answers, timings and status. Model `usage` and `transportAttempts` stay in each branch response; consumers summing usage must traverse these nested records. HTML and Mermaid show the fork, individual judgments and join. The displayed join is derived from `parallel`, not an extra YAML node.

Limits are shared across the run:

- `max_concurrency`: default 5; accepts higher positive integers without a fixed upper cap. Raise call/step budgets as needed. Bounds scheduled model requests inside one parallel node. Setting 1 serializes the branches. It is not a host-wide rate limit.
- `max_calls`: default 8; explicit `null` disables only the call-count cap; the runtime checks capacity for all non-singleton branches before making any request. Counts scheduled client evaluations, including failed ones, not transport-level retries. Singleton branches consume zero calls.
- `max_steps`: default 32; the parent parallel node and every branch each cost one step. Capacity for the entire group is checked before launching it; downstream nodes also need their own budget.
- `timeout_seconds`: default 60; one deadline includes preparation, queueing and execution. Each client gets the remaining time at invocation, not a new full timeout.

Any failure or timeout fails the whole run; there is no implicit partial-result or fallback policy. Unstarted work is cancelled. Already-running synchronous transports cannot be forcibly stopped and may still incur usage; they must honor their timeout. Late answers cannot change the returned trace or reach downstream nodes. Branch statuses distinguish `pending`, `running`, `completed`, `failed`, `cancelled`, and `abandoned` (still in flight when the run stopped). Custom clients must tolerate concurrent `evaluate` calls; use `max_concurrency: 1` for a client without that guarantee. Do not reuse a mutable client concurrently with abandoned calls unless it is thread-safe.

Use a single batched `evaluate` for independent questions sharing the same evidence. Use `parallel` for independent questions with different evidence. Put dependent judgments after the join. The game example in `examples/parallel.yaml` analyzes environment, own status, items and visible enemies, then plans a stage objective without choosing a concrete action.

## Batched selection / 分组选择

`select` owns a complete candidate tournament inside the runtime. Required: `state`, `criteria` (a string-ID-to-description mapping or `$ref`), `instructions`, `next`. Optional `batch_size` defaults to 255 and accepts integers 2..255. It exposes `nodes.<id>.choice` and `.count` (the selected pool size before batching); there is no fabricated global probability distribution.

Optional `fallback_criteria` is a nonempty ordered list of mappings or `$ref`s. Only when `criteria` is empty, try these in order and use the first nonempty pool. Later pools are not evaluated after a nonempty one is found. This replaces repeated empty-check branches, selectors and returns when only the candidate pool changes. Never include a pool that violates a hard constraint; relaxing policy preferences must be explicit in YAML. All pools empty still fails, as do malformed/missing data and provider errors; this is not an error retry mechanism. References in every fallback must be available on all incoming paths. Trace `criteriaChecks` records checked pool indices and counts, `criteriaIndex` identifies the selected pool (0 = primary, 1 = first fallback), and `inputCount` records its size.

```yaml
choose:
  type: select
  state: {$ref: input.state}
  criteria: {$ref: nodes.preferred.criteria}
  fallback_criteria:
    - {$ref: nodes.eligible.criteria}
  instructions: Choose within the selected pool; hard eligibility is never relaxed.
  next: result
result:
  type: return
  value:
    candidate: {$ref: nodes.choose.choice}
    candidateCount: {$ref: nodes.choose.count}
```

```yaml
choose:
  type: select
  state: {$ref: input.state}
  criteria: {$ref: nodes.eligible.criteria}
  instructions: 在已约束的候选中选择最符合当前目标的一项。
  batch_size: 32
  next: result
result:
  type: return
  value: {candidate: {$ref: nodes.choose.choice}}
```

Apply policy filters to the full candidate set before `select`. The node partitions in insertion order, selects one winner per group, then compares winners until one remains. Grouping can affect outcomes; it is not equivalent to global ranking. Do not expose group probabilities as overall confidence. Empty pools require an explicit branch or `fallback_criteria`; if all configured pools are empty, selection fails. A singleton group needs no provider call.

Groups in a round share `limits.max_concurrency` (default 5); later rounds depend on earlier winners. The selector and each group evaluation count toward `max_steps`, while non-singleton groups count toward `max_calls`. All rounds are budget-checked before the first request. Requests and retries share the run's absolute deadline. Failure never publishes a partial winner. Running calls follow the same cancellation/late-result rules as `parallel`.

Trace `rounds` hold per-group evaluation records, input counts and selected IDs. Mock keys are `<node>.round_0.batch_0`, etc., with a `selection` choice answer. Generic metrics include these nested evaluations without adding overlapping durations into wall time. The visual inspector shows the selector and its full nested trace.

The host provides legal candidates and scenario facts; it must not duplicate tournament execution or interpret strategy guards outside the flow. Cross-run caching is not implied by this node. Keep domain heuristics and filter priorities in YAML, not in the engine or application callbacks.
