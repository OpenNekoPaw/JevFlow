# JevFlow

[English](README.md)

JevFlow 是一个轻量执行器，将复杂任务和思考拆解为小粒度的 Jev 判断，通过 YAML 流程和决策图连接起来。Agent 负责设计流程、提供数据，JevFlow 执行判断和分支，返回结构化结果与执行记录。

支持并行判断、候选筛选、条件分支和可视化回放，可通过 Python API、CLI 或 MCP Server 接入；外部动作由宿主 Agent 执行。

## 运行

在仓库根目录执行，需要 Python 3.9+：

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
jevflow run examples/triage.yaml --input examples/input.json --mock examples/mock.json
```

离线示例的 `result` 为 `{"route":"billing"}`，不调用模型。真实运行时，在环境中配置 `TYPESAFE_API_KEY`，去掉 `--mock`：

```sh
jevflow run examples/triage.yaml --input examples/input.json
```

### 查看流程图

[斗地主示例](examples/ddz/jevflow.yaml) 包含 47 个节点，展示并行研判、身份决策和候选选择。原样复制自 `ai-h5-game/strategies/ddz/jevflow.yaml`；实际执行需要游戏宿主提供局面和候选等输入。

```sh
jevflow validate examples/ddz/jevflow.yaml
jevflow visualize examples/ddz/jevflow.yaml --output .tmp/ddz-flow.html
```

打开生成的 HTML 即可查看流程图。实时监控和 YAML 自动刷新需要 Python 3.10+：

```sh
python -m pip install -e '.[server]'
jevflow serve --web-port 0 --preview-flow examples/ddz/jevflow.yaml
```

打开 stderr 输出的链接，切换到“配置预览”。同一服务通过 stdio MCP 提供 `validate_flow` 和 `run_flow` 工具。

## 效果对比

已报告的斗地主对局结果：

| 对局组合 | 胜率比（JevFlow : 对手） |
|---|---:|
| JevFlow 对 Jev | **7:3** |
| JevFlow 对纯 LLM（Astra，高） | **6:4** |

| 方案 | P50 延迟 | P95 延迟 |
|---|---:|---:|
| JevFlow | **3.76 s** | **8.84 s** |
| 纯 LLM（Astra，高） | 7.41 s | 11.00 s |

这组数据尚未附带样本量和原始评测记录。

## 更多

- [YAML 格式与节点类型](skills/jevflow/references/flow-format.md)
- [CLI、MCP、Gateway 安装与监控](skills/jevflow/references/invocation.md)
- [Agent 使用指南](skills/jevflow/SKILL.md)
- [推理预算与并行设计](skills/jevflow/references/scenario-budgets.md)
