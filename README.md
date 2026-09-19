# jevflow

供 Agent 或应用嵌入的轻量决策插件。Agent 理解场景并提供 YAML 与输入数据，jevflow 负责数据流转、Jev 判断和分支执行，返回结构化结果与执行记录。

- 纯 Python Jev HTTP 客户端，无 Node.js 或 Agent 框架依赖。
- 三种节点：`evaluate`、`branch`、`return`。
- 一个 `evaluate` 可以批量询问多个独立问题：`choice`、`noul`、`score`。
- YAML 校验、运行快照、动态更新、JSON 日志和离线回归。
- Python API、JSON 输出 CLI，以及通用 Agent Skill。

## 职责边界

| jevflow 提供 | 宿主 Agent / 应用提供 |
|---|---|
| 解析、校验和更新 YAML Flow | 理解场景、拆分需求、编写和优化 Flow |
| `$ref` 数据映射、节点结果传递、条件分支 | 数据获取、业务计算、候选动作生成 |
| Jev 客户端、批量判断、类型检查 | 密钥配置、并发调度、跨请求预算 |
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

宿主可以并发运行多个 Flow；每次执行有独立输入、节点状态和配置快照，各执行应使用独立客户端和 trace。共享 Flow 的配置更新影响后续执行，不改变已启动的执行。若不同请求需要不同候选集，应先构建各自的配置快照，再并发执行，避免“修改共享配置后才排队运行”导致请求绑定到错误版本。

一个 `evaluate` 内的独立问题由 Jev 批量处理；YAML 节点仍按顺序运行，MVP 没有并行分支/汇合节点。线程池、并发数和任务队列由宿主提供。

## Agent 插件接入

[skills/jevflow/SKILL.md](skills/jevflow/SKILL.md) 是通用 Agent Skill，不绑定某个 Agent SDK。

- 支持 Skill 的 Agent：把 `skills/jevflow` 复制或链接到对应的技能目录，并安装本 Python 包。
- 支持命令工具的 Agent：调用 `jevflow validate/run/test`；stdout 是 JSON，退出码 0 成功、1 执行/验证失败（CLI 参数错误为 2）。
- Python Agent：将 `Flow.run`、`Flow.update`、`Flow.reload` 包装为工具。

本地 Codex 的安装示例（在仓库根目录运行，已有同名技能时不要覆盖）：

```sh
mkdir -p ~/.codex/skills
ln -s "$PWD/skills/jevflow" ~/.codex/skills/jevflow
```

仓库同时提供 Codex 插件 manifest；其他 Agent 可以直接使用库、CLI 和 Skill。MVP 不包含常驻 MCP 服务。

## YAML 与优化

完整格式见 [Flow 配置说明](skills/jevflow/references/flow-format.md)。`$ref` 引用输入或已执行节点的答案；分支按顺序匹配第一条条件，未匹配走 `default`。YAML 不执行 Python 或 shell 表达式。

Agent 的最小工作循环：读取 trace → 定位缺失信息/错误问题/分支问题 → 局部改 YAML 并增加 revision → validate → test → 对照真实样本 → 启用新版本。

`test` 默认使用每条案例的 mock，验证流程路由；只有 `--live` 会调用 Jev。改变问题措辞不会改变 mock 答案，因此 mock 通过不证明语义质量提高。`expected` 只用于本地评分，不发送到模型。阈值应在真实数据上评估，confidence 不是正确率。

trace 包含配置快照、内容 hash、输入、逐节点请求/回答、分支条件、token usage（服务返回时）、耗时和最终结果。可通过 `--trace` 或 `test --trace-dir` 指定位置。日志会保存输入正文，应选择适合数据敏感程度的本地目录；不记录 API Key。API 模式由宿主决定如何保存传入的 trace 字典。

## MVP 边界

仅执行决策与返回数据；实际发消息、支付、操作游戏等动作由宿主处理。没有 Web UI、数据库、调度服务或自动优化 Agent。支持顺序、分支、合流和有预算的循环；同节点多问题由 Jev 批量判断，不同节点按顺序执行。候选与问题写在 YAML 中，MVP 不从输入动态生成候选；需要每次变化时，由宿主生成配置后调用 `Flow.update()` 或创建独立的 `Flow`。

默认最多 32 步、8 次模型调用、60 秒流程预算，可在 YAML `limits` 修改。HTTP 超时约束连接/读取等待，执行器在节点边界检查流程总耗时；不是可强杀任意用户函数的硬实时调度器。

## 开发验证

```sh
python3 -m unittest discover -s tests -v
```

测试使用 mock 与本地 HTTP 服务，验证真实 HTTP 编码、鉴权头、类型检查和错误处理；不需要付费密钥。

接口依据：[TypeSafe HTTP API](https://docs.typesafe.ai/api)、[问题类型](https://docs.typesafe.ai/primitives)、[置信度](https://docs.typesafe.ai/confidence)。

## Single 和 Flow

`mode: single` 描述一次 Jev 调用（可包含多个独立问题），自动转换为 `evaluate → return`。`mode: flow` 或省略 mode 时使用完整节点图。两种模式共用校验、执行、trace、调用预算和动态更新，不维护两套执行器。参见 `examples/single.yaml`。

```sh
python3 scripts/jevflow.py run examples/single.yaml --input examples/input.json --mock examples/single-mock.json
```

## Codex 插件

仓库包含 `.codex-plugin/plugin.json` 和 `skills/jevflow`，可作为个人 marketplace 插件安装。安装后的 Skill 通过 `<plugin-root>/scripts/jevflow.py` 调用缓存中自带的 Python 代码；运行需要 Python 3.9+ 和 PyYAML。输入、输出、虚拟环境和场景适配器由宿主保存。插件缓存不保存运行状态。

为避免本地 `.venv`、`.git` 和 trace 被复制进插件缓存，先用 `scripts/export-plugin.py` 导出干净的插件目录，再让个人 marketplace 指向导出目录。导出目录仍应命名为 `jevflow`。
