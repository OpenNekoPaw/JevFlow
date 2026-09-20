# jevflow

## 通用延迟与执行统计

```sh
python3 scripts/jevflow.py stats traces/*.json --output metrics.json
```

离线分析任意场景的 Flow trace，不调用模型、不导出输入和答案正文。统计整条流程与节点耗时的 P50/P95/最大值、失败、可观测传输重试，并按 mock/live 模式和配置 hash 分组。并行分支的耗时不相加冒充流程耗时；缺失或仍在运行的请求耗时不记为零。

Python 可调用 `trace_metrics(trace)`、`summarize_traces(traces)`、`latency_summary(milliseconds)`。通用执行、客户端测试和 trace 性能统计由本仓库维护；游戏等宿主只补充自己的端到端动作耗时、截止时间、胜负与得分。`stats` 是统计工具，不自动发起负载测试；已有 `test` 命令可生成 mock 或真实模型 trace。

取消、加载失败和排队超时也计入统计。`latency.total` 包含加载及排队耗时，`latency.flow` 只统计实际进入执行器的记录；缺少配置快照的失败记录归入 `flowHash: null`，不会伪造流程耗时或配置版本。

[English](README.en.md) · [配置规范](skills/jevflow/references/flow-format.md)

将宿主 Agent 拆解后的复杂任务与判断逻辑，表达为可编辑、可执行、可追踪的 YAML 流程和决策图。Agent 理解场景并提供 YAML 与输入数据，jevflow 负责数据流转、Jev 判断和分支执行，返回结构化结果与执行记录。判断输出支持 `choice`、`noul`、`score`；任务拆解、自由文本生成和外部动作由宿主完成。

- 纯 Python Jev HTTP 客户端，无 Agent 框架依赖；可选 Gateway 适配器另需 Node.js。
- 六种节点：`filter`、`evaluate`、`parallel`、`select`、`branch`、`return`；离线 HTML / Mermaid 可视化。
- 一个 `evaluate` 可以批量询问多个独立问题：`choice`、`noul`、`score`。
- YAML 校验、运行快照、动态更新、JSON 日志和离线回归。
- Python API、JSON 输出 CLI、本地 stdio MCP Server，以及通用 Agent Skill。

## 职责边界

| jevflow 提供 | 宿主 Agent / 应用提供 |
|---|---|
| 解析、校验和更新 YAML Flow | 理解场景、拆分需求、编写和优化 Flow |
| `$ref` 数据映射、节点结果传递、条件分支 | 数据获取、业务计算、候选动作生成 |
| Jev 客户端、批量及并行判断、类型检查 | 密钥配置、跨请求调度和预算 |
| 单次执行预算、结果和 trace | 外部工具执行、持久化、质量评估与版本发布 |

插件不自行启动上层 Agent、抓取业务数据或执行决策对应的业务动作。Skill 是宿主 Agent 的操作指南；示例和离线测试命令是开发辅助，不是常驻服务。斗地主等具体场景的适配器和评测放在宿主仓库中。

## 快速开始

Python 3.9+，运行时唯一第三方依赖为 PyYAML。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
.venv/bin/jevflow validate examples/triage.yaml
.venv/bin/jevflow run examples/triage.yaml --input examples/input.json --mock examples/mock.json
.venv/bin/jevflow test examples/triage.yaml examples/cases.json
```

离线示例返回 `{"route":"billing"}`；不会访问网络。运行日志默认写入 `.tmp/jevflow/`。

## CLI 与 Server 两种入口

`jevflow run` 执行一次完整 Flow 后退出；`jevflow serve` 保持进程，通过 MCP 提供 `validate_flow` 和 `run_flow`。两者共用执行器、结构化结果和 trace。

```sh
# 单次执行；也支持 --input - 从 stdin 读取 JSON。
jevflow run examples/triage.yaml --input examples/input.json --mock examples/mock.json

