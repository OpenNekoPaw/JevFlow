# jevflow

[中文](README.md) · [Format reference](skills/jevflow/references/flow-format.md)

A lightweight agent plugin for YAML decision flows. The host owns scenario understanding, data acquisition, business facts, legal candidates, external actions and scheduling. jevflow owns validation, data references, candidate filtering, typed Jev calls, branching, immutable run snapshots and traces.

Python 3.9+ and PyYAML are required. Install with `pip install -e .`. Run `python scripts/jevflow.py validate examples/constrained.yaml`; use `run` with input and mock JSON for an offline execution. Native live calls use `TYPESAFE_API_KEY`; the optional `GatewayClient` uses `AI_GATEWAY_API_KEY` and a host-installed Node SDK (`npm install --prefix "$HOME/.local/share/jevflow/ai-gateway" --save-exact ai@7.0.106`). No game or agent framework is bundled.

Four node types: `filter`, `evaluate`, `branch`, `return`. Dynamic choice criteria can reference input or a prior filter's `.criteria`. Filtering must actually constrain the next judgment. Empty sets require explicit handling; singleton choices bypass the model. `mode: single` compiles to the same execution engine. Independent host requests can run concurrently; YAML branches run sequentially.

`Flow.update()` and `Flow.reload()` validate before replacement; in-flight runs retain their snapshot. Agents edit YAML following [the Skill](skills/jevflow/SKILL.md), inspect traces and compare outcome metrics before publishing revisions. Mocks validate wiring, not model accuracy.

`jevflow visualize flow.yaml --output flow.html` produces an offline read-only inspector. Add `--trace trace.json` to highlight execution and inspect node requests, or `--format mermaid` for Mermaid. Trace-bearing HTML can contain the original request evidence. There is no visual editor or always-on service.

Policies, model instructions and the default diagram UI use Chinese. Field names, IDs and API enums remain English. Use `--locale en` for English UI labels; this does not translate policy text. See the Chinese entry for complete installation and host integration examples.
