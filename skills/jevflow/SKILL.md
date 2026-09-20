---
name: jevflow
description: Create, inspect, adjust, and evaluate YAML flows with JevFlow. Use for task flows that split complex work into multiple typed Jev processing steps, or strategy networks that decompose complex reasoning into simple, dependent judgments. Improve either structure from execution traces using the jevflow Python package and CLI.
---

# jevflow

JevFlow is a lightweight runtime for task flows and strategy networks built from Jev judgments. Use YAML as the editable definition and jevflow as the stable runtime. The host agent owns decomposition, scenario understanding and improvements; Jev supplies typed judgments during execution.

Keep jevflow a plugin component: the host supplies data, business calculations, candidate generation, tool execution, scheduling and optimization. Put scenario adapters and end-to-end evaluations in the host application, not inside the generic runtime. This skill guides the host agent; it is not an autonomous optimizer running inside jevflow.

## Locate and run

Use either the CLI for one-shot execution or the persistent stdio MCP server. When the plugin's `validate_flow` and `run_flow` tools are available, invoke one complete flow per tool call with an absolute YAML path; do not dispatch individual nodes through the agent. Both interfaces use the same execution service and result contract. Read [CLI and MCP invocation](references/invocation.md) for installation, provider options, deadlines, cancellation and server-wide limits. Server mode needs Python 3.10+ and the optional `server` dependency; the basic CLI remains Python 3.9+ with PyYAML. Do not create a scenario-owned Python bridge or a second protocol.

Resolve the directory of this SKILL.md; its grandparent is the plugin root. Run `python3 <plugin-root>/scripts/jevflow.py ...` by absolute path. This uses the bundled runtime even from the Codex plugin cache; it does not require a globally installed jevflow command. Python 3.9+ and PyYAML are required. If PyYAML is missing, create a host-owned virtual environment and install `<plugin-root>` into it, then use that environment’s Python. Do not write runtime data or environments inside the plugin cache.

The installed `jevflow` command or `python -m jevflow` also works in an environment containing the package.

When this skill is symlinked from a source checkout, resolve the real skill directory; its grandparent is the repository root. The checkout's `.venv/bin/jevflow` can be invoked by absolute path from any working directory.

Read [the format reference](references/flow-format.md) before writing or changing nodes. Follow the existing flow's scope. For this project, write policies, model prompts, human descriptions and node titles in Chinese; retain English field names, node IDs and API enums. Use `locale: zh-CN`; English documentation and UI labels remain available. The engine supports `filter`, `evaluate`, `parallel`, `select`, `branch`, and `return`; it does not execute external business actions.

```sh
jevflow validate flow.yaml
jevflow run flow.yaml --input input.json --mock mock.json
jevflow test flow.yaml cases.json
jevflow stats traces/*.json --output metrics.json
```

Omit `--mock` on `run`, or add `--live` on `test`, to call the actual model. Live calls require `TYPESAFE_API_KEY` and consume API usage. Use the user's existing authorization and budget; do not introduce a new permission step for already authorized work. Never put credentials in YAML, inputs, fixtures or prompts.

Use `stats` on execution trace files for offline latency, per-node timing and retry/failure counts. It makes no model calls and does not export input or answer contents. Generic trace analysis and client tests belong in jevflow; hosts add task-specific outcomes, action deadlines and end-to-end measurements. Python hosts can use `trace_metrics`, `summarize_traces` and `latency_summary` instead of copying aggregation logic. Report mock/live and configuration groups separately; parallel branch durations must not be summed into elapsed flow time.

## Choose the decomposition

- **Task flow (流程图):** split a complex task into bounded Jev processing steps with explicit intermediate outputs and data dependencies. Processing-step nodes are appropriate here. The aim is to reduce the burden and errors of doing all work in one call.
- **Strategy network (策略网络):** split complex reasoning into simple judgments whose answers guide later judgments, constraints or decisions. The host agent designs the reasoning structure so each Jev call handles a narrower question. Model why a choice follows from the evidence; do not make one intermediate node per concrete operation. This is an explicit decision graph, not neural policy training.

Follow the user's requested purpose; a task flow can contain a strategy decision segment. These are design patterns over the same node types, not new YAML modes. Choose the decomposition pattern separately from `mode: single` or `mode: flow`. More steps do not by themselves establish better accuracy or reasoning ability.

## Choose the timing and reasoning budget

Classify response deadlines and reasoning complexity separately before choosing the graph. A complex strategy can also have a real-time execution deadline. Use the host's actual deadline, state freshness requirements and measured tail latency, not a universal seconds-per-scenario rule. These profiles guide design; they are not YAML fields or runtime modes.