# Server 可选依赖需要 Python 3.10+；基础 CLI 无需 MCP SDK。
python3 -m pip install -e '.[server]'
jevflow serve --trace-dir .tmp/jevflow --max-runs 5 --max-concurrency 5
```

Server 默认仅启用本地 stdio，无需端口；可选 `--web-port` 开启只读 Web 监控。每次调用使用 YAML 快照；Agent 修改后，下一次调用读取新版本。服务整体 Flow 数和模型调用并发分别受上限约束，默认均为 5，可调高。排队、执行和重试共享截止时间，取消后不再启动后续节点或返回迟到成功。

### 可选 Web 实时监控

```sh
jevflow serve --trace-dir .tmp/jevflow --web-port 8765 --monitor-history 100
# --web-port 0 自动选择空闲端口。
```

服务在 stderr 输出浏览器链接（本地默认免 token，直接打开即可；显式加 `--web-auth` 时才生成访问 token），stdout 仍只传输 MCP。Web 仅监听 `127.0.0.1`，普通 CLI 不启用时不启动 HTTP 服务、不收集实时快照。Codex 插件默认使用 `--web-port 0`，每个服务自动分配空闲端口，多个会话可同时启动；打开执行任务的那个服务输出的链接。需要固定端口时可在宿主配置中替换该值，并重启对应服务。服务不会自动打开浏览器。

页面显示任务排队、运行、完成、失败和取消；流程图实时高亮节点、并行分支与汇合，并可查看每次节点访问的输入证据、答案和配置。请求表区分全局并发排队和模型调用耗时，Gateway 尝试与重试实时更新。`select` 的逐轮分组在节点详情和请求表查看。每次执行使用自己的 `revision` / `flowHash` 和原始图，不会随着 YAML 热更新而切换旧任务的画面。

浏览器通过 SSE 接收轻量事件，再获取运行快照；断线自动重连，支持事件游标补发，游标超出保留范围时重取快照。关闭页面不取消任务；Web 无启动、编辑或取消动作。取消后仍在途的同步请求显示为停止等待，迟到结果不再修改已结束记录。

默认保留最近 100 次已结束执行及当前活动执行，可用 `--monitor-history` 修改；服务重启时从 trace 目录恢复近期已结束记录，不恢复执行中的任务。完整 trace 仍按原规则落盘。监控包含本地执行证据，始终只监听回环地址并检查 Host / Origin。需要 token 认证时，使用 `--web-auth`。浏览器无外部资源依赖，监控本身不调用模型。

完整参数、结果、取消限制及 Codex 注册见 [CLI / MCP 调用说明](skills/jevflow/references/invocation.md)。

### Web 配置预览与自动刷新

在监控页切换到“配置预览”，选择或拖入一个 YAML 文件（最大 1 MiB）。内容发送至当前本地服务，复用 Python 校验和图结构生成，不写入磁盘、不执行节点、不调用模型。预览标记为“尚未执行”；错误时显示校验信息并保留上一个有效图。浏览器选中的文件不会自动监听磁盘修改，重新选择即可更新。

Agent 编辑配置时，可让服务预览固定文件：

```sh
jevflow serve --web-port 0 --preview-flow /absolute/path/flow.yaml
```

页面连接期间每秒检查该文件；有效修改自动刷新，无效修改或文件暂时缺失时保留上一个有效版本，修复后自动恢复。选择本地文件后可点击“查看服务配置 · 自动刷新”返回此模式。Web 请求不能改写监听路径。运行中与历史回放始终使用各自的配置快照，不受预览更新影响。

离线 `visualize` / `replay` HTML 保持独立可用；YAML 文件选择与自动刷新需要运行中的本地 Web 服务。

真实调用使用 **TypeSafe 原生 API Key**，从进程环境 `TYPESAFE_API_KEY` 读取：

```sh
# 先通过你的密钥管理方式设置 TYPESAFE_API_KEY。
.venv/bin/jevflow run examples/triage.yaml --input examples/input.json
.venv/bin/jevflow test examples/triage.yaml examples/cases.json --live
```

真实请求使用 `https://api.typesafe.ai/v1/systemone`、默认模型 `jev-latest`；可通过 `--model` 指定已可用的固定版本。原生接口的 key 与 Vercel `AI_GATEWAY_API_KEY` 不通用。客户端不自动重试；HTTP 错误、缺失字段、超时使本次执行失败，不会悄悄换成 mock 或其他模型。

## Python 接入

```python
from jevflow import Flow, JevClient

client = JevClient()
flow = Flow.from_file("examples/triage.yaml")
trace = {}
result = flow.run({"message": "I was charged twice."}, client, trace=trace)

# Agent 修改 YAML 后，宿主显式重新加载。
flow.reload()

# 也可直接提交修改后的配置；不写磁盘。
candidate = flow.definition
candidate["revision"] = "2"
candidate["nodes"]["route"]["cases"][0]["right"] = 0.9
flow.update(candidate)
```

`update`/`reload` 先完整校验，再替换内存配置。更新失败时旧配置继续可用；正在运行的任务使用自己的快照，新任务使用更新后的版本。`flow.definition` 返回副本，修改副本不会自动生效。

