# Scenario budgets and parallelism

Use this reference to choose latency, concurrency and reasoning budgets for a host-owned flow. For node syntax and exact runtime semantics, use [flow-format.md](flow-format.md). No scenario profile, scheduler, background worker, cache or fallback service is created by selecting a design pattern below.

## Establish two independent requirements

Identify the action/response deadline, whether missing it is tolerable, how quickly the state changes, and what constitutes an adequate decision. Use requirements and existing measurements when available; clarify only material missing constraints. Do not silently choose a slow remote path for an unspecified hard deadline.

Separate timing from reasoning complexity. A millisecond control loop may execute a complex strategy prepared earlier; a leisurely batch classification may need only one judgment. `single`, `flow` and the number of nodes do not determine either property.

For a new or changed design, state a compact operating contract in the host's notes: deadline and submission reserve; required evidence and judgments; maximum calls/steps/concurrency; plan invalidation conditions; and what the host does when no valid result arrives. This is host documentation, not an invented YAML schema. Do not expand a simple task into a new scheduling platform.

## Real-time / 实时

For a hard deadline or rapidly changing state, remote Jev calls have no guaranteed worst-case latency. An average or a small-sample P95 below the deadline is insufficient to promise completion. Even one call, and even a parallel group, can overrun. Keep the hard control loop in bounded host logic when remote completion cannot satisfy the requirement.

Where the existing application permits it, let a slower host planning loop produce an objective, constraints and validity conditions; the fast execution loop applies the latest valid plan to current legal candidates. Cache policy decisions only for the state scope they were validated for. Bind a plan to relevant policy/config revision and observation version or material state signature; also check age and scenario-specific events. Elapsed age alone is not sufficient validity. Recheck legality and authority immediately before action. Discard stale asynchronous results; never apply a delayed result to a changed world merely because the model succeeded.

If a plan is unavailable or invalid, use only the host's explicitly defined behavior: for example, a legal action under an existing valid policy, a permitted no-op, or an unresolved result for the caller. Define this before relying on it. The engine does not turn timeouts into a successful plan, create legal actions, or select an arbitrary fallback. If the task requires a new remote judgment before every hard-deadline action and has no valid alternative, report the incompatible requirement instead of claiming the timing problem is solved.

## Near-real-time / 准实时

For interactive requests or turn-based decisions that tolerate bounded waiting, design a short critical path using only decision-relevant judgments. Precompute exact facts in the host, supply each node's relevant evidence, and reuse plans while their assumptions remain valid. Keep mandatory risk and legality checks when reducing work; remove duplicated analysis rather than the policy's exceptions.

Compute one host deadline from the remaining service budget, accounting for clock offset when service time is authoritative. Allocate preparation, planning, selection, final observation/submission and recovery within it. For example, if an action is due in 5 seconds and submission needs 1 second, all preparation and model work together have less than 4 seconds; three sequential requests whose observed P95 is 1.5 seconds each are not a defensible fit. Do not adopt the prior game's 42-second decision budget, 10-second reserve or 8-second attempt cap in an unrelated application.

Pass `deadline_unix_ms` (Unix milliseconds) to `Flow.run`; execution uses the earlier of that absolute deadline and `limits.timeout_seconds`. When a later stage needs reserved time, give the earlier stage an earlier deadline. Never reset the full time allowance for every node, stage or retry. YAML timeout seconds are positive integers; the host absolute deadline can express a tighter remaining budget.

For `GatewayClient`, choose `attempt_timeout` in seconds and `max_retries` from observed latency and remaining budget. Defaults are no attempt cap beyond the call budget and zero retries; `max_calls` does not limit transport retries. Avoid a cap so short that normal responses routinely trigger duplicate work. Retry a transient failing request only when another useful attempt plus backoff and downstream work can fit. Completed nodes remain intact during a client retry. An authentication/configuration error, invalid answer, missing evidence or exhausted budget is not fixed by more retries. Whole-flow recovery is a host decision: obtain a fresh observation, bound the attempts, and retain the original action deadline.

## Complex strategy / 复杂策略

Start from the actual policy, not a fixed node count. Map priorities and exceptions to conditions and evidence: immediate success/failure threats, competing routes, resource or control constraints, coordination, and reconsideration triggers where relevant to the domain. Compare alternatives with their downstream consequences. Preserve source evidence needed to check an earlier model conclusion. Do not put the full policy at every node or collapse all exceptions into a generic “analyze strategy” question to save calls.

Use typed uncertainty/unmatched outcomes where appropriate and route them to a targeted evidence check or a host-facing unresolved result. Such routes must be explicitly defined from successful typed answers; there is no YAML exception handler that catches a failed request. Returning `agent_review` is data for the host, not an automatic agent invocation. The host gathers new evidence or performs search; repeated flow cycles read the same immutable input and cannot discover new facts.

