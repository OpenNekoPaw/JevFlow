# jevflow

Express complex tasks and reasoning decomposed by a host agent as editable, executable, traceable YAML flows and decision graphs. JevFlow runs typed judgments (`choice`, `noul`, `score`); the host owns decomposition, free-text generation and external actions.

## CLI and persistent MCP server

Both modes use the same Python execution service, result contract and traces:

```sh
jevflow run examples/triage.yaml --input examples/input.json --mock examples/mock.json
# Optional MCP dependency requires Python 3.10+; the base CLI remains Python 3.9+.
python3 -m pip install -e '.[server]'
jevflow serve --trace-dir .tmp/jevflow --max-runs 5 --max-concurrency 5
```

The local stdio server exposes only `validate_flow(flow_path)` and `run_flow(flow_path, inputs, ...)`. Supply absolute YAML paths. Each request loads an isolated snapshot; edits affect later requests. Runs and provider calls have separate server-wide caps, both defaulting to 5 and configurable higher. YAML limits still apply. CLI `run --max-concurrency` also defaults to 5.

CLI flags `--provider`, `--model`, `--deadline-unix-ms`, `--attempt-timeout`, `--max-retries` correspond to MCP's snake_case arguments. CLI `--mock` reads a file; MCP `mock` accepts the mapping directly. Provider defaults to native (`TYPESAFE_API_KEY`); gateway uses `AI_GATEWAY_API_KEY` and the existing Node SDK. Attempt timeout defaults to 5 seconds and retries to 0. Native retries are unsupported and nonzero values are rejected; Gateway permits bounded retries, including 5 retries / 6 attempts. Model selection is native-only; Gateway uses `typesafe-ai/jev`.

Results include status, runId, mode, result, error, calls, elapsedMs (including queueing), flowElapsedMs, flowHash, revision and a local trace path. Failures have null result and MCP `isError=true`. Queueing, execution and retries share the deadline. MCP cancellation stops further nodes, queued calls and retries; already-issued synchronous I/O may continue until its transport timeout, retains its provider slot, and cannot publish late success. It may still incur usage. The host must cancel rather than merely abandon a timed-out request.

The bundled `.mcp.json` uses uv with Python 3.12 and `mcp>=1.28,<2`, runs in the plugin root, and stores traces outside the plugin cache at `~/.local/state/jevflow/traces`. First startup may download dependencies. Other MCP hosts can launch their preinstalled Python with `<plugin-root>/scripts/jevflow.py serve`. MCP uses stdio; the bundled launch additionally enables the read-only loopback monitor on an automatically assigned port. Gateway still launches Node per attempt; making Python persistent does not remove that inner cost. See the [invocation reference](skills/jevflow/references/invocation.md) for the full contract.

## Code responsibilities

`schema.py` owns YAML loading, definition/answer validation and snapshot hashes; `core.py` executes nodes.
`Flow` validates on load/update and copies a validated snapshot per run. The public `run_flow` still validates external definitions.
`execution.py` shares queueing, deadlines, clients and trace persistence between CLI and MCP; `control.py` owns errors and cancellation.

`records.py` shares request-state rules and historical trace conversion between the recorder (`replay.py`) and live service (`monitor.py`).
`topology.py` provides graph data; `visualize.py` exports pages using the same `web/` viewer and player as live monitoring.
Both history readers verify snapshot hashes and prefer recorded receipt time, then start time, before source-specific fallbacks. Invalid snapshots are rejected.
New `flowHash` values use a `v2:` prefix and preserve mapping order, which can affect stable filtering and selection batches. Legacy hashes remain readable only with their verified embedded snapshot. Displays and metrics recompute the ordered identity without rewriting saved files; attaching an old trace to external YAML also checks order. Legacy hashes cannot detect order-only edits that already happened to a historical file.
Preview configuration without executing it: `jevflow visualize flow.yaml --output flow.html`. The Web monitor also accepts YAML file selection/drop and watches the file selected by `--preview-flow`.

## Generic execution metrics

Run `python3 scripts/jevflow.py stats traces/*.json --output metrics.json` to analyze existing traces without model calls or exporting input/answer content. It reports flow and node P50/P95/max, failures and observed transport retries, with separate mode/configuration groups. Missing timings are not zero-filled; overlapping parallel durations are not added to fabricate flow wall time. Python APIs are `trace_metrics`, `summarize_traces` and `latency_summary`. Generic execution/client tests and trace metrics belong here; hosts add task outcomes and end-to-end action deadlines. This command analyzes runs, not load generation; `test` can produce mock or live traces.
Cancelled runs, load failures and queue timeouts are included. `latency.total` includes loading and queueing; `latency.flow` includes only runs that entered the executor. Failures without a configuration snapshot use `flowHash: null`, without fabricated flow timings.

Hosts can share an absolute deadline across stages with `flow.run(inputs, GatewayClient(max_retries=1, attempt_timeout=8), trace, deadline_unix_ms=deadline)` (Unix milliseconds). The earlier of this deadline and the YAML timeout applies. The optional attempt cap includes bridge startup; retry backoff consumes the same budget. Only the current request is retried, preserving completed nodes. Re-observation and whole-flow recovery remain host responsibilities.