每次 CLI 执行重新读取 YAML。MVP 不提供文件监听或运行中迁移节点；编辑文件时建议先写候选文件，验证后原子替换，最后调用 `reload()`。

客户端也能独立使用：

```python
response = client.evaluate(
    state={"message": "Please refund this order."},
    questions={"refund": {"type": "noul", "instructions": "Does the message request a refund?"}},
)
print(response["answers"]["refund"]["noul"])
```

客户端是同步的。异步 Agent 可使用 `asyncio.to_thread(flow.run, inputs, client)` 包装；框架工具可以直接封装 `Flow.run`。

宿主可以并发运行多个 Flow；每次执行有独立输入、节点状态和配置快照，各执行应使用独立客户端和 trace。共享 Flow 的配置更新影响后续执行，不改变已启动的执行。动态候选通过输入传入，问题使用 `criteria: {$ref: input.criteria}` 或引用上游筛选结果；无需逐次修改共享配置。

一个 `evaluate` 内的独立问题共享上下文，由 Jev 批量处理；不同上下文的独立分析可放进 `parallel.branches`。分支读取同一局面快照，各自只接收声明的输入，全部成功后汇合，再进入 `next`。相互依赖的判断仍放在后续节点。

```yaml
analyze:
  type: parallel
  branches:
    environment:
      state: {$ref: input.environment}
      questions:
        danger: {type: noul, instructions: 当前环境是否危险？}
    enemies:
      state: {$ref: input.enemies}
      questions:
        threat: {type: noul, instructions: 可见敌人是否构成迫近威胁？}
  next: plan
```

下游使用 `nodes.analyze.environment.danger.noul` 等引用。`limits.max_concurrency` 默认为 5，可配置更高的正整数，也可设为 1 顺序执行这些分支。分支共享本次 Flow 的调用、步骤和时间预算，每个分支占一步；整个并行节点占一步，单一候选分支仍不调用模型。独立 Flow 之间的总并发限制由宿主管理。

当前分支仅包含 `state`、`questions` 和可选 `title`，不嵌套子流程。任何分支失败或超时，整次 Flow 失败，取消未启动分支，不提交部分结果。已经在执行的同步请求无法强杀，会收到剩余时间限制；迟到结果丢弃，trace 不再变化。自定义客户端必须支持并发调用并遵守 `timeout`；内置客户端可用于并行分支。

完整的环境、状态、物品、敌人分析示例：

```sh
python3 scripts/jevflow.py run examples/parallel.yaml --input examples/parallel-input.json --mock examples/parallel-mock.json --trace .tmp/parallel.json
python3 scripts/jevflow.py visualize examples/parallel.yaml --trace .tmp/parallel.json --output .tmp/parallel.html
```

## Agent 插件接入

[skills/jevflow/SKILL.md](skills/jevflow/SKILL.md) 是通用 Agent Skill，不绑定某个 Agent SDK。

- 支持 Skill 的 Agent：把 `skills/jevflow` 复制或链接到对应的技能目录，并安装本 Python 包。
- 支持命令工具的 Agent：调用 `jevflow validate/run/test`；stdout 是 JSON，退出码 0 成功、1 执行/验证失败（CLI 参数错误为 2）。
- 支持 MCP 的 Agent：启动 `jevflow serve`，通过 `validate_flow` 和 `run_flow` 调用完整执行器。
- Python Agent：将 `Flow.run`、`Flow.update`、`Flow.reload` 包装为工具。

本地 Codex 的安装示例（在仓库根目录运行，已有同名技能时不要覆盖）：

```sh
mkdir -p ~/.codex/skills
ln -s "$PWD/skills/jevflow" ~/.codex/skills/jevflow
```

仓库同时提供 Codex 插件 manifest 与 `.mcp.json`；其他 Agent 可以直接使用库、CLI、stdio MCP 和 Skill。

## YAML 与优化

完整格式见 [Flow 配置说明](skills/jevflow/references/flow-format.md)。`$ref` 引用输入或已执行节点的答案；分支按顺序匹配第一条条件，未匹配走 `default`。YAML 不执行 Python 或 shell 表达式。

Agent 的最小工作循环：读取 trace → 定位缺失信息/错误问题/分支问题 → 局部改 YAML 并增加 revision → validate → test → 对照真实样本 → 启用新版本。

`test` 默认使用每条案例的 mock，验证流程路由；只有 `--live` 会调用 Jev。改变问题措辞不会改变 mock 答案，因此 mock 通过不证明语义质量提高。`expected` 只用于本地评分，不发送到模型。阈值应在真实数据上评估，confidence 不是正确率。

