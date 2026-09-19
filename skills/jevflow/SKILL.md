---
name: jevflow
description: Create, inspect, adjust, and evaluate lightweight Jev decision trees and flows defined in YAML. Use when an agent should decompose a scenario into typed Jev judgments, update routing, or optimize an existing jevflow from execution traces. This skill operates the jevflow Python package and CLI.
---

# jevflow

Use YAML as the editable policy and jevflow as the stable runtime. The host agent owns scenario understanding and improvements; Jev supplies typed judgments during execution.

Keep jevflow a plugin component: the host supplies data, business calculations, candidate generation, tool execution, scheduling and optimization. Put scenario adapters and end-to-end evaluations in the host application, not inside the generic runtime. This skill guides the host agent; it is not an autonomous optimizer running inside jevflow.

## Locate and run

Resolve the directory of this SKILL.md; its grandparent is the plugin root. Run `python3 <plugin-root>/scripts/jevflow.py ...` by absolute path. This uses the bundled runtime even from the Codex plugin cache; it does not require a globally installed jevflow command. Python 3.9+ and PyYAML are required. If PyYAML is missing, create a host-owned virtual environment and install `<plugin-root>` into it, then use that environment’s Python. Do not write runtime data or environments inside the plugin cache.

The installed `jevflow` command or `python -m jevflow` also works in an environment containing the package.

When this skill is symlinked from a source checkout, resolve the real skill directory; its grandparent is the repository root. The checkout's `.venv/bin/jevflow` can be invoked by absolute path from any working directory.

Read [the format reference](references/flow-format.md) before writing or changing nodes. Follow the existing flow's scope and language. The engine supports only `evaluate`, `branch`, and `return`; it does not execute external business actions.

```sh
jevflow validate flow.yaml
jevflow run flow.yaml --input input.json --mock mock.json
jevflow test flow.yaml cases.json
```

Omit `--mock` on `run`, or add `--live` on `test`, to call the actual model. Live calls require `TYPESAFE_API_KEY` and consume API usage. Use the user's existing authorization and budget; do not introduce a new permission step for already authorized work. Never put credentials in YAML, inputs, fixtures or prompts.

## Build the flow

- Choose `mode: single` for one model call (multiple independent questions are allowed). It compiles to `evaluate → return`; use `mode: flow` (the default) for explicit routing or dependent judgments. Both modes share validation, runtime, trace and update semantics. Do not use separate engines.
- Work backward from the result the host needs. Use code-computed input facts for calculations and exact rules. Use Jev only for judgments requiring semantic understanding.
- Give each question one coherent meaning and the relevant evidence. Question IDs are plumbing, not instructions to the model.
- Use `choice` for one of defined options; include an unmatched/insufficient outcome when appropriate. Use `noul` for yes/no probability and `score` for a degree on described levels.
- Group independent questions over the same state into one `evaluate`. They cannot read each other's answers. A dependent question belongs in a later node whose `state` explicitly references prior answers.
- Use `$ref` for data, explicit comparison operators for branches, and `return` for the host-facing result. Keep thresholds in branch configuration. Do not embed executable Python or shell in YAML.
- When candidates change per request, have the host bind the actual candidates into a validated flow snapshot. Handle a sole legal candidate deterministically instead of inventing a second choice. For concurrent requests, execute separate snapshots; do not queue calls that still depend on a mutable shared definition. Host scheduling and node-level parallelism are different: this MVP supports host-concurrent runs and batched Jev questions, but no parallel YAML node branches.

## Adjust and improve

Read the current YAML, representative inputs, and relevant traces before editing. Distinguish missing evidence, a misleading question, missing candidates, wrong routing, model errors and service failures. API failures should not be interpreted as poor model judgment.

Make the smallest change addressing the observed failure. Prefer a local change to evidence, question wording or a branch over rebuilding every node. Preserve stable IDs where practical and increment the quoted `revision`. Keep the old version available for comparison and rollback.

Validate first, then run offline cases covering the changed branch and unaffected routes. Add a case for the observed failure without adding its expected label to the model input. Offline mocks verify wiring only: changes to question wording require live or externally labeled evaluation to substantiate quality improvements. Use held-out cases when tuning thresholds. Compare task outcomes, calls, tokens and latency; do not optimize only for confidence.

For a host process, call `Flow.update(candidate)` or `Flow.reload()` after validation; invalid updates preserve the active definition. Existing runs keep their snapshot. CLI runs reload the file each time. Prefer writing a candidate file and atomically replacing the active YAML after checking it; do not modify the graph halfway through a running request.

Report the changed nodes, why they changed, validation results and whether evidence was mock or live. A passing mock is not evidence of improved Jev accuracy. Keep improvements within the user's requested scenario; do not add infrastructure or new action permissions to optimize a flow.