| Scenario | Design constraint |
|---|---|
| Real-time / 实时 | Keep remote judgments off a hard real-time control path when their completion cannot be guaranteed. Let the host prepare plans asynchronously, check their validity against current state, and execute a bounded fast path. A single remote call can still miss the deadline. |
| Near-real-time / 准实时 | Fit the necessary dependency path, request queueing, bounded retries and submission reserve into one absolute deadline. Batch shared-evidence questions, parallelize independent evidence, reuse valid plans, and replan on material changes. |
| Complex strategy / 复杂策略 | Budget for the source policy's priorities, exceptions, competing routes and decision-relevant verification. Do not remove essential judgments merely to fit default call limits. If that reasoning cannot fit the execution deadline, split host-managed planning and execution or return an explicit unresolved outcome. |

Read [scenario budgets and parallelism](references/scenario-budgets.md) when designing a time-sensitive or complex flow, changing concurrency/budgets, or reviewing latency failures. It covers deadline allocation, supported parallel semantics, plan validity and joint quality/latency acceptance. Read [the format reference](references/flow-format.md#parallel-judgments--并行判断) for node syntax.

`max_concurrency` defaults to 5 and may be raised, but independent work, provider capacity and measured latency must justify the increase. More concurrency does not shorten a dependency chain or guarantee lower tail latency. Keep total call/step budgets large enough for required branches and joins; keep host-wide concurrency bounded separately. When the user does not want a call-count ceiling, use `limits.max_calls: null`; omission still defaults to 8. Do not impose a two-call design target. Record actual calls and latency, and retain the host deadline.

## Build the flow

- Choose `mode: single` for one model call (multiple independent questions are allowed). It compiles to `evaluate → return`; use `mode: flow` (the default) for explicit routing or dependent judgments. Both modes share validation, runtime, trace and update semantics. Do not use separate engines.
- Work backward from the result the host needs. Use code-computed input facts for calculations and exact rules. Use Jev only for judgments requiring semantic understanding.
- Before choosing YAML nodes, describe each step's input, bounded question, typed output and downstream use. For task flows, derive steps from processing dependencies; for strategy networks, derive judgments and routes from the conditions that determine the decision.
- Give each question one coherent meaning and the relevant evidence. Question IDs are plumbing, not instructions to the model.
- Use `choice` for one of defined options; include an unmatched/insufficient outcome when appropriate. Use `noul` for yes/no probability and `score` for a degree on described levels.
- Group independent questions over the same state into one `evaluate`. For independent judgments needing different evidence, use `parallel.branches`, each with its own `state`, `questions`, and optional `title`. All branches must succeed before `next`. Read joined results as `nodes.<parallel>.<branch>.<question>.<field>`. Branches cannot read siblings or contain nested flows. A dependent question belongs in a later node whose `state` explicitly references prior answers.
- Use `$ref` for data, explicit comparison operators for branches, and `return` for the host-facing result. Keep thresholds in branch configuration. Do not embed executable Python or shell in YAML.
- When candidates change per request, use dynamic `criteria: {$ref: input.criteria}` or a filter output. Singleton choices bypass the model automatically. Empty choices must follow an explicit branch or fail closed. For concurrent requests, execute separate snapshots; do not queue calls that still depend on a mutable shared definition. `limits.max_concurrency` caps in-flow requests (default 5, configurable to higher positive integers); host scheduling governs total concurrency across runs. Parallel branches share the flow deadline and call/step budgets. A failure stops the flow without publishing partial results; unstarted branches are cancelled and late running results discarded. Built-in clients support parallel calls; custom clients must be thread-safe and honor the timeout.

## Task flows

Give each Jev step a distinct, bounded responsibility and pass its structured result to the steps that need it. For example, `识别请求类别 → 按该类别判断材料是否充分 → 返回处理去向` is a valid processing flow when later questions use the earlier answers. Use the supported `choice`, `noul` and `score` outputs; arbitrary text generation, data acquisition and external actions belong to the host.

Avoid repeating the entire original task at every node. Preserve source evidence needed downstream instead of passing only an earlier model conclusion: an incorrect intermediate answer can propagate through the flow. Where material, provide an insufficient-information route or a targeted check against the source evidence. An extra call that merely agrees with the earlier answer is not independent verification.

## Strategy networks

When building from an existing strategy document, extract its priorities, conditions and exceptions into decision paths before mapping them to nodes. Keep the document as the policy source and make the mapping traceable; do not replace it with an invented menu of tactics. Supply each judgment with the relevant strategy excerpts and input evidence.

In a strategy network, an `evaluate` resolves a decision-relevant question; a `branch` routes on its answer or a host-computed fact. Concrete operations belong in candidate data or terminal results for the host, not in a separate intermediate node for each operation. `filter` and `return` are supporting runtime nodes and need not be phrased as questions. A terminal result may name an operation without executing it.

For example, `play_pair → play_single → pass` does not explain a strategy decision. A decision path could ask `对手是否存在近期收尾威胁？`; if yes, judge `候选响应能否阻断该威胁？`, then `阻断代价是否可接受？`; otherwise, judge `候选是否破坏需要保留的手牌组合？`. Use host-computed facts wherever these are exact rule checks. The resulting answers constrain candidate selection, and the graph returns the chosen action or plan. These are illustrative questions, not a mandatory game policy. Cards, tool calls and UI actions remain candidate/result data rather than graph topology.

Review the graph by following contrasting outcomes: can you explain which evidence caused a different next judgment, candidate set or result? A linear chain of generic steps such as “analyze → plan → execute” is insufficient unless those dependencies are explicit. Do not add branches just for appearance: a direct choice can use `mode: single`, and dependent judgments can form a valid linear path. Renaming operation nodes as questions alone does not fix the decision structure.

Choose the output contract to match the host's task. For planning, return the objective, applicable constraints and conditions for reconsidering the plan; for action selection, return a choice among host-supplied legal candidates. If a host separates planning from execution, make the plan affect execution evidence, candidate constraints or routing, and let the host validate current legality and decide when to replan.

The graph supports branches, merges and budgeted cycles. Control follows one path, with independent judgments running concurrently inside an explicit `parallel` node. A merge can read only answers available on every incoming path. Cycles reuse immutable input and the latest completed node answers; they do not obtain new observations or execute actions. Return to the host when progress requires fresh state. Give cycles an explicit exit condition and execution limits; exhausting a budget is an execution failure, not a successful plan.

## Adjust and improve

Read the current YAML, representative inputs, and relevant traces before editing. Distinguish missing evidence, a misleading question, missing candidates, wrong routing, model errors and service failures. API failures should not be interpreted as poor model judgment.

Make the smallest change addressing the observed failure. Prefer a local change to evidence, question wording or a branch over rebuilding every node. Preserve stable IDs where practical and increment the quoted `revision`. Keep the old version available for comparison and rollback.

Validate first, then run offline cases covering the changed branch and unaffected routes. Add a case for the observed failure without adding its expected label to the model input. Offline mocks verify wiring only: changes to question wording require live or externally labeled evaluation to substantiate quality improvements. Use held-out cases when tuning thresholds. Compare task outcomes, calls, tokens and latency; do not optimize only for confidence.

When assessing the benefit of decomposition, compare against a single-call baseline on the same representative inputs with the same model and access to source evidence. For task flows, inspect intermediate correctness and error propagation as well as final accuracy. For strategy networks, inspect whether the simple judgments and their dependencies lead to better final decisions. Report added cost and latency alongside quality; do not claim that decomposition guarantees error reduction or removes the model's reasoning limits.

For a host process, call `Flow.update(candidate)` or `Flow.reload()` after validation; invalid updates preserve the active definition. Existing runs keep their snapshot. CLI runs reload the file each time. Prefer writing a candidate file and atomically replacing the active YAML after checking it; do not modify the graph halfway through a running request.

Report the changed nodes, why they changed, validation results and whether evidence was mock or live. A passing mock is not evidence of improved Jev accuracy. Keep improvements within the user's requested scenario; do not add infrastructure or new action permissions to optimize a flow.

## Configuration preview and live monitoring

The installed Codex plugin starts its MCP server with `--web-port 0`, enabling a local read-only Web monitor on an automatically assigned free port so multiple sessions can coexist. Open that server's URL from stderr directly; local Web access is token-free by default. Add `--web-auth` to require a generated token and use the complete URL in that mode. The service does not automatically open a browser. Inspect the same MCP service used for execution rather than starting a separate monitor with an unrelated run list. Plain CLI `serve` still needs an explicit `--web-port` to enable Web access.

When launching a server explicitly from the skill, use the bundled entry point and server dependencies:

```sh
uv run --no-project --python 3.12 --with 'mcp>=1.28,<2' --with 'PyYAML>=6,<7' \
  python "<plugin-root>/scripts/jevflow.py" serve \
  --trace-dir ~/.local/state/jevflow/traces --web-port 0 \
  --preview-flow "/absolute/project/flow.yaml"
```

`--preview-flow` is optional. Set it to the current project's YAML in a host-specific server launch configuration, then restart that service; do not hardcode project paths in the shared plugin manifest. In the Web configuration-preview tab, select or drop YAML to inspect it without executing nodes or calling a model. The startup-selected file refreshes each second while connected; invalid edits keep the previous valid graph with an error. Browser-selected files require reselection after changes. Existing runs and replays keep their own configuration snapshots. Standalone offline HTML does not provide the file picker or automatic refresh.

Keep one complete Flow per MCP call. Inspect queued requests, running branches, immutable revision snapshots and terminal results. Browser disconnection does not cancel a run.
For repeated calls, pass host-owned session/step/actor IDs as execution metadata. Use the replay controls to inspect a single run or a session playlist, or export multiple traces with `jevflow replay ... --output replay.html`. Replay observes recorded state and never reruns a model. Use the final trace and host outcome for optimization; live progress is not proof
of strategy quality. See the invocation reference for JSON/SSE and optional authentication,
reconnection, bounded history and historical trace loading. Do not share tokens
or evidence-bearing previews publicly.

## Constrain and visualize

Use `filter` for exact predicates over host-computed candidate facts. Its outputs are `items`, `criteria` and `count`; predicates combine with AND. Route on `count` before evaluation when empty results are possible. The next choice must reference the filtered `.criteria`, so excluded candidates cannot reappear. Use the generic `select` node for dynamic candidate grouping and winner reduction after applying constraints globally. The host must not implement another strategy interpreter, tournament loop or singleton shortcut. See the format reference for batch size, budgets and grouping limitations. A preliminary Jev judgment should change subsequent evidence, allowed candidates or routing; avoid adding a classification call followed by an unchanged full-candidate choice.

Distinguish hard rules, provable outcomes and tunable heuristics. Do not describe a filtered candidate as a guaranteed win unless the host evidence proves it. A public uncertainty set is not a probability. Record excluded IDs and failed predicate indices from filter traces, and compare outcome regressions when changing a heuristic.

When Jev is intended only for bounded judgments, do not restore a broad final “choose the best action” call after naming a strategy. Make each answer change a concrete constraint, carry needed evidence explicitly, and execute numeric comparisons without asking the model to calculate. A filter's optional `order_by` and `limit` can resolve remaining candidates deterministically; return `.ids.0` only after establishing a nonempty pool. Keep comparison priorities in YAML, including justified exceptions. Such ordering is a policy heuristic, not proof of superiority. See the format reference for trace and tie semantics.

For a dynamic decision tree, Jev owns the strategic judgments that select the execution path. The agent owns generating and revising the tree; Flow executes it against current evidence. Do not replace goal, sprint, cooperation or cost-benefit judgments with fixed thresholds merely because model answers regress on examples. Exact legality and proven terminal cases may bypass judgments. Subsequent judgments must receive relevant previous answers and the surviving candidate evidence. If `order_by` resolves remaining candidates, its strategic priority must follow the actual judgment, rather than independently overriding it. Test both answers on the same input to demonstrate different constraints, priorities or next questions; skip further judgments once the candidate is unique. Track decision quality and latency separately from successful execution.

When only the candidate pool differs, use `select.fallback_criteria` to declare ordered empty-pool fallbacks instead of duplicating branches, selectors and returns. Preserve hard constraints in every fallback pool. This only handles empty pools, never model/API failures; inspect `criteriaIndex` in the trace and use the selector's `.count` for the actual pool size. Keep separate nodes when their evidence, decision question or policy differs. See the format reference for syntax.

Generate `visualize flow.yaml --output flow.html [--trace trace.json]` for a read-only offline inspector, or use `--format mermaid`. The trace must belong to the exact configuration snapshot; export `trace.flow` if the current YAML changed. Offline and live views share the same snapshot contract and locally bundled Dagre viewer. Both default to left-to-right layout; use `--direction TD` for a vertical export or switch direction in the viewer. Terminal strategy chains fold for display only; expand them to inspect original IDs, conditions and requests. Trace-backed views can show only the executed path. Edit YAML, validate and regenerate; there is no drag-and-drop editor. Preserve privacy of trace-bearing previews just as for traces.

The optional `GatewayClient` belongs to the plugin; the host supplies its Node SDK directory and `AI_GATEWAY_API_KEY`. See the root README for installation. Do not copy a second provider client into a scenario adapter. Keep the native Python client available for `TYPESAFE_API_KEY`.

For host deadlines, use `Flow.run(..., deadline_unix_ms=...)`; Gateway attempt caps and retries are client options, not YAML fields. Select their values from the scenario budget as described in [scenario budgets and parallelism](references/scenario-budgets.md). The earlier game's 8-second attempt cap is an application setting, not a universal real-time default. The runtime bounds work but cannot guarantee a remote response before a hard deadline.