Failed evaluation traces retain timing, budget, retryability and sanitized transport attempts. Available attempt metrics include SDK time and process overhead; SDK time combines network, gateway and model service waiting, not isolated inference time. Report complete-decision P50/P95/max, failures and retries alongside strategy quality, separating forced actions, reused plans and fresh plans.

[中文](README.md) · [Format reference](skills/jevflow/references/flow-format.md)

A lightweight agent plugin for YAML decision flows. The host owns scenario understanding, data acquisition, business facts, legal candidates, external actions and scheduling. jevflow owns validation, data references, candidate filtering, typed Jev calls, branching, immutable run snapshots and traces.

Python 3.9+ and PyYAML are required. Install with `pip install -e .`. Run `python scripts/jevflow.py validate examples/constrained.yaml`; use `run` with input and mock JSON for an offline execution. Native live calls use `TYPESAFE_API_KEY`; the optional `GatewayClient` uses `AI_GATEWAY_API_KEY` and a host-installed Node SDK (`npm install --prefix "$HOME/.local/share/jevflow/ai-gateway" --save-exact ai@7.0.106`). No game or agent framework is bundled.
The Node bridge ships inside the Python package for both wheel and source installs. The source path `adapters/ai-gateway/evaluate.mjs` remains a compatibility entry point; Node.js and the SDK are still supplied by the host.

Full offline release checks, including MCP, simultaneous servers and the sdist/wheel Gateway smoke test (requires Node.js):

```sh
uv run --no-project --python 3.12 --with 'mcp>=1.28,<2' --with 'PyYAML>=6,<7' \
  --with build --with 'setuptools>=61' --with wheel \
  python -m unittest discover -s tests -v
node --test tests/*.test.cjs
```

Six node types: `filter`, `evaluate`, `parallel`, `select`, `branch`, `return`. Dynamic choice criteria can reference input or a prior filter's `.criteria`. Filtering must actually constrain the next judgment. Empty sets require explicit handling; singleton choices bypass the model. `mode: single` compiles to the same execution engine. Independent host requests can run concurrently.

Use `parallel.branches` for independent judgments with separately scoped `state` and `questions`. All branches read the same pre-fork snapshot and must succeed before `next` runs. Read answers through `nodes.<parallel>.<branch>.<question>.<field>`. Branches contain judgments only, not nested subflows; dependent analysis belongs after the join. See [the runnable game analysis example](examples/parallel.yaml) and the format reference.

`limits.max_concurrency` defaults to 5 per run and accepts higher positive integers (no fixed upper cap). Raise the shared call/step budgets as needed. Branches share the flow's call, step and deadline budgets; each branch and its parent parallel node count as a step. All inputs and sufficient call/step budgets are checked before launching requests. Failure or timeout prevents partial-result publication and cancels unstarted branches. Running synchronous calls cannot be forcibly interrupted: transports receive the remaining timeout, and late results are discarded without changing the trace. Custom clients must be thread-safe and honor timeouts. Host-wide concurrency remains the host's responsibility.

`Flow.update()` and `Flow.reload()` validate before replacement; in-flight runs retain their snapshot. Agents edit YAML following [the Skill](skills/jevflow/SKILL.md), inspect traces and compare outcome metrics before publishing revisions. Mocks validate wiring, not model accuracy.

`jevflow visualize flow.yaml --output flow.html` produces an offline read-only inspector. Add `--trace trace.json` to highlight execution and inspect node requests, or `--format mermaid` for Mermaid. Trace-bearing HTML can contain the original request evidence. There is no visual editor or always-on service.

Policies, model instructions and the default diagram UI use Chinese. Field names, IDs and API enums remain English. Use `--locale en` for English UI labels; this does not translate policy text. See the Chinese entry for complete installation and host integration examples.

For deterministic comparisons, a `filter` can apply `order_by` fields and a positive `limit` after its predicates. Outputs include ordered `.ids` and pre-limit `.matchedCount`; a nonempty singleton can be returned directly without a model call. Trace separates predicate exclusions from ordered truncation. Priorities belong in YAML and express policy, not guaranteed optimality.

The generic `select` node runs batched candidate tournaments after YAML filters. It defaults to batches of 255, shares the run concurrency (default 5), call/step/deadline budgets, and returns a `choice` and the selected pool’s `count`, never fabricated global confidence. Singleton groups skip the model. Optional `fallback_criteria` lists pools to try only when earlier pools are empty; all empty still fails. Malformed data and provider errors never trigger fallback, and every pool must preserve hard constraints. Trace `criteriaIndex` records the pool used. Group order can affect outcomes. Hosts provide domain facts and external actions rather than a second policy interpreter. See the [format reference](skills/jevflow/references/flow-format.md).

## Optional live Web monitor

