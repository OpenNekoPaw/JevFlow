# JevFlow

[简体中文](README.zh-CN.md)

JevFlow is a lightweight runtime for decomposing complex tasks and reasoning into small Jev judgments, connected by YAML flows and decision graphs. Your agent designs the flow and supplies data; JevFlow runs the judgments and branches, then returns structured results and execution traces.

It supports parallel judgments, candidate filtering, conditional routing and visual replay. Use it through the Python API, CLI or MCP server; external actions remain with the host agent.

## Run

From the repository root, with Python 3.9+:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
jevflow run examples/triage.yaml --input examples/input.json --mock examples/mock.json
```

The offline example returns `{"route":"billing"}` in `result`, without calling a model. To run live, configure `TYPESAFE_API_KEY` in your environment and omit `--mock`:

```sh
jevflow run examples/triage.yaml --input examples/input.json
```

### View a flow

The [Dou Dizhu example](examples/ddz/jevflow.yaml) contains 47 nodes covering parallel assessment, role-dependent decisions and candidate selection. It is copied from `ai-h5-game/strategies/ddz/jevflow.yaml`; executing it requires the game host's state and candidate inputs.

```sh
jevflow validate examples/ddz/jevflow.yaml
jevflow visualize examples/ddz/jevflow.yaml --output .tmp/ddz-flow.html
```

Open the generated HTML to inspect the graph. For live monitoring and automatic YAML refresh, use Python 3.10+:

```sh
python -m pip install -e '.[server]'
jevflow serve --web-port 0 --preview-flow examples/ddz/jevflow.yaml
```

Open the URL printed to stderr and select **Configuration preview**. This server also exposes `validate_flow` and `run_flow` through MCP over stdio.

## Results

Reported results from Dou Dizhu matches:

| Matchup | Win-rate ratio (JevFlow : opponent) |
|---|---:|
| JevFlow vs. Jev | **7:3** |
| JevFlow vs. pure LLM (Astra, High) | **6:4** |

| Approach | P50 latency | P95 latency |
|---|---:|---:|
| JevFlow | **3.76 s** | **8.84 s** |
| Pure LLM (Astra, High) | 7.41 s | 11.00 s |

Sample sizes and raw evaluation records are not included with these reported figures.

## More

- [YAML format and node types](skills/jevflow/references/flow-format.md)
- [CLI, MCP, Gateway setup and monitoring](skills/jevflow/references/invocation.md)
- [Agent workflow guide](skills/jevflow/SKILL.md)
- [Reasoning budgets and parallelism](skills/jevflow/references/scenario-budgets.md)