Count required work on plausible branches, including verification and bounded loop visits. Runtime defaults are `max_calls: 8`, `max_steps: 32`, `timeout_seconds: 60`, `max_concurrency: 5`; these are execution guards, not adequacy targets. Set `max_calls: null` when the user requests no call-count cap; deadlines, cancellation, concurrency and step limits still apply. Omitting max_calls retains the default 8. Raise them when required by a justified design and the host's time/cost budget. A parallel group reserves all its scheduled evaluations before launch, so a required group larger than the remaining call budget fails rather than quietly omitting evidence. Include downstream synthesis and return steps in the budget.

If adequate reasoning cannot fit a real-time execution path, use host-managed slower planning and bounded execution with explicit validity conditions. If that separation is unavailable, expose the tradeoff or return unresolved; do not report an incomplete policy as a fully evaluated strategy. More nodes, higher confidence or repeated agreement do not establish better decisions. Validate priority conflicts, rare exceptions, alternative routes and failure outcomes, not just the most frequently selected tactic.

## Choose batching, parallelism or dependency order

| Relationship between judgments | Shape |
|---|---|
| Independent questions over the same bounded evidence | One `evaluate` with multiple questions; no guaranteed latency benefit from an oversized batch. |
| Independent questions needing different evidence | `parallel` with scoped branch state, then a downstream judgment using the joined answers. |
| A question needs another question's answer | Sequential nodes, or sequential layers of parallel groups. |
| Slow optional enrichment need not block the current result | Separate host-scheduled work; the current parallel node cannot perform a partial join. |

Environment, status, items and visible enemy assessments can run concurrently only if their inputs and question meanings are independent. A question about whether to spend an item that depends on the enemy assessment belongs after the join. Independence does not mean the decisions never interact; their interaction is resolved downstream.

The current MVP's `parallel` branches are evaluate definitions, not arbitrary subflows. They share a pre-fork snapshot, cannot read siblings and must all succeed before `next`. There is no first-success, quorum, best-effort, per-branch error route or automatic dependency scheduling. On failure, unstarted work is cancelled and late in-flight results discarded; in-flight synchronous calls may still consume usage. Do not place slow optional work in a required join and expect the fast branches to return early.

Start from `limits.max_concurrency: 5` unless the host's constraints indicate otherwise. Any positive integer, including values above 5, is supported; 1 serializes the branches. Raise concurrency only for available independent work and provider/host capacity, checking error rate and tail latency at the intended load. It is a per-flow cap, not a cap across seats, rooms or agents. The host must account for overlapping flows, retries and abandoned calls when controlling total load. Custom clients must support concurrent use and honor remaining time.

Parallelism reduces waiting along independent paths, not total model work. With enough capacity a group's completion time is approximately the slowest branch plus overhead; when branches exceed capacity, queueing adds further execution waves. Its downstream synthesis still adds latency. Treat these as planning estimates, not P95 guarantees: correlated stalls, rate limits and retries can dominate. Do not sum overlapping branch times and call that elapsed flow time, or sum component P95s and label it a measured end-to-end P95.

## Accept quality and latency together

Run `jevflow stats trace1.json trace2.json --output metrics.json` on existing traces, including traces produced by `jevflow test`. This offline command reports wall-time and per-node P50/P95/max, failures, observed transport retries and separate mode/configuration groups. Generic metrics live in the plugin; scenario adapters only add their own events and outcome definitions. Missing/abandoned timings are not filled with zero, and parallel node times are not summed to invent a critical-path measurement. This analyzes observed runs; it does not create load or measure host-level deadline misses without host data.

Measure representative inputs at intended load using the actual adapter and prompt sizes. Separate cold initialization, fresh planning, reused plans and exact-rule/forced decisions. Record host end-to-end latency, per-stage and per-node latency, parallel-group wall time and queueing where available, transport attempts, retries, failures, stale results, calls and usage. Gateway SDK time includes network, gateway and model service waiting; process overhead and YAML branching are separate components. Do not label SDK time as isolated model inference or internal Flow overhead as complete decision latency.

Report sample count, P50/P95/max, deadline-miss rate and task outcome quality. Use all submitted requests for completion/miss accounting; keep failed stages, recovery delays and failed decisions visible instead of excluding them to improve percentiles. Averages over many forced actions can hide slow model decisions. For small samples, state that observed tails are not a production bound. Whole-flow recovery and transport retry are different events and should be reported separately.

For real-time use, verify plan invalidation, stale-result rejection and defined deadline behavior, not just fast successful responses. For near-real-time use, exercise slow responses, temporary errors and remaining-budget exhaustion. For complex strategies, verify source-policy coverage and meaningful alternative paths in addition to final scores. Mock tests establish wiring and deadline behavior; live or externally labeled evaluation is needed for strategy quality. Compare single-call and decomposed approaches on matched evidence and representative cases where the task calls for a comparison. Accept a design only when its measured timing and decision quality meet the host's requirements; otherwise revise the structure, budget or host split and report the remaining limitation.