trace 包含配置快照、内容 hash、输入、逐节点请求/回答、分支条件、token usage（服务返回时）、耗时和最终结果。可通过 `--trace` 或 `test --trace-dir` 指定位置。日志会保存输入正文，应选择适合数据敏感程度的本地目录；不记录 API Key。API 模式由宿主决定如何保存传入的 trace 字典。

## MVP 边界

仅执行决策与返回数据；实际发消息、支付、操作游戏等动作由宿主处理。提供只读离线图形预览，没有图形编辑器、数据库、调度服务或自动优化 Agent。支持顺序、条件分支、并行判断与汇合，以及有预算的循环；不自动推断任意节点之间的并行依赖。问题和约束写在 YAML 中，候选可从输入动态引用。`filter` 输出 `items`、`criteria`、`count`；空集由显式分支或 `select.fallback_criteria` 处理，唯一候选直接返回 typed answer，不消耗模型调用。

默认最多 32 步、8 次模型调用、5 个并行请求、60 秒流程预算，可在 YAML `limits` 修改。显式设置 `max_calls: null` 可取消模型调用次数上限；省略该字段仍默认 8 次。调用统计、总期限、取消、并发及步骤保护继续生效。进入并行节点时先检查所有分支输入及剩余预算，避免只启动一部分后才发现预算不足；排队时间计入总期限。HTTP 超时约束连接/读取等待，执行器在节点边界及等待并行结果时检查总耗时；不是可强杀任意用户函数的硬实时调度器。

## 代码职责

`schema.py` 负责 YAML 读取、定义与回答校验、流程快照 hash；`core.py` 负责节点执行。
`Flow` 在加载或更新时校验定义，运行时复制已校验快照；直接调用 `run_flow` 仍会校验外部传入的定义。
`execution.py` 统一 CLI/MCP 的排队、期限、客户端和 trace 落盘。`control.py` 提供基础异常和取消检查。

`records.py` 统一请求状态规则及历史 trace 到展示数据的转换。`replay.py` 记录变化，`monitor.py` 发布实时状态及历史记录。
`topology.py` 提供展示拓扑，`visualize.py` 导出页面；两者与实时监控共用 `web/` 下的图形和回放组件。
历史恢复与离线回放都校验配置快照 hash，优先采用记录中的接收时间、其次采用开始时间；无效快照不展示为有效历史。
新 `flowHash` 使用 `v2:` 前缀并保留映射顺序，因为候选顺序可影响稳定排序和分组选择。旧 hash 仅在 trace 包含原始配置且校验通过时兼容读取，展示和统计使用按该快照重算的新指纹，不修改磁盘记录。将旧 trace 附到外部 YAML 时还会比较配置顺序；旧 hash 本身无法检测历史文件曾经发生的纯顺序修改。
只预览配置无需执行：`jevflow visualize flow.yaml --output flow.html`。Web 监控页也支持选择、拖入 YAML，以及 `--preview-flow` 指定文件的自动刷新。

## 开发验证

```sh
python3 -m unittest discover -s tests -v
node --test tests/*.test.cjs
```

测试使用 mock 与本地 HTTP 服务，验证真实 HTTP 编码、鉴权头、类型检查和错误处理；不需要付费密钥。

完整发布检查（包含 MCP、多实例启动和 sdist → wheel 安装包内的 Gateway 验证，需要 Node.js）：

```sh
uv run --no-project --python 3.12 --with 'mcp>=1.28,<2' --with 'PyYAML>=6,<7' \
  --with build --with 'setuptools>=61' --with wheel \
  python -m unittest discover -s tests -v
```