Run `jevflow serve --web-port 8765 --trace-dir .tmp/jevflow --monitor-history 100`.
Use port `0` for an available port. Open the URL printed to
stderr directly; local access is token-free by default. Add `--web-auth` to require
a generated token and use its complete URL; stdout remains MCP-only. The Web server binds exclusively to 127.0.0.1.
Without this flag, no HTTP server or live snapshots are created.

The read-only monitor shows queued/active/terminal runs, the immutable flow
revision and hash, live node/parallel-branch progress, per-visit evidence and
answers, request queueing, timings and Gateway retries. Selection rounds are
visible in node details and the request table. SSE reconnection uses event IDs;
expired cursors trigger a fresh snapshot. Closing the viewer never cancels work.
Late results cannot modify terminal records.

Completed in-memory history defaults to 100 runs; active runs are retained.
Recent completed traces are loaded on restart, but in-flight tasks are not
resumed. Browser data endpoints require a session token only with `--web-auth`; both modes retain loopback binding and Host/Origin checks. Traces and node
inputs are private local data; do not publish the URL. No external assets or
model calls are needed by the viewer. The Codex plugin's MCP launch configuration
includes `--web-port 0` by default, allocating a free port for each server so multiple sessions can coexist. Open the URL printed by the server executing your flows. Plain CLI `serve` remains stdio-only unless the
flag is supplied. Override the port in the host configuration and restart that
service if needed. The server prints its URL but does not open a browser.

Offline `visualize` exports and live monitoring share a Python graph snapshot contract and the same Web viewer. Dagre (MIT) is bundled locally; no CDN is required. Layout defaults to left-to-right with a top-down toggle. Mermaid also defaults to `LR`; use `--direction TD` for vertical output. Live status updates preserve full-graph coordinates; layout changes only with topology, direction, folding or path filtering.

The configuration-preview tab accepts one selected or dropped YAML file up to 1 MiB. It sends text only to the local server, reuses Python validation/topology, and neither persists nor executes it. Invalid changes retain the last valid diagram with an error. Reselect a browser-picked file after editing; it is not watched automatically.

For automatic refresh, start `jevflow serve --web-port 0 --preview-flow /absolute/path/flow.yaml`. While connected, the page checks that startup-selected file each second, retains the previous valid snapshot during invalid edits or temporary removal, and recovers after repair. Uploading a file switches the preview source; the watch button returns to the server file. HTTP requests cannot change the watched path. Existing executions and history stay bound to their own snapshots. File selection and automatic refresh require the local server; standalone offline HTML does not include these controls.

Single-entry terminal `select → return` chains, optionally preceded by a `filter`, are folded for presentation. Shared returns are never folded, and equal titles never merge execution nodes. Expand groups or inspect each original member and visit. Trace-backed views can filter the executed path. Zoom, drag the canvas, fit the graph or focus the current node. Large graphs start at readable zoom; Fit graph provides an overview.

Python publishes `schemaVersion`, `flowHash`, `revision`, `nodes`, `edges` and `start`. Live graphs come from each run's `trace.flow`, so newer YAML never rewrites historical diagrams. Browser layout, virtual parallel joins and folded groups cannot affect execution. Run `node --test tests/viewer.test.cjs` for layout, cycle, grouping and trace-mapping tests.

### Single-run and multi-run playback

`ExecutionService.run` and MCP `run_flow` accept optional `session_id`, `step_id`, `actor_id`, and `parent_run_id` (CLI: `--session-id`, etc.). Host-supplied correlation IDs are stored in report/trace `context`, separate from YAML, its hash, and model input. Every invocation keeps its own `runId` and immutable flow snapshot.

Use `python -m jevflow replay trace-1.json trace-2.json --output replay.html` to export a standalone multi-run archive. The live monitor and offline archive provide session/actor filters, previous/next run navigation and continuous playback. The playlist is frozen at start and sorted by invocation time, including failures and cancellations. Overlapping calls are not treated as dependencies. Unassociated runs can explicitly be played as a list without inventing a shared session.

Single-run playback supports stepping, seeking, play/pause, 0.5–8× speed and returning to live/final state. Skip long waits is on by default and caps each playback delay at one second; disable it for recorded intervals. Seeking reveals only the evidence observed by that point, without rerunning the flow or changing its trace.

New CLI/MCP runs capture bounded observation patches (`replay.version=1`) for queued requests, parallel progress and terminal outcomes. Legacy traces and plain `Flow.run` traces offer labeled node-completion-order playback. The journal is capped at 10,000 events / 8 MiB of event JSON per run; reaching either limit sets `truncated`, leaves the final trace intact and labels the partial recording in the UI. This is observation playback, not crash recovery or resumable execution. Monitor history is bounded, so export all relevant trace files explicitly for a complete session archive. Host actions and task outcomes remain separate evidence.

### Uncapped model calls

Set `limits.max_calls: null` to disable the model-call count cap. Omitting it retains the default of 8. Serial, parallel and batched selection all honor this setting; calls remain recorded and deadlines, cancellation, concurrency and step limits remain active.