接口依据：[TypeSafe HTTP API](https://docs.typesafe.ai/api)、[问题类型](https://docs.typesafe.ai/primitives)、[置信度](https://docs.typesafe.ai/confidence)。

## Single 和 Flow

`mode: single` 描述一次 Jev 调用（可包含多个独立问题），自动转换为 `evaluate → return`。`mode: flow` 或省略 mode 时使用完整节点图。两种模式共用校验、执行、trace、调用预算和动态更新，不维护两套执行器。参见 `examples/single.yaml`。

```sh
python3 scripts/jevflow.py run examples/single.yaml --input examples/input.json --mock examples/single-mock.json
```

## Codex 插件

仓库包含 `.codex-plugin/plugin.json`、`.mcp.json` 和 `skills/jevflow`，可作为个人 marketplace 插件安装。安装后的 MCP 由 uv 启动自带 Python 代码，trace 存于宿主的 `~/.local/state/jevflow/traces`；Skill 也可通过 `<plugin-root>/scripts/jevflow.py` 使用单次 CLI。输入、输出、虚拟环境和场景适配器由宿主保存。插件缓存不保存运行状态。

为避免本地 `.venv`、`.git` 和 trace 被复制进插件缓存，先用 `scripts/export-plugin.py` 导出干净的插件目录，再让个人 marketplace 指向导出目录。导出目录仍应命名为 `jevflow`。

## 候选约束与可视化

Flow 的收益来自改变证据、允许的选择或分支。不要先问一次战术分类，再把原来的全量候选交给模型重复选择。用 `filter` 按宿主提供的事实筛选候选；下游 `evaluate.questions.*.criteria` 引用 `nodes.<filter>.criteria`。模型响应不得包含被排除的 ID。语义分析也可先用 Jev 判断，再以该答案作为过滤条件，但必须实际影响后续执行。
候选按明确数值策略收敛时，`filter` 支持 `order_by`（按字段逐级比较）与 `limit`；返回 `.ids` 和排序前 `.matchedCount`，可直接输出唯一 ID，无需最后再调用模型选动作。比较条件属于 YAML，执行器不包含领域策略；详见 [格式说明](skills/jevflow/references/flow-format.md#deterministic-narrowing--确定性收敛)。


```sh
python3 scripts/jevflow.py run examples/constrained.yaml --input examples/constrained-input.json --mock examples/constrained-mock.json --trace .tmp/constrained.json
python3 scripts/jevflow.py visualize examples/constrained.yaml --trace .tmp/constrained.json --output .tmp/constrained.html
python3 scripts/jevflow.py visualize examples/constrained.yaml --format mermaid --output .tmp/constrained.mmd
```

HTML 无外部依赖，点击节点查看条件、提示词与执行记录，支持缩放和路径高亮。预览是只读的；Agent 编辑 YAML 后重新生成。附带 trace 的 HTML 会包含节点请求和输入证据，按原始 trace 的权限保存。

## 语言约定

本项目策略、模型提示、示例说明和默认图形界面使用中文；YAML 字段名、节点 ID、API 枚举保持英文。`title` 是显示名，`locale: zh-CN` 控制图形界面，也可用 `--locale en` 生成英文界面，不会自动翻译策略正文。中文和英文文档使用相同接口与版本。

## 可选 AI Gateway 客户端

该客户端与原生 HTTP 客户端属于同一个 jevflow 插件，宿主无需维护第二套 Jev 调用代码。原生客户端仍只依赖 Python；Gateway SDK 安装到宿主目录，不写入插件缓存。
Node 桥接脚本随 Python 包一起发布，普通 wheel 安装和源码安装使用同一实现；源码中的 `adapters/ai-gateway/evaluate.mjs` 保留为兼容入口。Node.js 和下述 SDK 仍需由宿主提供。

```sh
npm install --prefix "$HOME/.local/share/jevflow/ai-gateway" --save-exact ai@7.0.106
```

```python
from jevflow import Flow, GatewayClient
flow = Flow.from_file("flow.yaml")
result = flow.run(inputs, GatewayClient(max_retries=2), trace={})
```

需要进程环境中的 `AI_GATEWAY_API_KEY`。`JEVFLOW_GATEWAY_HOME` 可覆盖 SDK 目录。默认不重试；显式设置 `max_retries` 后，仅对临时上游故障有界重试，所有尝试共用剩余时间并记录 `transportAttempts`。不会切换模型或回退 mock。传输失败与策略错误分别评估。

宿主可传入绝对截止时间：`flow.run(inputs, GatewayClient(max_retries=1, attempt_timeout=8), trace, deadline_unix_ms=deadline)`，其中 `deadline` 为 Unix 毫秒时间戳。执行器取它与 YAML 时间预算的较早值；Gateway 每次尝试最多 8 秒，重试与退避仍消耗原预算。重试仅重做当前请求，已完成节点不会重跑；整条流程失败后是否重新观察和恢复，由宿主决定。SDK、Node 桥接与 Python 子进程共用请求截止时间。

失败节点也保留耗时、预算、重试属性及传输尝试。`transportAttempts` 分别记录总耗时、可用时的 SDK 耗时和进程开销；SDK 耗时包含网络、网关及模型服务等待，不能据此单独判断模型推理时间。比较策略效果时同时报告完整决策耗时 P50/P95/最大值、失败与重试；强制动作、计划复用和新建计划应分开统计。

动态候选分组由通用 `select` 节点执行：先对完整候选集执行 YAML 约束，再按 `batch_size` 分组、并行比较并逐轮汇总。默认批量 255、并发 5，均受流程预算约束；单候选免模型；可用 `fallback_criteria` 显式声明空集合时依次尝试的候选池，全部为空仍失败。该选项不处理模型/API 错误，也不应放宽硬约束。选择节点输出 `choice` 和所用候选池的 `count`，trace 记录 `criteriaIndex`，不伪造全局置信度。具体格式见 [分组选择](skills/jevflow/references/flow-format.md#batched-selection--分组选择)。宿主提供领域事实和外部操作，不再实现策略裁剪或淘汰循环。

离线 `visualize` 和在线监控共用 Python 图快照协议与 Web 查看器，图布局采用本地打包的 Dagre（MIT），无需 CDN。默认从左到右，可切换上下布局；Mermaid 导出也默认 `LR`，用 `--direction TD` 保留纵向展示。完整图的实时状态刷新不重新布局，切换方向、折叠或路径筛选时才重新计算。

查看器默认折叠单入口的 `select → return` 终端链（可包含紧邻的 `filter`），共享返回节点不会折叠。折叠只改变展示，组内原始节点、配置与访问记录均可查看；不会因标题相同而合并执行节点。有 trace 时可筛选本次路径；支持缩放、拖动画布、适应全图与定位当前节点。大图默认保留可读缩放，点击“适应全图”查看总览。

Python 输出的图包含 `schemaVersion`、`flowHash`、`revision`、`nodes`、`edges` 和 `start`，在线图从该次执行的 `trace.flow` 生成；新 YAML 不会覆盖旧执行的图。前端只负责布局和展示，虚拟并行汇合节点与折叠组不是新增执行节点。`node --test tests/viewer.test.cjs` 验证布局、循环、分组与状态映射。

### 单次执行与多次执行回放

`run` / MCP `run_flow` 接受可选的 `session_id`、`step_id`、`actor_id`、`parent_run_id`（CLI 对应 `--session-id` 等）。这些标识由宿主提供，保存到执行报告与 trace 的 `context`；它们不改变 Flow 哈希，也不会自动进入模型输入。一个对局可以包含多个回合，每回合可以调用多个 Flow，每次调用仍有独立的 `runId` 与配置快照。

```sh
python -m jevflow run examples/parallel.yaml \
  --input examples/parallel-input.json --mock examples/parallel-mock.json \
  --session-id match-42 --step-id turn-3 --actor-id seat-0 \
  --trace .tmp/turn-3.json
python -m jevflow replay .tmp/turn-3.json .tmp/turn-4.json --output .tmp/match-replay.html
```

单次回放支持前后步进、播放/暂停、进度拖动、0.5–8 倍速与返回实时/最终状态。默认跳过长等待，将单个事件间隔的播放等待限制为 1 秒；取消该选项按采集时间间隔播放。回退时只显示该时刻已记录的节点、请求和结果。原始 trace 与实际执行不会因回放而改变。

在线会话筛选和离线多 trace HTML 均可连续回放已载入的结束记录，也可筛选参与者、切换上次/下次执行。播放列表在启动时固定，按调用开始时间排序；重叠执行仍是独立记录，不推断依赖关系。每次使用自己的 `flowHash`/revision，热更新前后的执行可以处于同一会话。未提供会话标识的记录可以显式按当前列表播放，但不会自动归为同一对局。

CLI/MCP 的 `ExecutionService` 为新记录保存 `replay.version=1` 状态变化日志，包括模型请求排队、并行节点状态及最终失败/取消。记录是执行器的观测事件，不保证捕捉网络线程每一瞬间的状态。旧 trace 或直接 `Flow.run` 产生的普通 trace，降级为按节点完成顺序回放，界面会明确提示。日志每次执行最多保存 10,000 个事件、8 MiB 的事件 JSON；达到任一上限标记 `truncated`，保留完整最终 trace，界面提示仅能回放已采集片段。此功能不提供进程崩溃后的断点续跑。

监控只保留配置数量的近期结束记录，因此会话列表可能只包含已载入的部分历史。完整复盘可显式将对局的全部 trace 传给 `replay` 导出。回放不重新执行 Flow、不调用模型，也不证明策略质量；实际动作、游戏局面与胜负反馈仍由宿主记录。
